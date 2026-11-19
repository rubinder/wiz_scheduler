"""Deterministic, read-only grading of one scenario run's database end state.

What counts as a booking: a booking linked to the conversation, read through
the conversation's provider (`providers.provider_for(conv).lookup(ref=...)`
for its `reservation_id` / `held_reservation_id`; `reservation_id` is how a
modify or cancel of a seeded booking is linked), plus, for WizScheduler, any
reservation naming the conversation in its own `conversation_id`. It counts
when its status is booked or seated, or pending_approval with a confirmation
code (a submitted request). Live holds and code-less pending rows are not
bookings: they lapse on their own after hold_timeout_minutes. A mock
external conversation's bookings are therefore graded from the mock's own
inventory, never from `reservations`. Seeded bookings (kept / cancelled
codes) are WizScheduler reservations: the scenario seeds them there.

A model failure is never a correct handoff: a handoff counts only when the
model called handoff_to_staff, or the core handed off for a reason the
scenario lists in `allowed_handoff_reasons`. Any other core handoff
(round_cap, max_tokens, llm_error, ...) and any llm_error / llm_timeout /
internal error event fail the run.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models import Location
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.evals.scenarios_io import (
    ExpectedBooking,
    RequiredToolCall,
    Scenario,
)
from backend.reservations.evals.seed import SeededScenario
from backend.reservations.providers import ProviderBooking, ProviderContext, provider_for
from backend.reservations.rules import get_rules
from backend.reservations.models import (
    BOOKED,
    CANCELLED,
    COMPLETED,
    PENDING,
    SEATED,
    DiningTable,
    Reservation,
    ReservationTable,
)


# Error events that mean the model or the core failed, not the diner.
FAILING_ERRORS = ("llm_error", "llm_timeout", "internal")
# Tools whose success raises a confirm-change / confirm-cancel proposal.
PROPOSAL_TOOLS = ("modify_booking", "cancel_booking")
HANDOFF_TOOL = "handoff_to_staff"


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Booked:
    """A linked booking, from either provider, in the fields grading reads."""

    ref: str
    start_at: datetime
    party_size: int
    status: str
    confirmation_code: str | None
    diner_name: str | None
    diner_phone: str | None

    @classmethod
    def of_reservation(cls, r: Reservation) -> "_Booked":
        return cls(r.id, r.start_at, r.party_size, r.status, r.confirmation_code,
                   r.diner_name, r.diner_phone)

    @classmethod
    def of_booking(cls, b: ProviderBooking) -> "_Booked":
        return cls(b.ref, b.start, b.party_size, b.status, b.code, b.diner_name,
                   b.diner_phone)


async def _linked_bookings(db: AsyncSession, conv: AgentConversation,
                           scope: tuple[str, str], zone_name: str) -> list[_Booked]:
    """The conversation's bookings, through its own provider."""
    provider = provider_for(conv)
    rules, _ = await get_rules(db, scope[0], scope[1])
    ctx = ProviderContext(scope[0], scope[1], zone_name, rules, datetime.now(timezone.utc),
                          conversation_id=conv.id)
    out: dict[str, _Booked] = {}
    if provider.name == "wizscheduler":
        # WizScheduler's own link, which survives a declined, expired or
        # replaced hold.
        rows = (await db.execute(
            select(Reservation)
            .where(Reservation.company_id == scope[0],
                   Reservation.location_id == scope[1],
                   Reservation.conversation_id == conv.id)
            .order_by(Reservation.created_at, Reservation.id)
            .execution_options(populate_existing=True)
        )).scalars().all()
        out = {r.id: _Booked.of_reservation(r) for r in rows}
    for ref in (conv.reservation_id, conv.held_reservation_id):
        if ref is not None and ref not in out:
            found = await provider.lookup(db, ctx, ref=ref)
            if found is not None:
                out[ref] = _Booked.of_booking(found)
    return list(out.values())


def _is_booking(r: _Booked | Reservation) -> bool:
    return r.status in (BOOKED, SEATED) or (
        r.status == PENDING and r.confirmation_code is not None)


def _confirmed(r: _Booked | Reservation) -> bool:
    """Ever confirmed: has a code, or reached a post-confirm status."""
    return r.confirmation_code is not None or r.status in (BOOKED, SEATED, COMPLETED)


def _describe(r: _Booked | Reservation, zone: ZoneInfo) -> str:
    local = r.start_at.astimezone(zone)
    return (f"party {r.party_size} {local:%Y-%m-%d %H:%M} {r.status} "
            f"code={r.confirmation_code} name={r.diner_name!r} phone={r.diner_phone}")


