"""The host agent's tools (#259): Anthropic tool definitions and the executor
that maps a tool call onto the conversation's booking provider
(`providers.provider_for`), never onto `booking` or `availability` directly.

Guards, so the model cannot state or book what the evaluator never produced:

- `hold_slot` and a time change in `modify_booking` only accept a start that
  an earlier `find_slots` call in this conversation offered, for that party
  size (`conv.offered_starts`, compared as instants).
- `modify_booking` and `cancel_booking` act only on the reservation that
  `lookup_booking` verified by code and phone. Neither writes the booking:
  `modify_booking` validates the change and stages it on
  `conv.pending_modify`, `cancel_booking` stages `conv.pending_cancel_id`,
  and each asks for the structured confirmation (`expect`) that the core's
  `confirm_modify` / `confirm_cancel` action applies. Staging one clears the
  other, so only the request the diner was last shown can be confirmed.

Each tool runs in a savepoint, so a failed write leaves the session usable
and the conversation unchanged. `execute_tool` never raises. Flush only;
callers commit.
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location
from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation, ReservationLookupFailure
from backend.reservations.providers import (
    BOOKED,
    HELD,
    PENDING,
    BookingProvider,
    InvalidTransition,
    PartyTooLarge,
    PhoneRequired,
    ProviderBooking,
    ProviderContext,
    ProviderError,
    ProviderSlot,
    SlotUnavailable,
    provider_for,
)
from backend.reservations.rules import RulesSchema, get_rules
from backend.services.archive import active

logger = logging.getLogger(__name__)

MAX_OPTIONS = 6
MAX_OFFERED = 30  # most recent offers kept on the conversation
MAX_FAILED_LOOKUPS = 3
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

EXPECT_CONFIRM_HOLD = "confirm_hold"
EXPECT_CONFIRM_CANCEL = "confirm_cancel"
EXPECT_CONFIRM_MODIFY = "confirm_modify"
EXPECT_HANDOFF = "handoff"


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "find_slots",
        "Find bookable start times for a party on one date, in the restaurant's "
        "local time. Returns up to 6 options spread across the window. The only "
        "source of availability: never state a time that this tool did not return.",
        {
            "party_size": {"type": "integer", "description": "Number of guests."},
            "date": {"type": "string", "description": "Local date, YYYY-MM-DD."},
            "earliest": {"type": ["string", "null"],
                         "description": "Earliest local start, HH:MM, or null."},
            "latest": {"type": ["string", "null"],
                       "description": "Latest local start, HH:MM, or null."},
        },
    ),
    _tool(
        "hold_slot",
        "Hold one offered time for the diner. start_iso must be an option that "
        "find_slots returned for this party size. The diner then confirms the hold.",
        {
            "start_iso": {"type": "string",
                          "description": "start_iso of an option from find_slots."},
            "party_size": {"type": "integer"},
            "name": {"type": "string", "description": "Diner's name."},
            "phone": {"type": "string",
                      "description": "Diner's phone in E.164, e.g. +12125550100."},
        },
    ),
    _tool(
        "lookup_booking",
        "Find the diner's existing booking by confirmation code and the phone "
        "number it was made with. Required before modifying or cancelling.",
        {
            "confirmation_code": {"type": "string"},
            "phone": {"type": "string", "description": "E.164 phone."},
        },
    ),
    _tool(
        "modify_booking",
        "Propose a change to the party size and/or time of the booking found by "
        "lookup_booking. A new start_iso must be an option find_slots returned for "
        "the new party size. Null leaves a field unchanged. Nothing changes until "
        "the diner presses Confirm.",
        {
            "party_size": {"type": ["integer", "null"]},
            "start_iso": {"type": ["string", "null"]},
        },
    ),
    _tool(
        "cancel_booking",
        "Ask the diner to confirm cancelling the booking found by lookup_booking. "
        "Nothing is cancelled until the diner confirms.",
        {},
    ),
    _tool(
        "handoff_to_staff",
        "Hand the conversation to restaurant staff when you cannot help.",
        {"reason": {"type": "string"}},
    ),
    _tool(
        "restaurant_info",
        "Facts about the restaurant and today's service periods.",
        {},
    ),
]


@dataclass(frozen=True)
class ToolOutcome:
    result: dict[str, Any]
    is_error: bool = False
    expect: str | None = None


class _ToolError(Exception):
    def __init__(self, code: str, *, handoff: bool = False) -> None:
        super().__init__(code)
        self.handoff = handoff
        self.code = code


def _instant(start_iso: Any) -> datetime:
    """Parse an offset-carrying ISO datetime to UTC; naive or junk is invalid."""
    if not isinstance(start_iso, str):
        raise _ToolError("invalid_input")
    try:
        dt = datetime.fromisoformat(start_iso)
    except ValueError as exc:
        raise _ToolError("invalid_input") from exc
    if dt.tzinfo is None:
        raise _ToolError("invalid_input")
    return dt.astimezone(timezone.utc)


def _party(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _ToolError("invalid_input")
    if value < 1:
        raise _ToolError("invalid_party_size")
    return value


def _hhmm(value: Any) -> time | None:
    """Exactly "HH:MM" (24h) or None; seconds, offsets and junk are refused."""
    if value is None:
        return None
    if not isinstance(value, str) or not _HHMM.match(value):
        raise _ToolError("invalid_time")
    return time(int(value[:2]), int(value[3:]))


def _was_offered(conv: AgentConversation, start: datetime, party_size: int) -> bool:
    for entry in conv.offered_starts or []:
        if not isinstance(entry, dict) or entry.get("party_size") != party_size:
            continue
        try:
            if _instant(entry.get("start_iso")) == start:
                return True
        except _ToolError:
            continue
    return False


async def _location(db: AsyncSession, conv: AgentConversation) -> Location:
    loc = (await db.execute(
        select(Location).where(Location.id == conv.location_id,
                               Location.company_id == conv.company_id,
                               active(Location))
    )).scalar_one_or_none()
    if loc is None:
        raise _ToolError("location_unavailable")
    return loc


def _ctx(conv: AgentConversation, loc: Location, rules: RulesSchema,
         now: datetime) -> ProviderContext:
    return ProviderContext(company_id=conv.company_id, location_id=conv.location_id,
                           timezone=loc.timezone, rules=rules, now=now)


async def _provider(db: AsyncSession, conv: AgentConversation, now: datetime
                    ) -> tuple[BookingProvider, ProviderContext, Location]:
    """The conversation's provider and its context (the location must be active)."""
    loc = await _location(db, conv)
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    return provider_for(conv), _ctx(conv, loc, rules, now), loc


