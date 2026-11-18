"""Holds and bookings: the write paths that must never double-book.

Every write takes the location row lock (`SELECT ... FROM locations ... FOR
UPDATE`), sweeps that location's lapsed holds to `expired`, and only then
checks availability and writes, so two writers for one location run one after
the other. The exclusion constraint `ex_reservation_tables_no_overlap` is the
backstop; its violation surfaces as `SlotUnavailable`.

Lock order: the location row first, then reservations. A reservation id
passed in is read without a lock only to learn its location (which never
changes); it is re-read after the location lock. New code must not lock
reservation rows before their location, or it can deadlock against this.

`ReservationTable.active` always equals `status in ACTIVE_STATUSES`. Every
held or pending row has `held_until` set (fail closed); it is cleared only
when the row becomes booked.

Functions flush; callers commit. On any `ReservationError` (or other
exception) the caller must roll back: a failed flush leaves the session's
transaction unusable, and a partly applied write must not be committed.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from backend.reservations.availability import Slot, find_slots
from backend.reservations.codes import new_code
from backend.reservations.models import (
    ACTIVE_STATUSES,
    BOOKED,
    CANCELLED,
    COMPLETED,
    DECLINED,
    EXPIRED,
    HELD,
    NO_SHOW,
    PENDING,
    SEATED,
    SOURCE_STAFF,
    Reservation,
    ReservationTable,
)
from backend.reservations.rules import RulesSchema, get_rules

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_OVERLAP_CONSTRAINT = "ex_reservation_tables_no_overlap"
_CODE_INDEX = "uq_reservations_location_code"
_CODE_ATTEMPTS = 5

_TRANSITIONS: dict[str, frozenset[str]] = {
    BOOKED: frozenset({SEATED, NO_SHOW, CANCELLED}),
    SEATED: frozenset({COMPLETED}),
}


class ReservationError(Exception):
    code: str = "reservation_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)


class SlotUnavailable(ReservationError):
    code = "slot_unavailable"


class HoldExpired(ReservationError):
    code = "hold_expired"


class PartyTooLarge(ReservationError):
    code = "party_too_large"


class InvalidTransition(ReservationError):
    code = "invalid_transition"


class ReservationNotFound(ReservationError):
    code = "not_found"


class InvalidPartySize(ReservationError):
    code = "invalid_party_size"


class CodeExhausted(ReservationError):
    """Every attempt at a unique confirmation code collided."""

    code = "code_exhausted"


@dataclass(frozen=True)
class DinerContact:
    name: str
    phone: str
    email: str | None = None

    def __post_init__(self) -> None:
        if not E164.match(self.phone):
            raise ValueError("phone must be E.164, e.g. +12125550100")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


async def _lock_location(db: AsyncSession, company_id: str, location_id: str) -> bool:
    found = (await db.execute(
        select(Location.id)
        .where(Location.id == location_id, Location.company_id == company_id)
        .with_for_update()
    )).scalar_one_or_none()
    return found is not None


async def _sweep_expired(
    db: AsyncSession, company_id: str, location_id: str, now: datetime
) -> None:
    expired_ids = (await db.execute(
        update(Reservation)
        .where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.status.in_((HELD, PENDING)),
            Reservation.held_until <= now,
        )
        .values(status=EXPIRED, updated_at=now)
        .returning(Reservation.id)
    )).scalars().all()
    if expired_ids:
        await db.execute(
            update(ReservationTable)
            .where(
                ReservationTable.company_id == company_id,
                ReservationTable.reservation_id.in_(expired_ids),
            )
            .values(active=False)
        )


async def _begin_write(
    db: AsyncSession, company_id: str, location_id: str, now: datetime
) -> bool:
    locked = await _lock_location(db, company_id, location_id)
    if locked:
        await _sweep_expired(db, company_id, location_id, now)
    return locked


async def _load(
    db: AsyncSession, company_id: str, reservation_id: str,
    location_id: str | None = None,
) -> Reservation:
    query = select(Reservation).where(
        Reservation.id == reservation_id, Reservation.company_id == company_id
    ).execution_options(populate_existing=True)
    if location_id is not None:
        query = query.where(Reservation.location_id == location_id)
    res = (await db.execute(query)).scalar_one_or_none()
    if res is None:
        raise ReservationNotFound()
    return res


async def _load_for_write(
    db: AsyncSession, company_id: str, reservation_id: str, now: datetime
) -> Reservation:
    """Load, lock its location, sweep, then reload so the status is current."""
    res = await _load(db, company_id, reservation_id)
    if not await _begin_write(db, company_id, res.location_id, now):
        raise ReservationNotFound()
    return await _load(db, company_id, reservation_id, res.location_id)


async def _set_tables_active(
    db: AsyncSession, company_id: str, reservation_id: str, active_flag: bool
) -> None:
    await db.execute(
        update(ReservationTable)
        .where(
            ReservationTable.company_id == company_id,
            ReservationTable.reservation_id == reservation_id,
        )
        .values(active=active_flag)
    )


async def _set_status(
    db: AsyncSession, res: Reservation, status: str, now: datetime
) -> None:
    res.status = status
    res.updated_at = now
    if status not in (HELD, PENDING):
        res.held_until = None
    await _set_tables_active(db, res.company_id, res.id, status in ACTIVE_STATUSES)
    await db.flush()


async def _flush_tables(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        if _OVERLAP_CONSTRAINT in str(exc.orig):
            raise SlotUnavailable() from exc
        raise


async def _find_exact_slot(
    db: AsyncSession, company_id: str, location_id: str, party_size: int,
    start: datetime, now: datetime, *, exclude_reservation_id: str | None = None,
    apply_cutoff: bool,
) -> Slot:
    slots = await find_slots(
        db, company_id, location_id, party_size, start, start + timedelta(minutes=1),
        now=now, exclude_reservation_id=exclude_reservation_id,
        apply_cutoff=apply_cutoff,
    )
    target = _utc(start)
    for slot in slots:
        if _utc(slot.start) == target:
            return slot
    raise SlotUnavailable()


def _table_rows(
    company_id: str, reservation_id: str, slot: Slot, rules: RulesSchema,
    party_size: int,
) -> list[ReservationTable]:
    churn = timedelta(minutes=rules.band_for(party_size).churn_minutes)
    during = Range(_utc(slot.start), _utc(slot.end) + churn, bounds="[)")
    return [
        ReservationTable(company_id=company_id, reservation_id=reservation_id,
                         table_id=table_id, during=during, active=True)
        for table_id in slot.table_ids
    ]


async def _assign_code(db: AsyncSession, res: Reservation) -> None:
    """Give `res` a code unique within its location, retrying on collision."""
    await db.flush()  # anything else on res must survive a savepoint rollback
    for _ in range(_CODE_ATTEMPTS):
        code = new_code()
        try:
            async with db.begin_nested():
                res.confirmation_code = code
                await db.flush()
            return
        except IntegrityError as exc:
            if _CODE_INDEX not in str(exc.orig):
                raise
    raise CodeExhausted()


async def hold(
    db: AsyncSession,
    company_id: str,
    location_id: str,
    party_size: int,
    start: datetime,
    *,
    source: str,
    conversation_id: str | None = None,
    now: datetime | None = None,
    apply_cutoff: bool = True,
) -> Reservation:
    """Hold tables for one party at exactly `start`.

    Parties above `max_self_book_party` go to `pending_approval` unless staff
    are booking them.
    """
    now = _now(now)
    if not await _begin_write(db, company_id, location_id, now):
        raise SlotUnavailable()
    if party_size < 1:
        raise InvalidPartySize()
    rules, _ = await get_rules(db, company_id, location_id)
    if party_size > rules.max_party_size:
        raise PartyTooLarge()
    slot = await _find_exact_slot(db, company_id, location_id, party_size, start, now,
                                  apply_cutoff=apply_cutoff)
    pending = party_size > rules.max_self_book_party and source != SOURCE_STAFF
    held_until = now + (
        timedelta(hours=rules.pending_hold_hours) if pending
        else timedelta(minutes=rules.hold_timeout_minutes)
    )
    res = Reservation(
        company_id=company_id, location_id=location_id, party_size=party_size,
        start_at=slot.start, end_at=slot.end, status=PENDING if pending else HELD,
        held_until=held_until, source=source, conversation_id=conversation_id,
        created_at=now, updated_at=now,
    )
    db.add(res)
    await db.flush()
    db.add_all(_table_rows(company_id, res.id, slot, rules, party_size))
    await _flush_tables(db)
    return res


async def confirm(
    db: AsyncSession, company_id: str, reservation_id: str, diner: DinerContact,
    now: datetime | None = None,
) -> Reservation:
    """A live hold becomes booked; a pending row gets the diner and a code."""
    now = _now(now)
    res = await _load_for_write(db, company_id, reservation_id, now)
    if res.status == EXPIRED:
        raise HoldExpired()
    if res.status not in (HELD, PENDING):
        raise InvalidTransition()
    res.diner_name, res.diner_phone, res.diner_email = diner.name, diner.phone, diner.email
    res.updated_at = now
    if res.confirmation_code is None:
        await _assign_code(db, res)
    if res.status == HELD:
        await _set_status(db, res, BOOKED, now)
    await db.flush()
    return res


async def book_staff(
    db: AsyncSession,
    company_id: str,
    location_id: str,
    party_size: int,
    start: datetime,
    diner: DinerContact,
    notes: str | None = None,
    now: datetime | None = None,
) -> Reservation:
    """Staff bookings skip the cutoff and approval."""
    now = _now(now)
    res = await hold(db, company_id, location_id, party_size, start,
                     source=SOURCE_STAFF, now=now, apply_cutoff=False)
    res.notes = notes
    return await confirm(db, company_id, res.id, diner, now=now)


async def approve(
    db: AsyncSession, company_id: str, reservation_id: str, now: datetime | None = None
) -> Reservation:
    now = _now(now)
    res = await _load_for_write(db, company_id, reservation_id, now)
    if res.status == EXPIRED:
        raise HoldExpired()
    if res.status != PENDING:
        raise InvalidTransition()
    await _set_status(db, res, BOOKED, now)
    return res


async def decline(
    db: AsyncSession, company_id: str, reservation_id: str, now: datetime | None = None
) -> Reservation:
    now = _now(now)
    res = await _load_for_write(db, company_id, reservation_id, now)
    if res.status != PENDING:
        raise InvalidTransition()
    await _set_status(db, res, DECLINED, now)
    return res


async def modify(
    db: AsyncSession,
    company_id: str,
    reservation_id: str,
    *,
    party_size: int | None = None,
    start: datetime | None = None,
    notes: str | None = None,
    now: datetime | None = None,
    apply_cutoff: bool = True,
) -> Reservation:
    """Change party, time or notes of a booked or pending reservation.

    `None` means "leave unchanged" for every field, so `notes=None` keeps the
    notes (pass "" to blank them). On `SlotUnavailable`, `PartyTooLarge` or
    `InvalidPartySize` the reservation and its table rows are unchanged: the
    table swap runs in a savepoint and the reservation's fields are only
    assigned after it succeeds. Staff pass
    `apply_cutoff=False`. A diner (`apply_cutoff=True`) cannot grow a booked
    party above `max_self_book_party`: that would skip approval.
    """
    now = _now(now)
    if party_size is not None and party_size < 1:
        raise InvalidPartySize()
    res = await _load_for_write(db, company_id, reservation_id, now)
    if res.status == EXPIRED:
        raise HoldExpired()
    if res.status not in (BOOKED, PENDING):
        raise InvalidTransition()
    new_party = party_size if party_size is not None else res.party_size
    new_start = start if start is not None else res.start_at
    moving = new_party != res.party_size or _utc(new_start) != _utc(res.start_at)
    if moving:
        rules, _ = await get_rules(db, company_id, res.location_id)
        if new_party > rules.max_party_size:
            raise PartyTooLarge()
        if (apply_cutoff and res.status == BOOKED and new_party != res.party_size
                and new_party > rules.max_self_book_party):
            raise PartyTooLarge()
        slot = await _find_exact_slot(
            db, company_id, res.location_id, new_party, new_start, now,
            exclude_reservation_id=res.id, apply_cutoff=apply_cutoff,
        )
        # The savepoint keeps the original table rows if the exclusion
        # constraint (the backstop behind find_slots) rejects the new ones.
        async with db.begin_nested():
            old_rows = (await db.execute(
                select(ReservationTable).where(
                    ReservationTable.company_id == company_id,
                    ReservationTable.reservation_id == res.id,
                )
            )).scalars().all()
            for row in old_rows:
                await db.delete(row)
            await db.flush()
            db.add_all(_table_rows(company_id, res.id, slot, rules, new_party))
            await _flush_tables(db)
        res.party_size, res.start_at, res.end_at = new_party, slot.start, slot.end
    if notes is not None:
        res.notes = notes
    res.updated_at = now
    await db.flush()
    return res


async def cancel(
    db: AsyncSession, company_id: str, reservation_id: str, now: datetime | None = None
) -> Reservation:
    now = _now(now)
    res = await _load_for_write(db, company_id, reservation_id, now)
    if res.status not in (HELD, PENDING, BOOKED):
        raise InvalidTransition()
    await _set_status(db, res, CANCELLED, now)
    return res


async def set_status(
    db: AsyncSession, company_id: str, reservation_id: str, status: str,
    now: datetime | None = None,
) -> Reservation:
    """Floor transitions: booked -> seated | no_show | cancelled, seated -> completed."""
    now = _now(now)
    res = await _load_for_write(db, company_id, reservation_id, now)
    if status not in _TRANSITIONS.get(res.status, frozenset()):
        raise InvalidTransition()
    await _set_status(db, res, status, now)
    return res


async def lookup(
    db: AsyncSession, company_id: str, location_id: str, code: str, phone: str
) -> Reservation | None:
    """Find a reservation by code (any case) and exact phone. Read-only."""
    return (await db.execute(
        select(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            # Codes are stored upper-case, so this stays index-friendly.
            Reservation.confirmation_code == code.strip().upper(),
            Reservation.diner_phone == phone,
        )
    )).scalar_one_or_none()
