"""Manager reservations API: floor, rules, slots, bookings and the AI host's
conversations."""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.insights import InvalidRange, daily_metrics, insights_range
from backend.config import settings
from backend.dependencies import get_db, get_location_scope, require_manager
from backend.models import Location, User
from backend.reservations import (
    booking, diner_data, number_rules, subscription, voice_numbers, voice_transfers,
)
from backend.reservations.agent.models import OUTCOMES
from backend.reservations.providers import mock_external
from backend.reservations.availability import find_slots
from backend.reservations import floor_ai
from backend.reservations.floor import (
    FloorIn, FloorInvalid, FloorOut, TableHasBookings, get_floor, save_floor,
)
from backend.reservations.models import Reservation
from backend.reservations.queries import (
    conversation_events, get_conversation, list_between, list_conversations,
    list_pending_approvals, table_labels,
)
from backend.reservations.rules import (
    DISCLOSURE_MESSAGE, RulesSchema, get_rules, missing_disclosure, save_rules, stored_fields,
)
from backend.schemas.reservations import (
    BookingIn, ConversationDetailOut, ConversationOut, DinerDataOut,
    DinerCodeEraseOut, DinerCodeIn, DinerEraseOut, DinerPhoneIn, FloorDraftOut, InsightsDayOut, InsightsOut, MarkSpamOut,
    NumberRuleIn, NumberRuleOut, NumberRulePatch,
    ReservationOut, ReservationPatch, ReservationsConfigOut, RulesOut,
    ScreeningStatsOut, SlotOut, StatusIn, VoiceNumberOut, WalkInIn,
)
from backend.services.archive import active
from backend.services.location_scope import LocationScope


logger = logging.getLogger(__name__)


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
        mock_provider_allowed=settings.RESERVATIONS_MOCK_PROVIDER_ALLOWED,
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
    number_rules.DuplicateRule: 409,
    number_rules.InvalidNumber: 422,
    number_rules.ExpiryInPast: 422,
    number_rules.NoCallerNumber: 409,
    number_rules.NumberAllowed: 409,
    diner_data.AmbiguousCode: 409,
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
            detail={"code": e.code, "message": str(e), **getattr(e, "extra", {})},
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


@gated.post("/locations/{location_id}/floor/ai-drafts", status_code=202,
             response_model=FloorDraftOut)
async def create_floor_draft(
    location_id: str,
    background: BackgroundTasks,
    files: list[UploadFile] | None = File(default=None),
    captions: list[str] | None = Form(default=None),
    notes: str = Form(default=""),
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorDraftOut:
    """Draft a floor from photos and/or blueprints (decision 101). Gates
    billing, then runs the model after the response; poll the draft."""
    await _location(db, scope, user, location_id)
    try:
        draft_files = await floor_ai.read_uploads(files or [], captions or [], notes)
        await floor_ai.assert_can_draft(db, str(user.company_id))
        # Counted before any lock is taken: it is a network call.
        input_tokens = await floor_ai.count_input_tokens(draft_files, notes)
        row = await floor_ai.create_draft(
            db, str(user.company_id), location_id, floor_ai.worst_case_usd(input_tokens))
    except floor_ai.DraftInputInvalid as e:
        raise HTTPException(status_code=e.status,
                            detail={"code": e.code, "message": e.message})
    except floor_ai.DraftBlocked as e:
        await db.rollback()
        raise HTTPException(status_code=402,
                            detail={"code": e.code, "message": e.message, **e.extra})
    except floor_ai.DraftEstimateUnavailable:
        raise HTTPException(status_code=503, detail={
            "code": "estimate_unavailable",
            "message": "The draft's cost could not be estimated. Try again shortly."})
    except floor_ai.DraftLimited:
        await db.rollback()
        raise HTTPException(status_code=429, detail={
            "code": "too_many_failed_drafts",
            "message": "Too many AI drafts failed today. Try again tomorrow."})
    except floor_ai.DraftInProgress:
        await db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "draft_in_progress",
            "message": "An AI draft for this location is still running."})
    await db.commit()
    background.add_task(floor_ai.run_draft, row.id, draft_files, notes, input_tokens)
    return FloorDraftOut.of(row)