async def find_slots(db: AsyncSession, conv: AgentConversation, ctx: ProviderContext,
                     party_size: int, window_start: datetime, window_end: datetime,
                     *, exclude_ref: str | None) -> list[ProviderSlot]:
    """The agent's one availability question, asked of the conversation's
    provider (for WizScheduler, `availability.find_slots` with the cutoff)."""
    return await provider_for(conv).find_slots(
        db, ctx, party_size, window_start, window_end, exclude_ref=exclude_ref)


def _local(dt: datetime, zone: ZoneInfo) -> dict[str, str]:
    local = dt.astimezone(zone)
    return {"start_iso": local.isoformat(), "local_time": local.strftime("%H:%M"),
            "local_date": local.date().isoformat()}


def _spread(items: list[Any], k: int) -> list[Any]:
    if len(items) <= k:
        return items
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


def _summary(res: ProviderBooking, zone: ZoneInfo) -> dict[str, Any]:
    when = _local(res.start, zone)
    return {"confirmation_code": res.code, "name": res.diner_name,
            "party_size": res.party_size, "status": res.status,
            "local_time": when["local_time"], "local_date": when["local_date"]}


async def _verified(db: AsyncSession, conv: AgentConversation,
                    provider: BookingProvider, ctx: ProviderContext) -> ProviderBooking:
    if conv.verified_reservation_id is None:
        raise _ToolError("not_verified")
    res = await provider.lookup(db, ctx, ref=conv.verified_reservation_id)
    if res is None:
        raise _ToolError("not_verified")
    return res