def _mismatches(r: _Booked, exp: ExpectedBooking, facts: dict[str, str],
                zone: ZoneInfo) -> list[str]:
    local = r.start_at.astimezone(zone)
    wrong = []
    if r.party_size != exp.party_size:
        wrong.append("party_size")
    if local.date().isoformat() != exp.local_date:
        wrong.append("local_date")
    if local.strftime("%H:%M") != exp.local_time:
        wrong.append("local_time")
    if r.status != exp.status:
        wrong.append("status")
    if r.confirmation_code is None:
        wrong.append("confirmation_code")
    elif exp.confirmation_code is not None and r.confirmation_code != exp.confirmation_code:
        wrong.append("confirmation_code")
    if (r.diner_name or "").strip().casefold() != facts["name"].strip().casefold():
        wrong.append("diner_name")
    if r.diner_phone != facts["phone"]:
        wrong.append("diner_phone")
    return wrong


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


async def _double_booked(db: AsyncSession, seeded: SeededScenario) -> list[str]:
    a, b = aliased(ReservationTable), aliased(ReservationTable)
    rows = (await db.execute(
        select(DiningTable.label, a.reservation_id, b.reservation_id)
        .join(a, a.table_id == DiningTable.id)
        .join(b, and_(b.table_id == a.table_id, b.id > a.id))
        .where(DiningTable.company_id == seeded.company_id,
               DiningTable.location_id == seeded.location_id,
               a.active.is_(True), b.active.is_(True),
               a.during.op("&&")(b.during))
        .order_by(DiningTable.label)
    )).all()
    return [f"table {label} double-booked by {r1} and {r2}" for label, r1, r2 in rows]


@dataclass(frozen=True)
class _ToolCall:
    seq: int
    name: str
    ok: bool  # its tool_result was not an error


def _tool_calls(events: list[AgentEvent]) -> list[_ToolCall]:
    """Every tool_call with whether it succeeded, read from the first
    tool_result event after it (tool_use ids repeat across turns)."""
    calls = []
    for i, e in enumerate(events):
        if e.type != "tool_call":
            continue
        tid = (e.payload or {}).get("tool_use_id")
        ok = False
        for later in events[i + 1:]:
            if later.type == "tool_result":
                for r in (later.payload or {}).get("content") or []:
                    if isinstance(r, dict) and r.get("tool_use_id") == tid:
                        ok = not r.get("is_error", False)
                        break
                break
        calls.append(_ToolCall(e.seq, str((e.payload or {}).get("name")), ok))
    return calls


def _missing_tool_call(req: RequiredToolCall, calls: list[_ToolCall],
                       events: list[AgentEvent]) -> str | None:
    found = [c for c in calls if c.name == req.name and (c.ok or not req.succeeded)]
    what = f"{'successful ' if req.succeeded else ''}{req.name} tool call"
    if not found:
        return f"required {what} missing"
    if req.diner_turn_after and not any(
            e.type == "user_turn" and e.seq > c.seq for c in found for e in events):
        return f"no diner turn after the {what}"
    return None


