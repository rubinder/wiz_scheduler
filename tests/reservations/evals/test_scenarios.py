"""Scenario files load strictly, and seeding isolates each scenario."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Location
from backend.reservations.availability import find_slots
from backend.reservations.evals.scenarios_io import (
    SCENARIO_DIR,
    ScenarioInvalid,
    load_scenarios,
)
from backend.reservations.evals.seed import seed_scenario
from backend.reservations.models import BOOKED, DiningTable, Reservation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXPECTED_IDS = {
    "book_four_friday_7pm", "book_with_ambiguous_date", "change_mind_mid_booking",
    "party_of_14_web", "party_of_14_voice", "outside_service_hours",
    "book_via_mock_external", "mock_external_transient_retry",
    "fully_booked_night", "change_booking_with_code", "cancel_with_wrong_phone",
    "prompt_injection_book_without_confirm", "book_four_friday_7pm_voice",
}


async def test_every_scenario_file_loads() -> None:
    scenarios = load_scenarios()
    assert {s.id for s in scenarios} == EXPECTED_IDS
    for s in scenarios:
        assert s.now.tzinfo is not None
        assert s.now.utcoffset() == timezone.utc.utcoffset(None)
        assert s.expect.max_turns > 0


async def test_load_selected_ids_and_unknown_id() -> None:
    [s] = load_scenarios(ids=["party_of_14_voice"])
    assert s.channel == "voice" and s.expect.handoff is True
    with pytest.raises(ScenarioInvalid):
        load_scenarios(ids=["no_such_scenario"])


def _copy_with(tmp_path: Path, mutate) -> Path:
    data = json.loads((SCENARIO_DIR / "book_four_friday_7pm.json").read_text())
    mutate(data)
    (tmp_path / "book_four_friday_7pm.json").write_text(json.dumps(data))
    return tmp_path


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(surprise=1),
    lambda d: d["expect"].update(surprise=1),
    lambda d: d["expect"]["booking"].update(surprise=1),
    lambda d: d["diner"].update(surprise=1),
    lambda d: d["location"].update(surprise=1),
    lambda d: d["floor"]["tables"][0].update(surprise=1),
    lambda d: d["rules"].update(surprise=1),
    lambda d: d["rules"]["service_periods"][0].update(surprise=1),
], ids=["top", "expect", "booking", "diner", "location", "table", "rules", "period"])
async def test_unknown_key_fails_loading(tmp_path: Path, mutate) -> None:
    with pytest.raises(ScenarioInvalid, match="surprise"):
        load_scenarios(_copy_with(tmp_path, mutate))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(now="2026-12-01T15:00:00"),
    lambda d: d.update(channel="sms"),
    lambda d: d.update(policy="whatever"),
    lambda d: d["expect"].update(outcome="won"),
    lambda d: d["expect"].pop("max_turns"),
    lambda d: d["location"].update(timezone="Mars/Olympus"),
    lambda d: d.update(id="other_id"),
], ids=["naive_now", "channel", "policy", "outcome", "missing", "zone", "id"])
async def test_invalid_values_fail_loading(tmp_path: Path, mutate) -> None:
    with pytest.raises(ScenarioInvalid):
        load_scenarios(_copy_with(tmp_path, mutate))


async def test_seeding_two_scenarios_gives_disjoint_data(db_session: AsyncSession) -> None:
    a, b = load_scenarios(ids=["book_four_friday_7pm", "change_booking_with_code"])
    sa = await seed_scenario(db_session, a)
    sb = await seed_scenario(db_session, b)
    assert len({sa.company_id, sb.company_id}) == 2
    assert len({sa.location_id, sb.location_id}) == 2
    assert sa.slug != sb.slug
    for seeded, scenario in ((sa, a), (sb, b)):
        loc = await db_session.get(Location, seeded.location_id)
        assert loc.company_id == seeded.company_id
        assert loc.timezone == scenario.location.timezone
        company = await db_session.get(Company, seeded.company_id)
        assert company.slug == seeded.slug
        tables = (await db_session.execute(
            select(func.count()).select_from(DiningTable)
            .where(DiningTable.location_id == seeded.location_id))).scalar_one()
        assert tables == len(scenario.floor["tables"])
    existing = (await db_session.execute(
        select(Reservation).where(Reservation.location_id == sb.location_id)
    )).scalars().all()
    assert [(r.status, r.confirmation_code) for r in existing] == [(BOOKED, "EVCHG2")]
    assert (await db_session.execute(
        select(func.count()).select_from(Reservation)
        .where(Reservation.location_id == sa.location_id))).scalar_one() == 0


async def test_every_scenario_seeds(db_session: AsyncSession) -> None:
    """Every scenario's floor, rules and existing bookings are valid."""
    for scenario in load_scenarios():
        seeded = await seed_scenario(db_session, scenario)
        rows = (await db_session.execute(
            select(Reservation).where(Reservation.location_id == seeded.location_id)
        )).scalars().all()
        assert len(rows) == len(scenario.existing), scenario.id
        codes = {e.confirmation_code for e in scenario.existing} - {None}
        assert codes <= {r.confirmation_code for r in rows}, scenario.id


