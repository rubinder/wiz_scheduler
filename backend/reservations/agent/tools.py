"""The host agent's tools (#259): Anthropic tool definitions and the executor
that maps a tool call onto the reservation services.

Guards, so the model cannot state or book what the evaluator never produced:

- `hold_slot` and a time change in `modify_booking` only accept a start that
  an earlier `find_slots` call in this conversation offered, for that party
  size (`conv.offered_starts`, compared as instants).
- `modify_booking` and `cancel_booking` act only on the reservation that
  `lookup_booking` verified by code and phone. `cancel_booking` does not
  cancel: it asks for the structured confirmation (`expect`).

Each tool runs in a savepoint, so a failed write leaves the session usable
and the conversation unchanged. `execute_tool` never raises. Flush only;
callers commit.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from backend.reservations import booking
from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from backend.reservations.availability import find_slots
from backend.reservations.booking import DinerContact, ReservationError
from backend.reservations.models import (
    BOOKED,
    HELD,
    PENDING,
    SOURCE_VOICE_AGENT,
    SOURCE_WEB_AGENT,
    Reservation,
)
from backend.reservations.rules import get_rules
from backend.services.archive import active

logger = logging.getLogger(__name__)

MAX_OPTIONS = 6

EXPECT_CONFIRM_HOLD = "confirm_hold"
EXPECT_CONFIRM_CANCEL = "confirm_cancel"
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
        "Change the party size and/or time of the booking found by lookup_booking. "
        "A new start_iso must be an option find_slots returned for the new party "
        "size. Null leaves a field unchanged.",
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
    def __init__(self, code: str) -> None:
        super().__init__(code)
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
    if value is None:
        return None
    try:
        return time.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


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


def _local(dt: datetime, zone: ZoneInfo) -> dict[str, str]:
    local = dt.astimezone(zone)
    return {"start_iso": local.isoformat(), "local_time": local.strftime("%H:%M"),
            "local_date": local.date().isoformat()}


def _spread(items: list[Any], k: int) -> list[Any]:
    if len(items) <= k:
        return items
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


def _summary(res: Reservation, zone: ZoneInfo) -> dict[str, Any]:
    when = _local(res.start_at, zone)
    return {"confirmation_code": res.confirmation_code, "name": res.diner_name,
            "party_size": res.party_size, "status": res.status,
            "local_time": when["local_time"], "local_date": when["local_date"]}


async def _verified(db: AsyncSession, conv: AgentConversation) -> Reservation:
    if conv.verified_reservation_id is None:
        raise _ToolError("not_verified")
    res = (await db.execute(
        select(Reservation).where(Reservation.id == conv.verified_reservation_id,
                                  Reservation.company_id == conv.company_id,
                                  Reservation.location_id == conv.location_id)
    )).scalar_one_or_none()
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
    if (inp.get("earliest") is not None and earliest is None) or (
            inp.get("latest") is not None and latest is None):
        raise _ToolError("invalid_input")
    loc = await _location(db, conv)
    rules, _ = await get_rules(db, conv.company_id, conv.location_id)
    if party_size > rules.max_party_size:
        raise _ToolError("party_too_large")
    zone = ZoneInfo(loc.timezone)
    window_start = datetime.combine(day, earliest or time(0, 0), tzinfo=zone)
    window_end = (datetime.combine(day, latest, tzinfo=zone) + timedelta(minutes=1)
                  if latest is not None
                  else datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone))
    if window_end <= window_start:
        raise _ToolError("invalid_input")
    # A verified booking being moved must not block its own new time.
    slots = await find_slots(
        db, conv.company_id, conv.location_id, party_size, window_start, window_end,
        now=now, exclude_reservation_id=conv.verified_reservation_id, apply_cutoff=True,
    )
    options = [_local(s.start, zone) for s in _spread(slots, MAX_OPTIONS)]
    offered = list(conv.offered_starts or [])
    for opt in options:
        if not _was_offered(conv, _instant(opt["start_iso"]), party_size):
            offered.append({"start_iso": opt["start_iso"], "party_size": party_size})
    conv.offered_starts = offered  # reassign so the JSONB change is tracked
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
    diner = DinerContact(name=name.strip(), phone=phone.strip()
                         if isinstance(phone, str) else None)
    if diner.phone is None:
        raise booking.PhoneRequired()
    loc = await _location(db, conv)
    source = SOURCE_VOICE_AGENT if conv.channel == "voice" else SOURCE_WEB_AGENT
    res = await booking.hold(db, conv.company_id, conv.location_id, party_size, start,
                             source=source, conversation_id=conv.id, now=now,
                             diner=diner)
    previous = conv.held_reservation_id
    if previous is not None and previous != res.id:
        # Only one live hold per conversation: release the earlier one.
        old = await db.get(Reservation, previous)
        if old is not None and old.company_id == conv.company_id \
                and old.status in (HELD, PENDING):
            await booking.cancel(db, conv.company_id, previous, now=now)
    conv.held_reservation_id = res.id
    when = _local(res.start_at, ZoneInfo(loc.timezone))
    held_until = res.held_until or now
    return ToolOutcome({
        "hold_id": res.id, "local_time": when["local_time"],
        "local_date": when["local_date"], "party_size": res.party_size,
        "status": "pending_approval" if res.status == PENDING else "held",
        "expires_in_minutes": max(0, math.ceil((held_until - now).total_seconds() / 60)),
    }, expect=EXPECT_CONFIRM_HOLD)


async def _lookup_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    code, phone = inp.get("confirmation_code"), inp.get("phone")
    if not isinstance(code, str) or not isinstance(phone, str):
        return ToolOutcome({"found": False})
    res = await booking.lookup(db, conv.company_id, conv.location_id, code, phone.strip())
    if res is None:
        return ToolOutcome({"found": False})
    loc = await _location(db, conv)
    conv.verified_reservation_id = res.id
    return ToolOutcome({"found": True, **_summary(res, ZoneInfo(loc.timezone))})


async def _modify_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    res = await _verified(db, conv)
    raw_party, raw_start = inp.get("party_size"), inp.get("start_iso")
    if raw_party is None and raw_start is None:
        raise _ToolError("invalid_input")
    party_size = _party(raw_party) if raw_party is not None else None
    start = _instant(raw_start) if raw_start is not None else None
    if start is not None and not _was_offered(conv, start, party_size or res.party_size):
        raise _ToolError("not_offered")
    loc = await _location(db, conv)
    res = await booking.modify(db, conv.company_id, res.id, party_size=party_size,
                               start=start, now=now, by_staff=False)
    return ToolOutcome({"modified": True, **_summary(res, ZoneInfo(loc.timezone))})


async def _cancel_booking(db: AsyncSession, conv: AgentConversation,
                          inp: dict[str, Any], now: datetime) -> ToolOutcome:
    res = await _verified(db, conv)
    if res.status not in (HELD, PENDING, BOOKED):
        raise booking.InvalidTransition()
    loc = await _location(db, conv)
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
        return ToolOutcome({"error": exc.code}, is_error=True)
    except ReservationError as exc:
        return ToolOutcome({"error": exc.code}, is_error=True)
    except Exception:
        logger.exception("agent tool %s failed (conversation %s)", name, conv.id)
        return ToolOutcome({"error": "internal"}, is_error=True)
