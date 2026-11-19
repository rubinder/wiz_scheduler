"""The mock external booking provider (#259 step 9): a stand-in for someone
else's reservation system, so the AI host can be built and evaluated
against an external provider before a real one is integrated.

It keeps its own inventory (`mock_external_bookings`) and never reads or
writes `reservations`, tables or their exclusion constraint. It does not
call `availability.find_slots` either: it is "someone else's system" and
owns its inventory. CLAUDE.md's single-evaluator rule covers WizScheduler's
inventory only. It reuses only the pure pieces of the location's rules: its
service periods and date overrides (`periods_for`, through availability's
candidate grid), the duration for a party size, the booking cutoff and
horizon, and the hold timeout.

Inventory: capacity is covers, not tables. A start is bookable when, at every
15-minute step of the party's dining time, the covers of the held (not
expired) and booked rows overlapping that step plus the party stay within
`max_covers_per_slot`. Writes take a per-location advisory lock around the
capacity check and the write, so two holds cannot both take the last covers.

There is no approval workflow: a party above `max_self_book_party` is
refused with `PartyTooLarge` (the diner is told to call).

Simulated failure modes, from the location's `rules.mock_external`
(`rules.MockExternalSettings`):
- `latency_ms`: every call sleeps that long first (`_sleep`, swappable in
  tests).
- `failure_rate`: find_slots, hold, book, modify and cancel raise `Transient`
  with that probability, BEFORE any write. The draw is reproducible:
  `random.Random(f"{conversation_id}:{seq}:{action}")`, taking the
  (attempt + 1)th value, so the agent's one retry in a turn gets a fresh draw
  and an eval re-run gets the same failures. `lookup` never fails: it is the
  agent's internal re-read of a booking it already holds.
- Idempotency: book, modify and cancel take an `idempotency_key`
  (`{conversation_id}:{action}:{seq}`). The first response is stored in
  `mock_external_idempotency` and a replay returns it unchanged, even if the
  booking changed since, without acting again. A key replayed for another
  booking is refused (`InvalidTransition`).
"""

import asyncio
import random
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.availability import _candidates
from backend.reservations.booking import CodeExhausted, DinerContact, InvalidPartySize
from backend.reservations.codes import new_code
from backend.reservations.mock_external_models import (
    MockExternalBooking,
    MockExternalIdempotency,
)
from backend.reservations.models import EXPIRED
from backend.reservations.providers.base import (
    BOOKED,
    CANCELLED,
    HELD,
    HoldExpired,
    InvalidTransition,
    NotFound,
    PartyTooLarge,
    ProviderBooking,
    ProviderContext,
    ProviderSlot,
    SlotUnavailable,
    Transient,
)
from backend.reservations.rules import RulesSchema

STEP = timedelta(minutes=15)
_CODE_ATTEMPTS = 10
_SLACK = timedelta(days=1)  # longer than any dining duration (max 360 min)

# Swappable in tests, so latency costs no wall time.
_sleep = asyncio.sleep

# Evals only: replaces the conversation id in the failure draw's seed. The
# conversation id is generated, so an eval could not otherwise pick a failure
# rate whose draws are known. `evals.runner.run_scenario` sets it (to the
# scenario id by default) for the length of a run; in production it is None
# and the draw is seeded by the conversation id as documented above.
FAILURE_SEED: ContextVar[str | None] = ContextVar("mock_external_failure_seed", default=None)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _live(row: MockExternalBooking, now: datetime) -> bool:
    """Counts toward capacity: booked, or held and not expired."""
    if row.status == BOOKED:
        return True
    return row.status == HELD and row.held_until is not None \
        and _utc(row.held_until) > _utc(now)


def _status(row: MockExternalBooking, now: datetime) -> str:
    if row.status == HELD and not _live(row, now):
        return EXPIRED
    return row.status


def _booking(row: MockExternalBooking, now: datetime) -> ProviderBooking:
    return ProviderBooking(
        ref=row.id, code=row.code, start=row.start_at, end=row.end_at,
        party_size=row.party_size, status=_status(row, now),
        diner_name=row.diner_name, diner_phone=row.diner_phone, external=True,
        held_until=row.held_until if row.status == HELD else None)


def _dump(b: ProviderBooking) -> dict[str, Any]:
    return {"ref": b.ref, "code": b.code, "start": b.start.isoformat(),
            "end": b.end.isoformat(), "party_size": b.party_size, "status": b.status,
            "diner_name": b.diner_name, "diner_phone": b.diner_phone,
            "external": b.external,
            "held_until": b.held_until.isoformat() if b.held_until else None}


