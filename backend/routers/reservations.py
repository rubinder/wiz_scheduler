"""Manager reservations API: floor, rules, slots and bookings."""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, get_location_scope, require_manager
from backend.models import Location, User
from backend.reservations import booking
from backend.reservations.availability import find_slots
from backend.reservations.floor import (
    FloorIn, FloorInvalid, FloorOut, TableHasBookings, get_floor, save_floor,
)
from backend.reservations.models import Reservation
from backend.reservations.queries import list_between, table_labels
from backend.reservations.rules import RulesSchema, get_rules, save_rules
from backend.schemas.reservations import (
    BookingIn, ReservationOut, ReservationPatch, RulesOut, SlotOut, StatusIn,
)
from backend.services.archive import active
from backend.services.location_scope import LocationScope


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/reservations", tags=["reservations"],
    dependencies=[Depends(require_reservations_enabled)],
)

_ERROR_STATUS: dict[type[booking.ReservationError], int] = {
    booking.SlotUnavailable: 409,
    booking.HoldExpired: 410,
    booking.PartyTooLarge: 422,
    booking.InvalidPartySize: 422,
    booking.InvalidTransition: 409,
    booking.ReservationNotFound: 404,
    booking.CodeExhausted: 503,
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
    db: AsyncSession, scope: LocationScope, user: User, location_id: str
) -> Location:
    loc = (await db.execute(
        select(Location).where(
            Location.id == location_id, Location.company_id == user.company_id,
            active(Location),
        )
    )).scalar_one_or_none()
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


@router.get("/locations/{location_id}/floor", response_model=FloorOut)
async def read_floor(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorOut:
    await _location(db, scope, user, location_id)
    return await get_floor(db, str(user.company_id), location_id)


@router.put("/locations/{location_id}/floor", response_model=FloorOut)
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


@router.get("/locations/{location_id}/rules", response_model=RulesOut)
async def read_rules(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> RulesOut:
    await _location(db, scope, user, location_id)
    rules, version = await get_rules(db, str(user.company_id), location_id)
    return RulesOut(rules=rules, version=version)


@router.put("/locations/{location_id}/rules", response_model=RulesOut)
async def write_rules(
    location_id: str,
    body: RulesSchema,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> RulesOut:
    await _location(db, scope, user, location_id)
    version = await save_rules(db, str(user.company_id), location_id, body)
    await db.commit()
    return RulesOut(rules=body, version=version)


@router.get("/locations/{location_id}/slots", response_model=list[SlotOut])
async def read_slots(
    location_id: str,
    party_size: int = Query(ge=1),
    day: date = Query(alias="date"),
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[SlotOut]:
    loc = await _location(db, scope, user, location_id)
    start, end = _day_window(loc, day)
    slots = await find_slots(
        db, str(user.company_id), location_id, party_size, start, end,
        apply_cutoff=False,
    )
    return [SlotOut(start=s.start, end=s.end, table_ids=list(s.table_ids))
            for s in slots]


@router.get("/locations/{location_id}/reservations",
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
    return [ReservationOut.of(r, labels) for r, labels in rows]


@router.post("/locations/{location_id}/reservations", status_code=201,
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


@router.patch("/{reservation_id}", response_model=ReservationOut)
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
            apply_cutoff=False)
        await db.commit()
    return await _out(db, res)


@router.post("/{reservation_id}/status", response_model=ReservationOut)
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


@router.post("/{reservation_id}/approve", response_model=ReservationOut)
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


@router.post("/{reservation_id}/decline", response_model=ReservationOut)
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