async def check(db: AsyncSession, seeded: SeededScenario, conv: AgentConversation,
                scenario: Scenario) -> CheckResult:
    """Grade the run. Reads only; never flushes or commits."""
    exp = scenario.expect
    failures: list[str] = []
    scope = (seeded.company_id, seeded.location_id)
    conv = (await db.execute(
        select(AgentConversation)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == scope[0])
        .execution_options(populate_existing=True)
    )).scalar_one()
    if conv.location_id != scope[1]:
        failures.append("conversation is not at the scenario's location")
    zone_name = (await db.execute(
        select(Location.timezone).where(Location.id == scope[1],
                                        Location.company_id == scope[0])
    )).scalar_one()
    zone = ZoneInfo(zone_name)
    facts = scenario.diner.facts

    # Outcome.
    if conv.outcome not in exp.outcomes:
        failures.append(f"outcome {conv.outcome!r}, expected one of {list(exp.outcomes)}")

    # Bookings linked to this conversation.
    rows = await _linked_bookings(db, conv, scope, zone_name)
    # Seeded bookings the scenario says to keep are checked below, not here.
    kept = set(exp.kept_codes)
    bookings = [r for r in rows if _is_booking(r) and r.confirmation_code not in kept]
    matched: _Booked | None = None
    if exp.booking is not None:
        for r in bookings:
            if not _mismatches(r, exp.booking, facts, zone):
                matched = r
                break
        if matched is None:
            b = exp.booking
            detail = "; ".join(
                f"{_describe(r, zone)} (wrong: {', '.join(_mismatches(r, b, facts, zone))})"
                for r in bookings) or "none"
            failures.append(
                f"expected booking not found: party {b.party_size} {b.local_date} "
                f"{b.local_time} {b.status} for {facts['name']} {facts['phone']}"
                f"{' code ' + b.confirmation_code if b.confirmation_code else ''}; "
                f"found {detail}")
    for r in bookings:
        if r is not matched:
            failures.append(f"unexpected booking: {_describe(r, zone)}")

    if exp.no_confirmed_reservations:
        for r in rows:
            if _confirmed(r):
                failures.append(f"reservation confirmed without consent: {_describe(r, zone)}")

    # Tables.
    failures.extend(await _double_booked(db, seeded))

    # Handoff.
    events = (await db.execute(
        select(AgentEvent)
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == scope[0])
        .order_by(AgentEvent.seq)
    )).scalars().all()
    events = list(events)
    calls = _tool_calls(events)
    tool_handoff = any(c.name == HANDOFF_TOOL for c in calls)
    core_reasons = [str((e.payload or {}).get("reason")) for i, e in enumerate(events)
                    if e.type == "handoff" and not (
                        i > 0 and events[i - 1].type == "tool_call"
                        and (events[i - 1].payload or {}).get("name") == HANDOFF_TOOL)]
    allowed = set(exp.allowed_handoff_reasons)
    for reason in core_reasons:
        if reason not in allowed:
            failures.append(f"handoff without handoff_to_staff (reason {reason})")
    handed_off = tool_handoff or any(r in allowed for r in core_reasons)
    if exp.handoff is True and not handed_off:
        failures.append("handoff expected but handoff_to_staff was not called")
    elif exp.handoff is False and (tool_handoff or core_reasons):
        failures.append("unexpected handoff event")

    # Errors: a failed model call or core is a failed run.
    errors = [str((e.payload or {}).get("error")) for e in events if e.type == "error"]
    for err in errors:
        if err in FAILING_ERRORS:
            failures.append(f"error event: {err}")

    # Positive assertions.
    for req in exp.required_tool_calls:
        missing = _missing_tool_call(req, calls, events)
        if missing:
            failures.append(missing)
    if exp.verified is not None and \
            (conv.verified_reservation_id is not None) != exp.verified:
        failures.append("conversation has a verified booking" if not exp.verified
                        else "conversation has no verified booking")
    if exp.no_proposal:
        for c in calls:
            if c.name in PROPOSAL_TOOLS and c.ok:
                failures.append(f"a proposal was raised ({c.name})")

    # Seeded bookings that must be cancelled / must be kept.
    async def by_code(code: str) -> Reservation | None:
        return (await db.execute(
            select(Reservation).where(Reservation.company_id == scope[0],
                                      Reservation.location_id == scope[1],
                                      Reservation.confirmation_code == code)
            .execution_options(populate_existing=True)
        )).scalar_one_or_none()

    if exp.cancelled_code is not None:
        r = await by_code(exp.cancelled_code)
        if r is None or r.status != CANCELLED:
            failures.append(f"booking {exp.cancelled_code} not cancelled "
                            f"(status {r.status if r else 'missing'})")
    seeded_by_code = {e.confirmation_code: e for e in scenario.existing
                      if e.confirmation_code}
    for code in exp.kept_codes:
        r, want = await by_code(code), seeded_by_code[code]
        if r is None:
            failures.append(f"kept booking {code} is missing")
            continue
        local = r.start_at.astimezone(zone).replace(tzinfo=None)
        if (r.status != want.status or r.party_size != want.party_size
                or local != want.start_local):
            failures.append(f"kept booking {code} changed: {_describe(r, zone)}")

    # Turns.
    turns = conv.turn_count or 0
    if turns > exp.max_turns:
        failures.append(f"{turns} turns, more than max_turns {exp.max_turns}")

    llm_ms = [float(e.latency_ms["llm"]) for e in events
              if isinstance(e.latency_ms, dict)
              and isinstance(e.latency_ms.get("llm"), (int, float))]
    tools_ms = [float(e.latency_ms["tools"]) for e in events
                if isinstance(e.latency_ms, dict)
                and isinstance(e.latency_ms.get("tools"), (int, float))]
    metrics: dict[str, Any] = {
        "turns": turns,
        "diner_turns": sum(1 for e in events if e.type == "user_turn"),
        "model_calls": sum(1 for e in events if e.type == "assistant_turn"),
        "total_cost_usd": float(conv.cost_usd or Decimal(0)),
        "llm_ms": llm_ms,
        "tools_ms": tools_ms,
        "llm_p50_ms": percentile(llm_ms, 50),
        "llm_p95_ms": percentile(llm_ms, 95),
        "handoff": handed_off,
        "errors": errors,
        "outcome": conv.outcome,
    }
    return CheckResult(passed=not failures, failures=failures, metrics=metrics)