def _load(d: dict[str, Any]) -> ProviderBooking:
    return ProviderBooking(
        ref=d["ref"], code=d["code"], start=datetime.fromisoformat(d["start"]),
        end=datetime.fromisoformat(d["end"]), party_size=d["party_size"],
        status=d["status"], diner_name=d["diner_name"], diner_phone=d["diner_phone"],
        external=d["external"],
        held_until=datetime.fromisoformat(d["held_until"]) if d["held_until"] else None)


def compute_starts(
    rules: RulesSchema, tz_name: str, busy: list[tuple[datetime, datetime, int]],
    party_size: int, window_start: datetime, window_end: datetime, now: datetime,
    max_covers: int,
) -> list[datetime]:
    """Bookable local starts in [window_start, window_end): 15-minute steps
    of the service periods, after the cutoff, within the horizon, with the
    covers at every 15-minute step of the dining time within `max_covers`.
    `busy` is (start, end, party) of the live rows. Pure."""
    zone = ZoneInfo(tz_name)
    dining = timedelta(minutes=rules.duration_for(party_size))
    now_utc = _utc(now)
    earliest = now_utc + timedelta(minutes=rules.cutoff_minutes)
    last_date = now_utc.astimezone(zone).date() + timedelta(days=rules.booking_horizon_days)
    w_start, w_end = _utc(window_start), _utc(window_end)
    grid = rules.model_copy(update={"slot_interval_minutes": 15})
    rows = [(_utc(s), _utc(e), p) for s, e, p in busy]
    out = []
    for local in _candidates(grid, zone, window_start, window_end):
        start = _utc(local)
        if not (w_start <= start < w_end) or start < earliest or local.date() > last_date:
            continue
        t, fits = start, True
        while t < start + dining:
            covers = sum(p for s, e, p in rows if s < t + STEP and e > t)
            if covers + party_size > max_covers:
                fits = False
                break
            t += STEP
        if fits:
            out.append(local)
    return out


NAME = "mock_external"