async def _find_slots(db: AsyncSession, conv: AgentConversation,
                      inp: dict[str, Any], now: datetime) -> ToolOutcome:
    party_size = _party(inp.get("party_size"))
    try:
        day = date.fromisoformat(inp.get("date"))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _ToolError("invalid_input") from exc
    earliest, latest = _hhmm(inp.get("earliest")), _hhmm(inp.get("latest"))
    loc = await _location(db, conv)
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    if party_size > rules.max_party_size:
        raise _ToolError("party_too_large")
    ctx = _ctx(conv, loc, rules, now)
    zone = ZoneInfo(loc.timezone)
    window_start = datetime.combine(day, earliest or time(0, 0), tzinfo=zone)
    window_end = (datetime.combine(day, latest, tzinfo=zone) + timedelta(minutes=1)
                  if latest is not None
                  else datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone))
    if window_end <= window_start:
        raise _ToolError("invalid_input")
    # A verified booking being moved must not block its own new time.
    slots = await find_slots(db, conv, ctx, party_size, window_start, window_end,
                             exclude_ref=conv.verified_reservation_id)
    options = [_local(s.start, zone) for s in _spread(slots, MAX_OPTIONS)]
    fresh = [{"start_iso": o["start_iso"], "party_size": party_size} for o in options]
    instants = {_instant(o["start_iso"]) for o in fresh}

    def _repeated(entry: Any) -> bool:
        try:
            return (isinstance(entry, dict) and entry.get("party_size") == party_size
                    and _instant(entry.get("start_iso")) in instants)
        except _ToolError:
            return False

    # A re-offered start moves to the end, so the cap drops the oldest offers.
    kept = [e for e in conv.offered_starts or [] if not _repeated(e)]
    # Reassign (not mutate) so the JSONB change is tracked.
    conv.offered_starts = (kept + fresh)[-MAX_OFFERED:]
    return ToolOutcome({"party_size": party_size, "options": options})


async def _hold_slot(db: AsyncSession, conv: AgentConversation,
                     inp: dict[str, Any], now: datetime) -> ToolOutcome:
    start = _instant(inp.get("start_iso"))
    party_size = _party(inp.get("party_size"))
    if not _was_offered(conv, start, party_size):
        raise _ToolError("not_offered")
    name = inp.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _ToolError("invalid_input")
    phone = inp.get("phone")
    if not isinstance(phone, str):
        raise PhoneRequired()
    provider, ctx, loc = await _provider(db, conv, now)
    # One unconfirmed hold per conversation: release the earlier one first,
    # so a re-hold may reuse its tables. A failed new hold rolls the savepoint
    # back, which restores it. A confirmed row (a submitted pending request
    # has a code) is never released here.
    if conv.held_reservation_id is not None:
        old = await provider.lookup(db, ctx, ref=conv.held_reservation_id)
        if old is not None and old.status in (HELD, PENDING) and old.code is None:
            try:
                await provider.cancel(db, ctx, old.ref)
            except InvalidTransition:
                pass  # it lapsed to expired under the lock: nothing to release
    res = await provider.hold(db, ctx, party_size, start, name=name.strip(),
                              phone=phone.strip(), channel=conv.channel,
                              conversation_id=conv.id)
    conv.held_reservation_id = res.ref
    when = _local(res.start, ZoneInfo(loc.timezone))
    held_until = res.held_until or now
    return ToolOutcome({
        "hold_id": res.ref, "local_time": when["local_time"],
        "local_date": when["local_date"], "party_size": res.party_size,
        "status": "pending_approval" if res.status == PENDING else "held",
        "expires_in_minutes": max(0, math.ceil((held_until - now).total_seconds() / 60)),
    }, expect=EXPECT_CONFIRM_HOLD)


