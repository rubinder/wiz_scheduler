"""The deterministic checker over hand-built end states."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations import booking
from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from backend.reservations.booking import DinerContact
from backend.reservations.evals.checker import check
from backend.reservations.evals.scenarios_io import Scenario, load_scenarios
from backend.reservations.evals.seed import SeededScenario, seed_scenario
from backend.reservations.models import (
    CANCELLED,
    SOURCE_WEB_AGENT,
    Reservation,
    ReservationTable,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _setup(db: AsyncSession, scenario_id: str
                 ) -> tuple[Scenario, SeededScenario, AgentConversation]:
    [scenario] = load_scenarios(ids=[scenario_id])
    seeded = await seed_scenario(db, scenario)
    conv = await recording.start_conversation(
        db, seeded.company_id, seeded.location_id, scenario.channel)
    await db.commit()
    return scenario, seeded, conv


def _local(scenario: Scenario, d: str, t: str) -> datetime:
    return datetime.fromisoformat(f"{d}T{t}").replace(
        tzinfo=ZoneInfo(scenario.location.timezone))


async def _book(db: AsyncSession, scenario: Scenario, seeded: SeededScenario,
                conv: AgentConversation, party: int, start: datetime) -> Reservation:
    facts = scenario.diner.facts
    res = await booking.hold(
        db, seeded.company_id, seeded.location_id, party, start,
        source=SOURCE_WEB_AGENT, conversation_id=conv.id, now=scenario.now,
        diner=DinerContact(facts["name"], facts["phone"]))
    res = await booking.confirm(db, seeded.company_id, res.id, now=scenario.now)
    conv.reservation_id = res.id
    await db.flush()
    return res


async def _turns(db: AsyncSession, conv: AgentConversation, n: int) -> None:
    for i in range(n):
        await recording.record_event(db, conv, "user_turn", {"content": f"t{i}"})
        await recording.record_event(
            db, conv, "assistant_turn", {"content": []},
            latency_ms={"llm": 100 * (i + 1)},
            usage={"input_tokens": 1000, "output_tokens": 100})
    conv.turn_count = n


async def test_correct_end_state_passes(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    exp = scenario.expect.booking
    await _book(db_session, scenario, seeded, conv, exp.party_size,
                _local(scenario, exp.local_date, exp.local_time))
    await _turns(db_session, conv, 3)
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()

    result = await check(db_session, seeded, conv, scenario)
    assert result.failures == []
    assert result.passed is True
    m = result.metrics
    assert m["diner_turns"] == 3
    assert m["model_calls"] == 3
    assert m["total_cost_usd"] > 0
    assert m["llm_p50_ms"] == 200
    assert m["llm_p95_ms"] == 300


async def test_check_is_read_only(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    await check(db_session, seeded, conv, scenario)
    assert not db_session.new and not db_session.dirty and not db_session.deleted


async def test_empty_end_state_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    result = await check(db_session, seeded, conv, scenario)
    assert result.passed is False
    assert any("expected booking not found" in f for f in result.failures)
    assert any("outcome" in f for f in result.failures)


async def test_wrong_party_size_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    exp = scenario.expect.booking
    await _book(db_session, scenario, seeded, conv, exp.party_size + 1,
                _local(scenario, exp.local_date, exp.local_time))
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.passed is False
    assert any("expected booking not found" in f for f in result.failures)
    assert any("unexpected booking" in f for f in result.failures)


async def test_wrong_local_time_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    exp = scenario.expect.booking
    await _book(db_session, scenario, seeded, conv, exp.party_size,
                _local(scenario, exp.local_date, exp.local_time) + timedelta(hours=1))
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert any("expected booking not found" in f for f in result.failures)


async def test_double_booked_table_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "book_four_friday_7pm")
    exp = scenario.expect.booking
    res = await _book(db_session, scenario, seeded, conv, exp.party_size,
                      _local(scenario, exp.local_date, exp.local_time))
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    # Only a broken write path could do this: drop the backstop to build it.
    await db_session.execute(text(
        "ALTER TABLE reservation_tables DROP CONSTRAINT ex_reservation_tables_no_overlap"))
    row = (await db_session.execute(select(ReservationTable).where(
        ReservationTable.reservation_id == res.id))).scalars().first()
    other = Reservation(
        company_id=seeded.company_id, location_id=seeded.location_id, party_size=2,
        start_at=res.start_at, end_at=res.end_at, status="booked", source="staff",
        confirmation_code="ZZZZZZ", diner_name="Other")
    db_session.add(other)
    await db_session.flush()
    db_session.add(ReservationTable(
        company_id=seeded.company_id, reservation_id=other.id,
        table_id=row.table_id, during=row.during, active=True))
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.passed is False
    assert any("double-booked" in f for f in result.failures)


async def test_handoff_expected_but_missing_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "party_of_14_voice")
    await recording.finish(db_session, conv, "handed_off")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert any("handoff" in f for f in result.failures)
    await recording.record_event(db_session, conv, "handoff", {"reason": "large_party"})
    await db_session.commit()
    assert (await check(db_session, seeded, conv, scenario)).passed is True


async def test_pending_request_needs_a_code(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "party_of_14_web")
    exp = scenario.expect.booking
    assert exp.status == "pending_approval"
    facts = scenario.diner.facts
    res = await booking.hold(
        db_session, seeded.company_id, seeded.location_id, exp.party_size,
        _local(scenario, exp.local_date, exp.local_time), source=SOURCE_WEB_AGENT,
        conversation_id=conv.id, now=scenario.now,
        diner=DinerContact(facts["name"], facts["phone"]))
    conv.held_reservation_id = res.id
    await recording.finish(db_session, conv, "handed_off")
    await db_session.commit()
    # An unsubmitted (code-less) request is not the booking.
    result = await check(db_session, seeded, conv, scenario)
    assert any("expected booking not found" in f for f in result.failures)
    await booking.confirm(db_session, seeded.company_id, res.id, now=scenario.now)
    conv.reservation_id = res.id
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.failures == []


async def test_cancel_with_wrong_phone_must_keep_the_booking(
        db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "cancel_with_wrong_phone")
    await recording.finish(db_session, conv, "abandoned")
    await db_session.commit()
    assert (await check(db_session, seeded, conv, scenario)).passed is True
    [code] = scenario.expect.kept_codes
    res = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seeded.location_id,
        Reservation.confirmation_code == code))).scalar_one()
    await booking.cancel(db_session, seeded.company_id, res.id, now=scenario.now)
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert any(code in f for f in result.failures)


async def test_modify_scenario_checks_the_same_code(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "change_booking_with_code")
    exp = scenario.expect.booking
    res = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seeded.location_id,
        Reservation.confirmation_code == exp.confirmation_code))).scalar_one()
    await booking.modify(db_session, seeded.company_id, res.id,
                         start=_local(scenario, exp.local_date, exp.local_time),
                         now=scenario.now)
    conv.reservation_id = res.id
    await recording.finish(db_session, conv, "modified")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.failures == []
    assert res.status != CANCELLED


async def test_turn_cap_fails(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "party_of_14_voice")
    await recording.record_event(db_session, conv, "handoff", {"reason": "x"})
    await recording.finish(db_session, conv, "handed_off")
    conv.turn_count = scenario.expect.max_turns + 1
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert any("turns" in f for f in result.failures)


async def test_injection_scenario_allows_a_live_hold_only(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "prompt_injection_book_without_confirm")
    assert scenario.expect.no_confirmed_reservations is True
    assert scenario.expect.outcomes == ("abandoned",)
    assert scenario.expect.handoff is False
    facts = scenario.diner.facts
    res = await booking.hold(
        db_session, seeded.company_id, seeded.location_id, 4,
        _local(scenario, "2026-12-04", "19:00"), source=SOURCE_WEB_AGENT,
        now=scenario.now, diner=DinerContact(facts["name"], facts["phone"]))
    # Linked only through held_reservation_id.
    conv.held_reservation_id = res.id
    await recording.finish(db_session, conv, "abandoned")
    await db_session.commit()
    assert (await check(db_session, seeded, conv, scenario)).failures == []

    await booking.confirm(db_session, seeded.company_id, res.id, now=scenario.now)
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert any("confirmed without consent" in f for f in result.failures)


async def test_kept_booking_is_not_reported_as_unexpected(db_session: AsyncSession) -> None:
    scenario, seeded, conv = await _setup(db_session, "cancel_with_wrong_phone")
    [code] = scenario.expect.kept_codes
    res = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seeded.location_id,
        Reservation.confirmation_code == code))).scalar_one()
    conv.reservation_id = res.id
    await recording.finish(db_session, conv, "abandoned")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.failures == []


async def test_moved_kept_booking_fails(db_session: AsyncSession) -> None:
    """A kept row that was moved (still booked, linked to the conversation)
    is a failure, not an allowed booking."""
    scenario, seeded, conv = await _setup(db_session, "cancel_with_wrong_phone")
    [code] = scenario.expect.kept_codes
    res = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seeded.location_id,
        Reservation.confirmation_code == code))).scalar_one()
    await booking.modify(db_session, seeded.company_id, res.id,
                         start=_local(scenario, "2026-12-04", "20:00"), now=scenario.now)
    conv.reservation_id = res.id
    await recording.finish(db_session, conv, "abandoned")
    await db_session.commit()
    result = await check(db_session, seeded, conv, scenario)
    assert result.passed is False
    assert any(f.startswith(f"kept booking {code} changed") for f in result.failures)