class MockExternalProvider:
    name = NAME

    # -- the simulated network ----------------------------------------------

    async def _call(self, ctx: ProviderContext, action: str | None) -> None:
        """Latency, then (for `action`) a reproducible injected failure,
        always before any write."""
        knobs = ctx.rules.mock_external
        if knobs.latency_ms:
            await _sleep(knobs.latency_ms / 1000)
        if action is None or knobs.failure_rate <= 0:
            return
        seed = FAILURE_SEED.get() or ctx.conversation_id
        rng = random.Random(f"{seed}:{ctx.seq}:{action}")
        for _ in range(ctx.attempt):
            rng.random()
        if rng.random() < knobs.failure_rate:
            raise Transient()

    # -- inventory -------------------------------------------------------------

    async def _lock(self, db: AsyncSession, location_id: str) -> None:
        await lock_location(db, location_id)

    async def _busy(self, db: AsyncSession, ctx: ProviderContext, window_start: datetime,
                    window_end: datetime, exclude_ref: str | None
                    ) -> list[tuple[datetime, datetime, int]]:
        q = select(MockExternalBooking).where(
            MockExternalBooking.company_id == ctx.company_id,
            MockExternalBooking.location_id == ctx.location_id,
            or_(MockExternalBooking.status == BOOKED,
                (MockExternalBooking.status == HELD)
                & (MockExternalBooking.held_until > ctx.now)),
            MockExternalBooking.start_at < window_end + _SLACK,
            MockExternalBooking.end_at > window_start - _SLACK,
        )
        if exclude_ref is not None:
            q = q.where(MockExternalBooking.id != exclude_ref)
        return [(r.start_at, r.end_at, r.party_size)
                for r in (await db.execute(q)).scalars().all()]

    async def _starts(self, db: AsyncSession, ctx: ProviderContext, party_size: int,
                      window_start: datetime, window_end: datetime,
                      exclude_ref: str | None) -> list[datetime]:
        busy = await self._busy(db, ctx, window_start, window_end, exclude_ref)
        return compute_starts(ctx.rules, ctx.timezone, busy, party_size, window_start,
                              window_end, ctx.now,
                              ctx.rules.mock_external.max_covers_per_slot)

    async def _exact(self, db: AsyncSession, ctx: ProviderContext, party_size: int,
                     start: datetime, exclude_ref: str | None) -> datetime:
        starts = await self._starts(db, ctx, party_size, start,
                                    start + timedelta(minutes=1), exclude_ref)
        if not any(_utc(s) == _utc(start) for s in starts):
            raise SlotUnavailable()
        return _utc(start)

    def _check_party(self, rules: RulesSchema, party_size: int) -> None:
        if party_size < 1:
            raise InvalidPartySize()
        if party_size > rules.max_party_size or party_size > rules.max_self_book_party:
            raise PartyTooLarge()

    async def _row(self, db: AsyncSession, ctx: ProviderContext,
                   ref: str) -> MockExternalBooking:
        row = (await db.execute(
            select(MockExternalBooking).where(
                MockExternalBooking.id == ref,
                MockExternalBooking.company_id == ctx.company_id,
                MockExternalBooking.location_id == ctx.location_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if row is None:
            raise NotFound()
        return row

    async def _replayed(self, db: AsyncSession, ctx: ProviderContext, key: str | None,
                        ref: str) -> ProviderBooking | None:
        if key is None:
            return None
        found = (await db.execute(
            select(MockExternalIdempotency).where(
                MockExternalIdempotency.key == key,
                MockExternalIdempotency.company_id == ctx.company_id)
        )).scalar_one_or_none()
        if found is None:
            return None
        if found.response.get("ref") != ref:
            raise InvalidTransition("idempotency key reused for another booking")
        return _load(found.response)

    async def _done(self, db: AsyncSession, ctx: ProviderContext, key: str | None,
                    row: MockExternalBooking) -> ProviderBooking:
        await db.flush()
        result = _booking(row, ctx.now)
        if key is not None:
            db.add(MockExternalIdempotency(key=key, company_id=ctx.company_id,
                                           response=_dump(result), created_at=ctx.now))
            await db.flush()
        return result

    async def _new_code(self, db: AsyncSession, location_id: str) -> str:
        """A code unused at this location (the caller holds the location lock;
        the partial unique index is the backstop)."""
        for _ in range(_CODE_ATTEMPTS):
            code = new_code()
            taken = (await db.execute(
                select(MockExternalBooking.id).where(
                    MockExternalBooking.location_id == location_id,
                    MockExternalBooking.code == code)
            )).first()
            if taken is None:
                return code
        raise CodeExhausted()

    # -- the protocol -----------------------------------------------------------

    async def find_slots(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        window_start: datetime, window_end: datetime, *,
        exclude_ref: str | None = None,
    ) -> list[ProviderSlot]:
        await self._call(ctx, "find_slots")
        self._check_party(ctx.rules, party_size)
        dining = timedelta(minutes=ctx.rules.duration_for(party_size))
        starts = await self._starts(db, ctx, party_size, window_start, window_end,
                                    exclude_ref)
        return [ProviderSlot(start=s, end=(_utc(s) + dining).astimezone(s.tzinfo),
                             party_size=party_size, ref=None) for s in starts]

    async def hold(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        start: datetime, *, name: str, phone: str, channel: str,
        conversation_id: str,
    ) -> ProviderBooking:
        await self._call(ctx, "hold")
        diner = DinerContact(name=name, phone=phone)  # E.164 or InvalidPhone
        self._check_party(ctx.rules, party_size)
        await self._lock(db, ctx.location_id)
        at = await self._exact(db, ctx, party_size, start, None)
        row = MockExternalBooking(
            company_id=ctx.company_id, location_id=ctx.location_id, start_at=at,
            end_at=at + timedelta(minutes=ctx.rules.duration_for(party_size)),
            party_size=party_size, status=HELD, diner_name=diner.name,
            diner_phone=diner.phone,
            held_until=ctx.now + timedelta(minutes=ctx.rules.hold_timeout_minutes),
            created_at=ctx.now, updated_at=ctx.now)
        db.add(row)
        await db.flush()
        return _booking(row, ctx.now)

    async def book(self, db: AsyncSession, ctx: ProviderContext,
                   ref: str, *, idempotency_key: str | None = None) -> ProviderBooking:
        await self._call(ctx, "book")
        await self._lock(db, ctx.location_id)
        replay = await self._replayed(db, ctx, idempotency_key, ref)
        if replay is not None:
            return replay
        row = await self._row(db, ctx, ref)
        if _status(row, ctx.now) == EXPIRED:
            raise HoldExpired()
        if row.status != HELD:
            raise InvalidTransition()
        row.code = await self._new_code(db, ctx.location_id)
        row.status, row.held_until, row.updated_at = BOOKED, None, ctx.now
        return await self._done(db, ctx, idempotency_key, row)

    async def modify(
        self, db: AsyncSession, ctx: ProviderContext, ref: str, *,
        party_size: int | None, start: datetime | None,
        idempotency_key: str | None = None,
    ) -> ProviderBooking:
        await self._call(ctx, "modify")
        await self._lock(db, ctx.location_id)
        replay = await self._replayed(db, ctx, idempotency_key, ref)
        if replay is not None:
            return replay
        row = await self._row(db, ctx, ref)
        if row.status != BOOKED:
            raise InvalidTransition()
        new_party = party_size if party_size is not None else row.party_size
        new_start = _utc(start) if start is not None else _utc(row.start_at)
        if new_party != row.party_size or new_start != _utc(row.start_at):
            self._check_party(ctx.rules, new_party)
            at = await self._exact(db, ctx, new_party, new_start, row.id)
            row.party_size, row.start_at = new_party, at
            row.end_at = at + timedelta(minutes=ctx.rules.duration_for(new_party))
        row.updated_at = ctx.now
        return await self._done(db, ctx, idempotency_key, row)

    async def cancel(self, db: AsyncSession, ctx: ProviderContext,
                     ref: str, *, idempotency_key: str | None = None) -> ProviderBooking:
        await self._call(ctx, "cancel")
        await self._lock(db, ctx.location_id)
        replay = await self._replayed(db, ctx, idempotency_key, ref)
        if replay is not None:
            return replay
        row = await self._row(db, ctx, ref)
        # An expired hold is already gone, as WizScheduler's expired rows are.
        if _status(row, ctx.now) not in (HELD, BOOKED):
            raise InvalidTransition()
        row.status, row.held_until, row.updated_at = CANCELLED, None, ctx.now
        return await self._done(db, ctx, idempotency_key, row)

    async def lookup(
        self, db: AsyncSession, ctx: ProviderContext, *,
        code: str | None = None, phone: str | None = None, ref: str | None = None,
    ) -> ProviderBooking | None:
        await self._call(ctx, None)
        q = select(MockExternalBooking).where(
            MockExternalBooking.company_id == ctx.company_id,
            MockExternalBooking.location_id == ctx.location_id)
        if ref is not None:
            q = q.where(MockExternalBooking.id == ref)
        elif code is not None and phone is not None:
            q = q.where(MockExternalBooking.code == code.strip().upper(),
                        MockExternalBooking.diner_phone == phone)
        else:
            raise ValueError("lookup needs a ref, or a code and a phone")
        row = (await db.execute(q.execution_options(populate_existing=True))
               ).scalar_one_or_none()
        return _booking(row, ctx.now) if row is not None else None


async def bookings_between(
    db: AsyncSession, company_id: str, location_id: str,
    window_start: datetime, window_end: datetime, now: datetime,
) -> list[ProviderBooking]:
    """The location's live mock bookings starting in [window_start,
    window_end), for the host view: booked, and held until the hold lapses
    (an expired hold is gone, as in WizScheduler's day view it reads as
    expired and frees its covers). Cancelled rows are left out. Read-only."""
    rows = (await db.execute(
        select(MockExternalBooking).where(
            MockExternalBooking.company_id == company_id,
            MockExternalBooking.location_id == location_id,
            MockExternalBooking.start_at >= window_start,
            MockExternalBooking.start_at < window_end,
            or_(MockExternalBooking.status == BOOKED,
                (MockExternalBooking.status == HELD)
                & (MockExternalBooking.held_until > now)),
        ).order_by(MockExternalBooking.start_at, MockExternalBooking.id)
    )).scalars().all()
    return [_booking(r, now) for r in rows]


# -- diner data (reservations/diner_data.py) ---------------------------------


async def lock_location(db: AsyncSession, location_id: str) -> None:
    """The lock every mock write takes, so a diner erase and a hold for the
    same diner cannot interleave."""
    await db.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended("mock_external:" + location_id, 0))))


async def bookings_for_phone(
    db: AsyncSession, company_id: str, location_id: str, phone: str, now: datetime,
) -> list[ProviderBooking]:
    """Every mock booking at the location made with `phone`, all statuses."""
    rows = (await db.execute(
        select(MockExternalBooking).where(
            MockExternalBooking.company_id == company_id,
            MockExternalBooking.location_id == location_id,
            MockExternalBooking.diner_phone == phone,
        ).order_by(MockExternalBooking.start_at, MockExternalBooking.id)
    )).scalars().all()
    return [_booking(r, now) for r in rows]


async def erase_bookings(db: AsyncSession, company_id: str, refs: list[str]) -> int:
    """Delete these mock bookings, and the stored idempotent responses that
    repeat their diner's details. Returns the bookings deleted."""
    if not refs:
        return 0
    await db.execute(
        delete(MockExternalIdempotency).where(
            MockExternalIdempotency.company_id == company_id,
            MockExternalIdempotency.response["ref"].astext.in_(refs))
        .execution_options(synchronize_session=False))
    return (await db.execute(
        delete(MockExternalBooking).where(
            MockExternalBooking.company_id == company_id,
            MockExternalBooking.id.in_(refs))
        .execution_options(synchronize_session=False))).rowcount
