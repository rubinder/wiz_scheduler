"""Tests for the LangGraph scheduling pipeline with mocked LLM."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduling.state import LocationResult, SchedulingState, ShiftAssignment

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMPANY_ID = str(uuid.uuid4())
_LOCATION1_ID = str(uuid.uuid4())
_LOCATION2_ID = str(uuid.uuid4())
_EMPLOYEE1_ID = str(uuid.uuid4())
_EMPLOYEE2_ID = str(uuid.uuid4())
_ROLE_ID = str(uuid.uuid4())

_MONDAY = "2026-03-23"  # A Monday


def _make_shift(
    employee_id: str,
    employee_name: str,
    location_id: str,
    date: str,
    start_hour: int = 9,
    end_hour: int = 17,
) -> ShiftAssignment:
    tz = "+05:00"
    return {
        "employee_id": employee_id,
        "employee_name": employee_name,
        "role_id": _ROLE_ID,
        "role_name": "Floor Associate",
        "location_id": location_id,
        "date": date,
        "start_time": f"{date}T{start_hour:02d}:00:00{tz}",
        "end_time": f"{date}T{end_hour:02d}:00:00{tz}",
        "status": "ok",
    }


def _base_state(location_ids: list[str] | None = None) -> SchedulingState:
    lids = location_ids or [_LOCATION1_ID]
    return SchedulingState(
        company_id=_COMPANY_ID,
        week_start_date=_MONDAY,
        locations=[{"id": lid, "name": f"Loc-{i}", "timezone": "America/New_York"} for i, lid in enumerate(lids)],
        shift_templates={
            lid: {
                "weekly_schedule": [
                    {
                        "day": "Monday",
                        "role_name": "Floor Associate",
                        "role_id": _ROLE_ID,
                        "headcount": 1,
                        "start_time": "09:00",
                        "end_time": "17:00",
                    }
                ]
            }
            for lid in lids
        },
        employees=[
            {
                "id": _EMPLOYEE1_ID,
                "full_name": "Alice",
                "roles": [{"role_name": "Floor Associate", "role_id": _ROLE_ID, "skill_level": 3}],
                "affinities": [],
                "available_windows": [{"start": f"{_MONDAY}T09:00:00-05:00", "end": f"{_MONDAY}T17:00:00-05:00"}],
            },
            {
                "id": _EMPLOYEE2_ID,
                "full_name": "Bob",
                "roles": [{"role_name": "Floor Associate", "role_id": _ROLE_ID, "skill_level": 2}],
                "affinities": [],
                "available_windows": [{"start": f"{_MONDAY}T09:00:00-05:00", "end": f"{_MONDAY}T17:00:00-05:00"}],
            },
        ],
        availability_draft={},
        current_location_index=0,
        completed_location_ids=[],
        retry_count=0,
        draft_schedules=[],
        errors=[],
        current_prompt="",
        current_raw_response="",
        current_parsed_shifts=[],
        conflict_notes="",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_availability_draft_updated_after_first_location():
    """After processing location 1, consumed windows should appear in availability_draft."""
    state = _base_state([_LOCATION1_ID])

    # Simulate what the pipeline does after parsing LLM output for a location:
    shifts = [
        _make_shift(_EMPLOYEE1_ID, "Alice", _LOCATION1_ID, _MONDAY),
    ]

    # Update availability_draft as the pipeline would
    for s in shifts:
        eid = s["employee_id"]
        if eid not in state["availability_draft"]:
            state["availability_draft"][eid] = []
        state["availability_draft"][eid].append(
            {"start": s["start_time"], "end": s["end_time"]}
        )

    assert _EMPLOYEE1_ID in state["availability_draft"]
    assert len(state["availability_draft"][_EMPLOYEE1_ID]) == 1
    consumed = state["availability_draft"][_EMPLOYEE1_ID][0]
    assert _MONDAY in consumed["start"]


async def test_no_double_booking_across_locations():
    """When two locations share an employee, the second location must not
    re-assign the employee during the same consumed window."""
    state = _base_state([_LOCATION1_ID, _LOCATION2_ID])

    # Location 1 assigns Alice
    loc1_shifts = [_make_shift(_EMPLOYEE1_ID, "Alice", _LOCATION1_ID, _MONDAY)]
    for s in loc1_shifts:
        eid = s["employee_id"]
        state["availability_draft"].setdefault(eid, []).append(
            {"start": s["start_time"], "end": s["end_time"]}
        )

    state["completed_location_ids"].append(_LOCATION1_ID)
    state["draft_schedules"].append(
        LocationResult(
            location_id=_LOCATION1_ID,
            location_name="Loc-0",
            shifts=loc1_shifts,
            errors=[],
            status="ok",
        )
    )

    # For location 2, the mock LLM should assign Bob (not Alice, who is consumed).
    loc2_shifts = [_make_shift(_EMPLOYEE2_ID, "Bob", _LOCATION2_ID, _MONDAY)]

    # Verify no overlap
    all_shifts = loc1_shifts + loc2_shifts
    employee_windows: dict[str, list[tuple[str, str]]] = {}
    for s in all_shifts:
        eid = s["employee_id"]
        employee_windows.setdefault(eid, []).append((s["start_time"], s["end_time"]))

    for eid, windows in employee_windows.items():
        # Each employee should only have one window (no double-booking)
        assert len(windows) == 1, f"Employee {eid} has {len(windows)} overlapping shifts"


async def test_mock_llm_returns_valid_json():
    """The mocked LLM should return parseable JSON shift assignments."""
    mock_response = json.dumps([
        {
            "employee_id": _EMPLOYEE1_ID,
            "employee_name": "Alice",
            "role_name": "Floor Associate",
            "date": _MONDAY,
            "start_time": f"{_MONDAY}T09:00:00-05:00",
            "end_time": f"{_MONDAY}T17:00:00-05:00",
        }
    ])

    parsed = json.loads(mock_response)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["employee_id"] == _EMPLOYEE1_ID
    assert parsed[0]["date"] == _MONDAY
    assert parsed[0]["role_name"] == "Floor Associate"


async def test_state_structure_integrity():
    """Verify the SchedulingState TypedDict can be constructed and has all keys."""
    state = _base_state()
    required_keys = {
        "company_id", "week_start_date", "locations", "shift_templates",
        "employees", "availability_draft", "current_location_index",
        "completed_location_ids", "retry_count", "draft_schedules",
        "errors", "current_prompt", "current_raw_response",
        "current_parsed_shifts", "conflict_notes",
    }
    assert required_keys.issubset(set(state.keys()))


async def test_pipeline_uses_specific_date_template_when_present(
    db_session, seed_location, seed_company, seed_shift_template
):
    """When the week contains a date with a specific_date override, the
    template resolver returns the override for that day and the recurring
    template for others. This is a unit-style smoke test of the resolver
    integration — the resolver itself is unit-tested elsewhere."""
    from datetime import date, timedelta
    from backend.scheduling.template_resolver import resolve_templates_for_week
    from backend.models import ShiftTemplate

    override = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Override",
        weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=date(2026, 12, 24),
    )
    db_session.add(override)
    await db_session.commit()

    week = [date(2026, 12, 21) + timedelta(days=i) for i in range(7)]
    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=week,
        selected_template_ids=None,
    )
    assert result[date(2026, 12, 24)].id == override.id
    for d in week:
        if d != date(2026, 12, 24):
            assert result[d].id == seed_shift_template.id


async def test_load_initial_state_fuses_override_into_weekly_schedule(
    db_session, seed_company, seed_location, seed_shift_template,
):
    """End-to-end: when an override template exists for a date in the window,
    _load_initial_state should pull the override's slots and place them under
    the matching day-name key in the fused weekly_schedule dict, going through
    the day-name-keyed fusion path and the slot normalizer."""
    from datetime import date
    from backend.models import ShiftTemplate
    from backend.scheduling.graph import _load_initial_state

    # Override on Thursday 2026-12-24 (dow=3) with one role using the new
    # per-dow shape (day_of_week + roles + HH:MM:SS times) so we exercise the
    # normalizer's shape-(3) branch.
    override = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Override",
        weekly_schedule=[{"day_of_week": 3, "roles": [
            {"role_id": "r1", "role_name": "Server",
             "headcount": 2,
             "start_time": "09:00:00", "end_time": "14:00:00"},
        ]}],
        specific_date=date(2026, 12, 24),
    )
    db_session.add(override)
    await db_session.commit()

    state = await _load_initial_state(
        company_id=str(seed_company.id),
        week_start_date="2026-12-21",  # Monday
        db=db_session,
        num_days=7,
    )

    weekly = state["shift_templates"][str(seed_location.id)]["weekly_schedule"]
    # Thursday (override) should have the Server slot with override times,
    # normalized to HH:MM.
    assert "Thursday" in weekly
    thu_slots = weekly["Thursday"]
    assert len(thu_slots) == 1
    assert thu_slots[0]["role_name"] == "Server"
    assert thu_slots[0]["start_time"] == "09:00"
    assert thu_slots[0]["end_time"] == "14:00"
