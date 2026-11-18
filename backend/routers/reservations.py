"""Manager reservations API: floor, rules, slots, bookings and the AI host's
conversations."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, get_location_scope, require_manager
from backend.models import Location, User
from backend.reservations import booking, diner_data, voice_numbers, voice_transfers
from backend.reservations.agent.models import OUTCOMES
from backend.reservations.availability import find_slots
from backend.reservations.floor import (
    FloorIn, FloorInvalid, FloorOut, TableHasBookings, get_floor, save_floor,
)
from backend.reservations.models import Reservation
from backend.reservations.queries import (
    conversation_events, get_conversation, list_between, list_conversations,
    list_pending_approvals, table_labels,
)
from backend.reservations.rules import RulesSchema, get_rules, save_rules
from backend.schemas.reservations import (
    BookingIn, ConversationDetailOut, ConversationOut, DinerDataOut,
    DinerEraseOut, DinerPhoneIn, ReservationOut, ReservationPatch,
    ReservationsConfigOut, RulesOut, SlotOut, StatusIn, VoiceNumberOut, WalkInIn,
)
from backend.services.archive import active
from backend.services.location_scope import LocationScope


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/reservations", tags=["reservations"])
gated = APIRouter(dependencies=[Depends(require_reservations_enabled)])


@router.get("/config", response_model=ReservationsConfigOut)
async def read_config(_: User = Depends(require_manager)) -> ReservationsConfigOut:
    """Whether reservations are on; the one route that answers when off."""
    return ReservationsConfigOut(
        enabled=settings.RESERVATIONS_ENABLED,
        conversation_outcomes=list(OUTCOMES))

_ERROR_STATUS: dict[type[booking.ReservationError], int] = {
    booking.SlotUnavailable: 409,
    booking.HoldExpired: 410,
    booking.PartyTooLarge: 422,
    booking.InvalidPartySize: 422,
    booking.InvalidTransition: 409,
    booking.ReservationNotFound: 404,
    booking.CodeExhausted: 503,
    booking.InvalidPhone: 422,
    booking.PhoneRequired: 422,
}


@asynccontextmanager
async def _domain_errors(db: AsyncSession) -> AsyncIterator[None]:
    """Roll back and map a domain error to its HTTP status."""
    try:
        yield
    except booking.ReservationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=_ERROR_STATUS.get(type(e), 400),
            detail={"code": e.code, "message": str(e)},
        )


async def _location(
    db: AsyncSession, scope: LocationScope, user: User, location_id: str,
    *, include_archived: bool = False,
) -> Location:
    """`include_archived` is for history (diner data requests): an archived
    location's diners keep their rights."""
    q = select(Location).where(
        Location.id == location_id, Location.company_id == user.company_id)
    if not include_archived:
        q = q.where(active(Location))
    loc = (await db.execute(q)).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    scope.require(location_id)
    return loc


async def _reservation(
    db: AsyncSession, scope: LocationScope, user: User, reservation_id: str
) -> Reservation:
    res = (await db.execute(
        select(Reservation).where(
            Reservation.id == reservation_id,
            Reservation.company_id == user.company_id,
        )
    )).scalar_one_or_none()
    if res is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    scope.require(res.location_id, "Reservation")
    return res