async def test_expected_bookings_are_bookable(db_session: AsyncSession) -> None:
    """Each scenario's expected booking time is free at the scenario's now
    (for a modify, with the booking being moved excluded), so a failure is
    the agent's, not the scenario's."""
    for scenario in load_scenarios():
        exp = scenario.expect.booking
        if exp is None:
            continue
        seeded = await seed_scenario(db_session, scenario)
        start = datetime.fromisoformat(f"{exp.local_date}T{exp.local_time}").replace(
            tzinfo=ZoneInfo(scenario.location.timezone))
        exclude = None
        if exp.confirmation_code:
            exclude = (await db_session.execute(select(Reservation.id).where(
                Reservation.location_id == seeded.location_id,
                Reservation.confirmation_code == exp.confirmation_code))).scalar_one()
        slots = await find_slots(
            db_session, seeded.company_id, seeded.location_id, exp.party_size,
            start, start + timedelta(minutes=1), now=scenario.now,
            exclude_reservation_id=exclude)
        assert any(s.start == start for s in slots), scenario.id


async def test_fully_booked_night_is_full_on_friday(db_session: AsyncSession) -> None:
    [scenario] = load_scenarios(ids=["fully_booked_night"])
    seeded = await seed_scenario(db_session, scenario)
    zone = ZoneInfo(scenario.location.timezone)
    for party in (2, 4):
        slots = await find_slots(
            db_session, seeded.company_id, seeded.location_id, party,
            datetime(2026, 12, 4, 0, 0, tzinfo=zone),
            datetime(2026, 12, 5, 0, 0, tzinfo=zone), now=scenario.now)
        assert slots == []


async def test_outside_service_hours_has_no_11pm(db_session: AsyncSession) -> None:
    [scenario] = load_scenarios(ids=["outside_service_hours"])
    seeded = await seed_scenario(db_session, scenario)
    late = datetime(2026, 12, 3, 23, 0, tzinfo=ZoneInfo(scenario.location.timezone))
    assert await find_slots(db_session, seeded.company_id, seeded.location_id, 2,
                            late, late + timedelta(minutes=1), now=scenario.now) == []


@pytest.mark.parametrize("value", ["yes", 1, None])
async def test_no_confirmed_reservations_must_be_bool(tmp_path: Path, value) -> None:
    with pytest.raises(ScenarioInvalid, match="no_confirmed_reservations"):
        load_scenarios(_copy_with(tmp_path, lambda d: d["expect"].update(
            booking=None, no_confirmed_reservations=value)))


async def test_no_confirmed_reservations_conflicts_with_a_booking(tmp_path: Path) -> None:
    with pytest.raises(ScenarioInvalid, match="no_confirmed_reservations"):
        load_scenarios(_copy_with(tmp_path, lambda d: d["expect"].update(
            no_confirmed_reservations=True)))


async def test_goal_kind_is_optional_and_strict(tmp_path: Path) -> None:
    by_id = {s.id: s for s in load_scenarios()}
    assert by_id["book_four_friday_7pm"].goal_kind is None
    assert by_id["change_booking_with_code"].goal_kind == "change"
    assert by_id["cancel_with_wrong_phone"].goal_kind == "cancel"
    [s] = load_scenarios(_copy_with(tmp_path, lambda d: d.update(goal_kind="book")))
    assert s.goal_kind == "book"
    with pytest.raises(ScenarioInvalid, match="goal_kind"):
        load_scenarios(_copy_with(tmp_path, lambda d: d.update(goal_kind="modify")))


async def test_positive_expectations_load(tmp_path: Path) -> None:
    by_id = {s.id: s for s in load_scenarios()}
    cancel = by_id["cancel_with_wrong_phone"].expect
    assert [r.name for r in cancel.required_tool_calls] == ["lookup_booking"]
    assert cancel.verified is False and cancel.no_proposal is True
    assert cancel.allowed_handoff_reasons == ("lookup_lockout",)
    [hold] = by_id["prompt_injection_book_without_confirm"].expect.required_tool_calls
    assert (hold.name, hold.succeeded, hold.diner_turn_after) == ("hold_slot", True, True)
    plain = by_id["book_four_friday_7pm"].expect
    assert (plain.required_tool_calls, plain.verified, plain.no_proposal,
            plain.allowed_handoff_reasons) == ((), None, False, ())


@pytest.mark.parametrize("extra", [
    {"required_tool_calls": ["no_such_tool"]},
    {"required_tool_calls": "lookup_booking"},
    {"required_tool_calls": [{"name": "hold_slot", "surprise": True}]},
    {"required_tool_calls": [{"name": "hold_slot", "succeeded": "yes"}]},
    {"verified": "no"},
    {"no_proposal": None},
    {"allowed_handoff_reasons": ["round_cap"]},
    {"allowed_handoff_reasons": "lookup_lockout"},
], ids=["tool", "not_list", "unknown_key", "flag", "verified", "no_proposal",
        "reason", "reasons_not_list"])
async def test_positive_expectations_are_strict(tmp_path: Path, extra: dict) -> None:
    with pytest.raises(ScenarioInvalid):
        load_scenarios(_copy_with(tmp_path, lambda d: d["expect"].update(extra)))


async def test_book_goal() -> None:
    by_id = {s.id: s for s in load_scenarios()}
    assert {sid for sid, s in by_id.items() if s.book_goal} == {
        "book_four_friday_7pm", "book_four_friday_7pm_voice", "book_with_ambiguous_date",
        "change_mind_mid_booking", "fully_booked_night", "outside_service_hours",
        "book_via_mock_external", "mock_external_transient_retry"}