async def _recent_lookup_failures(db: AsyncSession, conv: AgentConversation,
                                  phone: str, now: datetime, *,
                                  by_source: bool = False) -> int:
    """Failures for this phone at this location in the lockout window; with
    `by_source`, only those from this conversation's viewer key."""
    since = now - timedelta(minutes=settings.RESERVATION_AGENT_LOOKUP_LOCKOUT_MINUTES)
    q = select(func.count()).select_from(ReservationLookupFailure).where(
        ReservationLookupFailure.company_id == conv.company_id,
        ReservationLookupFailure.location_id == conv.location_id,
        ReservationLookupFailure.phone == phone,
        ReservationLookupFailure.created_at > since)
    if by_source:
        q = q.where(ReservationLookupFailure.source_key.is_(None)
                    if conv.source_key is None
                    else ReservationLookupFailure.source_key == conv.source_key)
    return (await db.execute(q)).scalar_one()


async def _lookup_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    if (conv.failed_lookups or 0) >= MAX_FAILED_LOOKUPS:
        raise _ToolError("too_many_attempts")
    code, phone = inp.get("confirmation_code"), inp.get("phone")
    phone = phone.strip() if isinstance(phone, str) else None
    # The lockouts span conversations, so a new conversation per guess does
    # not reset them. Keyed on the exact phone lookup() matches: a
    # reformatted number can never find the booking anyway. From one viewer
    # the lookups are refused; from anyone, past a higher figure, the diner
    # is handed to staff, who can verify them another way.
    if phone:
        if await _recent_lookup_failures(db, conv, phone, now) \
                >= settings.RESERVATION_AGENT_LOOKUP_PHONE_HANDOFF_FAILURES:
            raise _ToolError("too_many_attempts", handoff=True)
        if await _recent_lookup_failures(db, conv, phone, now, by_source=True) \
                >= settings.RESERVATION_AGENT_LOOKUP_LOCKOUT_FAILURES:
            raise _ToolError("too_many_attempts")
    res = None
    loc = None
    if isinstance(code, str) and phone:
        provider, ctx, loc = await _provider(db, conv, now)
        res = await provider.lookup(db, ctx, code=code, phone=phone)
    # A new lookup drops anything staged against the previous verification.
    if res is None:
        if phone:
            db.add(ReservationLookupFailure(
                company_id=conv.company_id, location_id=conv.location_id,
                phone=phone, source_key=conv.source_key, created_at=now))
        conv.failed_lookups = (conv.failed_lookups or 0) + 1
        conv.verified_reservation_id = None
        conv.pending_cancel_id = None
        conv.pending_modify = None
        return ToolOutcome({"found": False})
    assert loc is not None  # a found booking came from a provider call
    conv.verified_reservation_id = res.ref
    conv.pending_cancel_id = None
    conv.pending_modify = None
    return ToolOutcome({"found": True, **_summary(res, ZoneInfo(loc.timezone))})


async def _modify_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    """Validate a change and stage it; the confirm_modify action applies it."""
    provider, ctx, loc = await _provider(db, conv, now)
    res = await _verified(db, conv, provider, ctx)
    if res.status not in (BOOKED, PENDING):
        raise InvalidTransition()
    raw_party, raw_start = inp.get("party_size"), inp.get("start_iso")
    if raw_party is None and raw_start is None:
        raise _ToolError("invalid_input")
    party_size = _party(raw_party) if raw_party is not None else None
    start = _instant(raw_start) if raw_start is not None else None
    new_party = party_size or res.party_size
    if start is not None and not _was_offered(conv, start, new_party):
        raise _ToolError("not_offered")
    rules = ctx.rules
    if new_party > rules.max_party_size:
        raise PartyTooLarge()
    # Same rule booking.modify(by_staff=False) applies: a diner cannot grow a
    # booked party past the self-book limit, which would skip approval.
    if res.status == BOOKED and new_party != res.party_size \
            and new_party > rules.max_self_book_party:
        raise PartyTooLarge()
    new_start = start or res.start.astimezone(timezone.utc)
    slots = await find_slots(db, conv, ctx, new_party, new_start,
                             new_start + timedelta(minutes=1), exclude_ref=res.ref)
    if not any(s.start.astimezone(timezone.utc) == new_start for s in slots):
        raise SlotUnavailable()
    zone = ZoneInfo(loc.timezone)
    conv.pending_modify = {"start_iso": start.astimezone(zone).isoformat()
                           if start is not None else None,
                           "party_size": party_size}
    conv.pending_cancel_id = None  # one staged request at a time
    when = _local(new_start, zone)
    return ToolOutcome({
        "awaiting_confirmation": True, "current": _summary(res, zone),
        "proposed": {"party_size": new_party, "local_time": when["local_time"],
                     "local_date": when["local_date"]},
    }, expect=EXPECT_CONFIRM_MODIFY)