def _day_window(loc: Location, day: date) -> tuple[datetime, datetime]:
    """Local midnight to next local midnight, as aware datetimes."""
    zone = ZoneInfo(loc.timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def _out(db: AsyncSession, res: Reservation) -> ReservationOut:
    labels = await table_labels(db, res.company_id, [res.id])
    return ReservationOut.of(res, labels.get(res.id, []))


@gated.get("/locations/{location_id}/floor", response_model=FloorOut)
async def read_floor(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorOut:
    await _location(db, scope, user, location_id)
    return await get_floor(db, str(user.company_id), location_id)


@gated.put("/locations/{location_id}/floor", response_model=FloorOut)
async def write_floor(
    location_id: str,
    body: FloorIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorOut:
    await _location(db, scope, user, location_id)
    try:
        out = await save_floor(db, str(user.company_id), location_id, body)
        await db.commit()
    except FloorInvalid as e:
        await db.rollback()
        raise HTTPException(
            status_code=422, detail={"code": e.code, "message": e.message})
    except TableHasBookings as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "table_has_bookings", "message": str(e),
            "table_ids": e.table_ids})
    return out


@gated.get("/locations/{location_id}/rules", response_model=RulesOut)
async def read_rules(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> RulesOut:
    await _location(db, scope, user, location_id)
    rules, version = await get_rules(db, str(user.company_id), location_id)
    return RulesOut(rules=rules, version=version)


@gated.put("/locations/{location_id}/rules", response_model=RulesOut)
async def write_rules(
    location_id: str,
    body: RulesSchema,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> RulesOut:
    await _location(db, scope, user, location_id)
    if body.staff_transfer_number:
        problem = await voice_transfers.number_problem(db, body.staff_transfer_number)
        if problem:
            raise HTTPException(status_code=422, detail={
                "code": "transfer_number_not_allowed", "message": problem})
    version = await save_rules(db, str(user.company_id), location_id, body)
    await db.commit()
    return RulesOut(rules=body, version=version)


@gated.get("/locations/{location_id}/slots", response_model=list[SlotOut])
async def read_slots(
    location_id: str,
    party_size: int = Query(ge=1),
    day: date = Query(alias="date"),
    apply_cutoff: bool = False,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[SlotOut]:
    """Staff view by default; `apply_cutoff=true` previews what diners see."""
    loc = await _location(db, scope, user, location_id)
    start, end = _day_window(loc, day)
    slots = await find_slots(
        db, str(user.company_id), location_id, party_size, start, end,
        apply_cutoff=apply_cutoff,
    )
    return [SlotOut(start=s.start, end=s.end, table_ids=list(s.table_ids))
            for s in slots]


@gated.get("/locations/{location_id}/reservations",
            response_model=list[ReservationOut])
async def read_reservations(
    location_id: str,
    day: date = Query(alias="date"),
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[ReservationOut]:
    loc = await _location(db, scope, user, location_id)
    start, end = _day_window(loc, day)
    rows = await list_between(db, str(user.company_id), location_id, start, end)
    return [ReservationOut.of(r, labels, status) for r, labels, status in rows]


@gated.get("/locations/{location_id}/approvals",
            response_model=list[ReservationOut])
async def read_approvals(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[ReservationOut]:
    """Live pending requests on any day, so none lapses unseen."""
    await _location(db, scope, user, location_id)
    rows = await list_pending_approvals(db, str(user.company_id), location_id)
    return [ReservationOut.of(r, labels, status) for r, labels, status in rows]


@gated.post("/locations/{location_id}/reservations", status_code=201,
             response_model=ReservationOut)
async def create_reservation(
    location_id: str,
    body: BookingIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _location(db, scope, user, location_id)
    try:
        diner = booking.DinerContact(
            name=body.diner.name, phone=body.diner.phone, email=body.diner.email)
    except ValueError as e:
        # Raised before any write, so there is nothing to roll back.
        raise HTTPException(status_code=422, detail={
            "code": "invalid_phone", "message": str(e)})
    async with _domain_errors(db):
        res = await booking.book_staff(
            db, str(user.company_id), location_id, body.party_size, body.start,
            diner, notes=body.notes)
        await db.commit()
    return await _out(db, res)


@gated.post("/locations/{location_id}/walk-ins", status_code=201,
             response_model=ReservationOut)
async def seat_walk_in(
    location_id: str,
    body: WalkInIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _location(db, scope, user, location_id)
    async with _domain_errors(db):
        res = await booking.seat_walk_in(
            db, str(user.company_id), location_id, body.party_size,
            diner_name=body.name, diner_phone=body.phone, notes=body.notes)
        await db.commit()
    return await _out(db, res)


@gated.patch("/{reservation_id}", response_model=ReservationOut)
async def update_reservation(
    reservation_id: str,
    body: ReservationPatch,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _reservation(db, scope, user, reservation_id)
    async with _domain_errors(db):
        res = await booking.modify(
            db, str(user.company_id), reservation_id,
            party_size=body.party_size, start=body.start, notes=body.notes,
            apply_cutoff=False, by_staff=True)
        await db.commit()
    return await _out(db, res)


@gated.post("/{reservation_id}/status", response_model=ReservationOut)
async def change_status(
    reservation_id: str,
    body: StatusIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _reservation(db, scope, user, reservation_id)
    async with _domain_errors(db):
        if body.status == "cancelled":
            res = await booking.cancel(db, str(user.company_id), reservation_id)
        else:
            res = await booking.set_status(
                db, str(user.company_id), reservation_id, body.status)
        await db.commit()
    return await _out(db, res)


@gated.post("/{reservation_id}/approve", response_model=ReservationOut)
async def approve_reservation(
    reservation_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _reservation(db, scope, user, reservation_id)
    async with _domain_errors(db):
        res = await booking.approve(db, str(user.company_id), reservation_id)
        await db.commit()
    return await _out(db, res)


@gated.post("/{reservation_id}/decline", response_model=ReservationOut)
async def decline_reservation(
    reservation_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ReservationOut:
    await _reservation(db, scope, user, reservation_id)
    async with _domain_errors(db):
        res = await booking.decline(db, str(user.company_id), reservation_id)
        await db.commit()
    return await _out(db, res)


Outcome = Literal[OUTCOMES]  # type: ignore[valid-type]


@gated.get("/locations/{location_id}/conversations",
            response_model=list[ConversationOut])
async def read_conversations(
    location_id: str,
    day: date = Query(alias="date"),
    outcome: Outcome | None = None,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[ConversationOut]:
    """The AI host's conversations started on `date` (the location's local
    day), newest first."""
    loc = await _location(db, scope, user, location_id)
    start, end = _day_window(loc, day)
    rows = await list_conversations(
        db, str(user.company_id), location_id, start, end, outcome)
    return [ConversationOut.of(c) for c in rows]


@gated.get("/conversations/{conversation_id}",
            response_model=ConversationDetailOut)
async def read_conversation(
    conversation_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ConversationDetailOut:
    conv = await get_conversation(db, str(user.company_id), conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    scope.require(conv.location_id, "Conversation")
    events = await conversation_events(db, str(user.company_id), [conv.id])
    return ConversationDetailOut.with_events(conv, events[conv.id])



@gated.post("/locations/{location_id}/diner-data", response_model=DinerDataOut)
async def read_diner_data(
    location_id: str,
    body: DinerPhoneIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> DinerDataOut:
    """One diner's reservations and conversations here, for an access request.
    POST so the phone number travels in the body, not the URL (access logs)."""
    await _location(db, scope, user, location_id, include_archived=True)
    async with _domain_errors(db):
        found = await diner_data.find_diner_data(
            db, str(user.company_id), location_id, body.phone)
    labels = await table_labels(
        db, str(user.company_id), [r.id for r in found.reservations])
    return DinerDataOut(
        reservations=[ReservationOut.of(r, labels.get(r.id, []))
                      for r in found.reservations],
        conversations=[ConversationDetailOut.with_events(c, found.events[c.id])
                       for c in found.conversations],
    )


@gated.post("/locations/{location_id}/diner-data/erase",
             response_model=DinerEraseOut)
async def erase_diner_data(
    location_id: str,
    body: DinerPhoneIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> DinerEraseOut:
    """Erase one diner's data here: contact scrubbed, conversations deleted."""
    await _location(db, scope, user, location_id, include_archived=True)
    async with _domain_errors(db):
        counts = await diner_data.erase_diner_data(
            db, str(user.company_id), location_id, body.phone)
        await db.commit()
    return DinerEraseOut(**counts)


@gated.get("/locations/{location_id}/voice", response_model=VoiceNumberOut)
async def read_voice_number(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> VoiceNumberOut:
    """The location's AI host phone number and where it stands (archived
    locations too: their number is on hold or released)."""
    await _location(db, scope, user, location_id, include_archived=True)
    row = await voice_numbers.for_location(db, str(user.company_id), location_id)
    if row is None:
        return VoiceNumberOut(e164=None, status="none")
    return VoiceNumberOut(e164=row.e164, status=row.status, release_after=row.release_after)


router.include_router(gated)