# Declared before /{draft_id}, which would otherwise match "latest".
@gated.get("/locations/{location_id}/floor/ai-drafts/latest", response_model=FloorDraftOut)
async def read_latest_floor_draft(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorDraftOut:
    """The location's newest draft, so a closed modal or a reload can resume it."""
    await _location(db, scope, user, location_id)
    row = await floor_ai.get_latest_draft(db, str(user.company_id), location_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return FloorDraftOut.of(row)


@gated.get("/locations/{location_id}/floor/ai-drafts/{draft_id}",
           response_model=FloorDraftOut)
async def read_floor_draft(
    location_id: str,
    draft_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> FloorDraftOut:
    await _location(db, scope, user, location_id)
    row = await floor_ai.get_draft(db, str(user.company_id), location_id, draft_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return FloorDraftOut.of(row)


@gated.get("/locations/{location_id}/rules", response_model=RulesOut)
async def read_rules(
    location_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> RulesOut:
    await _location(db, scope, user, location_id)
    rules, version = await get_rules(db, str(user.company_id), location_id)
    rules = await subscription.rules_as_shown(db, str(user.company_id), rules)
    return RulesOut(rules=rules, version=version)


# Rules fields only an admin may change (403 admin_only for a regular
# manager whose save would change them).
ADMIN_ONLY_RULES = ("provider", "mock_external", "disclose_automated")


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
    # Fields a client may omit keep their stored values: the voice knobs (a
    # client that predates them must not reset them) and the admin-only
    # booking provider fields (a regular manager's editor does not send them).
    stored = await stored_fields(db, str(user.company_id), location_id,
                                 ("voice",) + ADMIN_ONLY_RULES)
    kept = {k: v for k, v in stored.items() if k not in body.model_fields_set}
    if kept:
        body = body.model_copy(update=kept)
    if scope.restricted and any(getattr(body, k) != stored[k] for k in ADMIN_ONLY_RULES):
        raise HTTPException(status_code=403, detail={
            "code": "admin_only",
            "message": "Only an admin can change the booking provider "
                       "or the automated-assistant disclosure"})
    if missing_disclosure(body):
        raise HTTPException(status_code=422, detail={
            "code": "greeting_missing_disclosure", "message": DISCLOSURE_MESSAGE})
    if (body.provider == "mock_external" and stored["provider"] != "mock_external"
            and not settings.RESERVATIONS_MOCK_PROVIDER_ALLOWED):
        raise HTTPException(status_code=422, detail={
            "code": "mock_provider_not_allowed",
            "message": "The mock booking provider is not enabled here"})
    try:
        await subscription.check_rules_cap(db, str(user.company_id),
                                           body.daily_spend_cap_usd)
    except subscription.CompedCapLimit as e:
        raise HTTPException(status_code=422, detail={
            "code": "comped_cap_limit", "message": str(e), "ceiling_usd": e.ceiling})
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
    """The day's reservations, merged with the live bookings the AI host made
    in the mock external system here (read-only, `external: true`)."""
    loc = await _location(db, scope, user, location_id)
    start, end = _day_window(loc, day)
    now = datetime.now(timezone.utc)
    rows = await list_between(db, str(user.company_id), location_id, start, end, now)
    out = [ReservationOut.of(r, labels, status) for r, labels, status in rows]
    external = await mock_external.bookings_between(
        db, str(user.company_id), location_id, start, end, now)
    out += [ReservationOut.external_booking(b, location_id, mock_external.NAME)
            for b in external]
    return sorted(out, key=lambda o: (o.start, o.external, o.id))


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
                      for r in found.reservations]
        + [ReservationOut.external_booking(b, location_id, mock_external.NAME)
           for b in found.external_bookings],
        conversations=[ConversationDetailOut.with_events(c, found.events[c.id])
                       for c in found.conversations],
        number_rules=[NumberRuleOut.of(v) for v in found.number_rules],
    )


def _audit_erase(location_id: str, user: User, by: Literal["code", "phone"],
                 counts: dict[str, int]) -> None:
    """One audit line per erase. Never the code or the phone: the line
    must not carry what was erased."""
    logger.info("diner_data.erase location=%s user=%s by=%s %s", location_id, user.id, by,
                " ".join(f"{k}={v}" for k, v in counts.items()))


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
    _audit_erase(location_id, user, "phone", counts)
    return DinerEraseOut(**counts)


@gated.post("/locations/{location_id}/diner-data/erase-by-code",
             response_model=DinerCodeEraseOut)
async def erase_diner_data_by_code(
    location_id: str,
    body: DinerCodeIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> DinerCodeEraseOut:
    """Erase one reservation here by its confirmation code: its contact
    scrubbed, its conversations deleted. The diner's other reservations stay
    (phone erasure removes everything for the number). 404 for a code that
    is not a reservation here."""
    await _location(db, scope, user, location_id, include_archived=True)
    async with _domain_errors(db):
        counts = await diner_data.erase_by_code(
            db, str(user.company_id), location_id, body.code)
        await db.commit()
    _audit_erase(location_id, user, "code", counts)
    return DinerCodeEraseOut(**counts)


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


# ── Number lists (#259 step 7) ──
# A location entry follows LocationScope like every location route. A
# company-wide entry (location_id null) is readable by every manager, since
# it applies at their locations, but only an admin may create, edit or
# delete one (403 admin_only).


def _require_company_wide_admin(scope: LocationScope) -> None:
    if scope.restricted:
        raise HTTPException(status_code=403, detail={
            "code": "admin_only",
            "message": "Only an admin can change entries for all locations"})


async def _rule_for_write(
    db: AsyncSession, scope: LocationScope, user: User, rule_id: str,
):
    rule = await number_rules.get_rule(db, str(user.company_id), rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Number rule not found")
    if rule.location_id is None:
        _require_company_wide_admin(scope)
    else:
        scope.require(rule.location_id, "Number rule")
    return rule


@gated.get("/number-rules", response_model=list[NumberRuleOut])
async def read_number_rules(
    location_id: str | None = None,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[NumberRuleOut]:
    """One location's entries plus the company-wide ones; without
    location_id, every entry in scope plus the company-wide ones."""
    if location_id is not None:
        await _location(db, scope, user, location_id, include_archived=True)
    rows = await number_rules.list_rules(
        db, str(user.company_id), scope, location_id, datetime.now(timezone.utc))
    return [NumberRuleOut.of(v) for v in rows]


@gated.post("/number-rules", status_code=201, response_model=NumberRuleOut)
async def create_number_rule(
    body: NumberRuleIn,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> NumberRuleOut:
    if body.location_id is None:
        _require_company_wide_admin(scope)
    else:
        await _location(db, scope, user, body.location_id)
    now = datetime.now(timezone.utc)
    async with _domain_errors(db):
        rule = await number_rules.create_rule(
            db, str(user.company_id), e164=body.e164.strip(), action=body.action,
            location_id=body.location_id, expires_on=body.expires_on,
            note=body.note, user_id=user.id, now=now)
        out = NumberRuleOut.of(await number_rules.view(db, rule, now))
        await db.commit()
    return out


@gated.patch("/number-rules/{rule_id}", response_model=NumberRuleOut)
async def update_number_rule(
    rule_id: str,
    body: NumberRulePatch,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> NumberRuleOut:
    rule = await _rule_for_write(db, scope, user, rule_id)
    sent = body.model_fields_set
    now = datetime.now(timezone.utc)
    kwargs: dict = {}
    if "expires_on" in sent:
        kwargs["expires_on"] = body.expires_on
    if "note" in sent:
        kwargs["note"] = body.note
    async with _domain_errors(db):
        await number_rules.update_rule(db, rule, action=body.action, now=now, **kwargs)
        out = NumberRuleOut.of(await number_rules.view(db, rule, now))
        await db.commit()
    return out


@gated.delete("/number-rules/{rule_id}", status_code=204)
async def delete_number_rule(
    rule_id: str,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> Response:
    """A hard delete: an entry is not history."""
    rule = await _rule_for_write(db, scope, user, rule_id)
    await number_rules.delete_rule(db, rule)
    await db.commit()
    return Response(status_code=204)


@gated.post("/conversations/{conversation_id}/mark-spam", response_model=MarkSpamOut)
async def mark_conversation_spam(
    conversation_id: str,
    response: Response,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> MarkSpamOut:
    """A permanent block for the caller at the conversation's location: 201
    when created, 200 with the existing block when there is one."""
    conv = await get_conversation(db, str(user.company_id), conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    scope.require(conv.location_id, "Conversation")
    now = datetime.now(timezone.utc)
    async with _domain_errors(db):
        rule, created = await number_rules.mark_spam(db, conv, user.id, now)
        out = MarkSpamOut(**NumberRuleOut.of(await number_rules.view(db, rule, now)).model_dump(),
                          created=created)
        await db.commit()
    response.status_code = 201 if created else 200
    return out


@gated.get("/screening-stats", response_model=ScreeningStatsOut)
async def read_screening_stats(
    location_id: str | None = None,
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> ScreeningStatsOut:
    """Calls screened over the last 7 days, at one location or every
    location in scope."""
    if location_id is not None:
        await _location(db, scope, user, location_id, include_archived=True)
    stats = await number_rules.screening_stats(
        db, str(user.company_id), scope, location_id, datetime.now(timezone.utc))
    return ScreeningStatsOut(days=number_rules.STATS_DAYS, blocked_list=stats.blocked_list,
                             blocked_ftc=stats.blocked_ftc, answered=stats.answered)


@gated.get("/insights", response_model=InsightsOut)
async def read_insights(
    location_id: str,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> InsightsOut:
    """The AI host's daily metrics at one location, `from` to `to` inclusive
    (UTC dates; default the 30 days ending yesterday, at most 180). History,
    so an archived location still answers."""
    await _location(db, scope, user, location_id, include_archived=True)
    try:
        start, end = insights_range(start, end, datetime.now(timezone.utc).date())
    except InvalidRange as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": str(e)})
    rows = await daily_metrics(db, str(user.company_id), location_id, start, end)
    return InsightsOut(location_id=location_id, from_=start, to=end,
                       days=[InsightsDayOut.of(m) for m in rows])


router.include_router(gated)
