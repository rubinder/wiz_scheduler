"""Deterministic, read-only grading of one scenario run's database end state.

What counts as a booking: a reservation at the scenario's location linked to
the conversation (its `conversation_id`, or the conversation's
`reservation_id`, which is how a modify or cancel of a seeded booking is
linked) whose status is booked or seated, or pending_approval with a
confirmation code (a submitted request). Live holds and code-less pending
rows are not bookings: they lapse on their own after hold_timeout_minutes.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models import Location
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.evals.scenarios_io import ExpectedBooking, Scenario
from backend.reservations.evals.seed import SeededScenario
from backend.reservations.models import (
    BOOKED,
    CANCELLED,
    PENDING,
    SEATED,
    DiningTable,
    Reservation,
    ReservationTable,
)


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def _is_booking(r: Reservation) -> bool:
    return r.status in (BOOKED, SEATED) or (
        r.status == PENDING and r.confirmation_code is not None)


def _describe(r: Reservation, zone: ZoneInfo) -> str:
    local = r.start_at.astimezone(zone)
    return (f"party {r.party_size} {local:%Y-%m-%d %H:%M} {r.status} "
            f"code={r.confirmation_code} name={r.diner_name!r} phone={r.diner_phone}")


def _mismatches(r: Reservation, exp: ExpectedBooking, facts: dict[str, str],
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


def _percentile(values: list[float], pct: float) -> float | None:
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
    linked = or_(Reservation.conversation_id == conv.id,
                 Reservation.id == conv.reservation_id)
    rows = (await db.execute(
        select(Reservation)
        .where(Reservation.company_id == scope[0], Reservation.location_id == scope[1],
               linked)
        .order_by(Reservation.created_at, Reservation.id)
        .execution_options(populate_existing=True)
    )).scalars().all()
    bookings = [r for r in rows if _is_booking(r)]
    matched: Reservation | None = None
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

    # Tables.
    failures.extend(await _double_booked(db, seeded))

    # Handoff.
    events = (await db.execute(
        select(AgentEvent)
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == scope[0])
        .order_by(AgentEvent.seq)
    )).scalars().all()
    handed_off = any(e.type == "handoff" for e in events)
    if exp.handoff is not None and handed_off != exp.handoff:
        failures.append("handoff event expected but missing" if exp.handoff
                        else "unexpected handoff event")

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
    metrics: dict[str, Any] = {
        "turns": turns,
        "diner_turns": sum(1 for e in events if e.type == "user_turn"),
        "model_calls": sum(1 for e in events if e.type == "assistant_turn"),
        "total_cost_usd": float(conv.cost_usd or Decimal(0)),
        "llm_p50_ms": _percentile(llm_ms, 50),
        "llm_p95_ms": _percentile(llm_ms, 95),
        "handoff": handed_off,
        "outcome": conv.outcome,
    }
    return CheckResult(passed=not failures, failures=failures, metrics=metrics)