async def _cancel_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    provider, ctx, loc = await _provider(db, conv, now)
    res = await _verified(db, conv, provider, ctx)
    if res.status not in (HELD, PENDING, BOOKED):
        raise InvalidTransition()
    conv.pending_cancel_id = res.ref
    conv.pending_modify = None  # one staged request at a time
    return ToolOutcome({"awaiting_confirmation": True,
                        **_summary(res, ZoneInfo(loc.timezone))},
                       expect=EXPECT_CONFIRM_CANCEL)


async def _handoff_to_staff(db: AsyncSession, conv: AgentConversation,
                            inp: dict[str, Any], now: datetime) -> ToolOutcome:
    reason = inp.get("reason")
    reason = reason if isinstance(reason, str) else ""
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    await recording.record_event(db, conv, "handoff", {"reason": reason})
    key = "transfer_number" if conv.channel == "voice" else "phone"
    return ToolOutcome({"handed_off": True, key: rules.staff_transfer_number},
                       expect=EXPECT_HANDOFF)


async def _restaurant_info(db: AsyncSession, conv: AgentConversation,
                           inp: dict[str, Any], now: datetime) -> ToolOutcome:
    loc = await _location(db, conv)
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    today = now.astimezone(ZoneInfo(loc.timezone)).date()
    return ToolOutcome({
        "name": loc.name,
        "facts": list(rules.persona.facts),
        "today": today.isoformat(),
        "service_periods": [
            {"name": p.name, "first_seating": p.first_seating.strftime("%H:%M"),
             "last_seating": p.last_seating.strftime("%H:%M")}
            for p in rules.periods_for(today)
        ],
        "max_party_size": rules.max_party_size,
    })


_Handler = Callable[[AsyncSession, AgentConversation, dict[str, Any], datetime],
                    Awaitable[ToolOutcome]]

_HANDLERS: dict[str, _Handler] = {
    "find_slots": _find_slots,
    "hold_slot": _hold_slot,
    "lookup_booking": _lookup_booking,
    "modify_booking": _modify_booking,
    "cancel_booking": _cancel_booking,
    "handoff_to_staff": _handoff_to_staff,
    "restaurant_info": _restaurant_info,
}


async def execute_tool(
    db: AsyncSession, conv: AgentConversation, name: str, tool_input: dict[str, Any],
    *, now: datetime,
) -> ToolOutcome:
    """Run one tool call. Never raises: domain errors come back as
    `{"error": code}` with `is_error=True`, anything else as `"internal"`."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return ToolOutcome({"error": "unknown_tool"}, is_error=True)
    if not isinstance(tool_input, dict):
        return ToolOutcome({"error": "invalid_input"}, is_error=True)
    now = now.astimezone(timezone.utc)
    # Handlers change `conv` only after their last write can fail, so a rolled
    # back savepoint never leaves a half-updated conversation in memory.
    try:
        async with db.begin_nested():
            outcome = await handler(db, conv, tool_input, now)
            await db.flush()
        return outcome
    except _ToolError as exc:
        # A handoff error (the phone-wide lookup lockout) ends the turn with staff.
        return ToolOutcome({"error": exc.code, "handoff": True} if exc.handoff
                           else {"error": exc.code}, is_error=True)
    except ProviderError as exc:
        return ToolOutcome({"error": exc.code}, is_error=True)
    except Exception:
        logger.exception("agent tool %s failed (conversation %s)", name, conv.id)
        return ToolOutcome({"error": "internal"}, is_error=True)
