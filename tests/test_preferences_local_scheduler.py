"""Preferences change the deterministic scheduler only when configured.

test_zero_preferences_is_a_no_op is the single most important test in this
feature. _pick_employee is on the path of all four strategies, so if adding a
scoring term perturbs the no-preference case, every existing schedule changes
silently.
"""

import pytest

from backend.scheduling.local_scheduler import local_schedule
from backend.scheduling.preferences import blocked_by_hard_preference
from backend.scheduling.prompts import eligible_for_slot
from backend.scheduling.state import SchedulingState


def _emp(eid, day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": eid,
        "_role_names": {"Cook"},
        "_day_windows": {"Monday": [("00:00", "23:59")]},
        "roles": [{"role_name": "Cook", "skill_level": 3}],
        "day_blackouts": [],
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_zero_preferences_is_a_no_op():
    """With no preferences, the eligible set is exactly what it was before."""
    prepared = [_emp("e1"), _emp("e2")]
    without = eligible_for_slot(prepared, "Monday", "Cook", "09:00", "17:00")
    with_args = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in without] == ["e1", "e2"]
    assert [e["id"] for e in with_args] == ["e1", "e2"]


def test_hard_day_preference_removes_the_candidate():
    prepared = [
        _emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}]),  # Monday only
        _emp("e2"),
    ]
    # Monday is day_index 0 -> e1 stays
    monday = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in monday] == ["e1", "e2"]
    # Tuesday -> e1 is filtered out entirely
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    prepared[1]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    tuesday = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in tuesday] == ["e2"]


def test_a_slot_can_lose_every_candidate():
    """This is what produces a VACANT shift, and it must not raise."""
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    assert eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    ) == []


def test_soft_preference_does_not_remove_the_candidate():
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 0.9}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    out = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in out] == ["e1"]


# ---------------------------------------------------------------------------
# Weekly hour-range cap — enforced live in _pick_employee, not eligible_for_slot
#
# eligible_for_slot's hard filter runs once per (day, role) slot, before any
# assignments exist, so range_counts is always {} at that point — it can
# never see a cap tick over. The live, incrementally-updated range_counts
# only exists inside local_schedule's assignment loop, so these tests drive
# the real local_schedule() end-to-end (not eligible_for_slot in isolation)
# to prove the cap actually binds there.
# ---------------------------------------------------------------------------

ROLE_FLOOR = {"role_id": "role0001", "role_name": "Floor", "skill_level": 3}

# Employee availability windows are "local wall-clock time tagged UTC",
# matching the write-path contract used throughout the scheduling tests.
_MON_9_17 = {"start": "2026-03-30T09:00:00+00:00", "end": "2026-03-30T17:00:00+00:00"}
_TUE_9_17 = {"start": "2026-03-31T09:00:00+00:00", "end": "2026-03-31T17:00:00+00:00"}
_WED_9_17 = {"start": "2026-04-01T09:00:00+00:00", "end": "2026-04-01T17:00:00+00:00"}

# Three identical Floor slots, one per day, all in the same hour range a cap
# can target. headcount=1 each -> 3 opportunities to assign e1 in a week.
_THREE_DAY_SCHEDULE = {
    "Monday": [{"role_name": "Floor", "role_id": "role0001", "start_time": "09:00", "end_time": "17:00", "headcount": 1}],
    "Tuesday": [{"role_name": "Floor", "role_id": "role0001", "start_time": "09:00", "end_time": "17:00", "headcount": 1}],
    "Wednesday": [{"role_name": "Floor", "role_id": "role0001", "start_time": "09:00", "end_time": "17:00", "headcount": 1}],
}


def _cap_employee(weight: float, max_per_week: int = 1) -> dict:
    return {
        "id": "e1",
        "full_name": "Alice Cap",
        "email": "alice@test.com",
        "location_ids": ["loc00001"],
        "roles": [ROLE_FLOOR],
        "affinities": [],
        "available_windows": [_MON_9_17, _TUE_9_17, _WED_9_17],
        "day_blackouts": [],
        "day_preferences": [],
        "hour_range_preferences": [],
        "hour_range_caps": [
            {"start_time": "09:00", "end_time": "17:00", "max_per_week": max_per_week, "weight": weight}
        ],
    }


def _cap_test_state(employees: list[dict]) -> SchedulingState:
    location = {"id": "loc00001", "name": "Test Location", "timezone": "UTC"}
    shift_template = {
        "id": "tmpl0001",
        "name": "Test Template",
        "location_id": "loc00001",
        "weekly_schedule": _THREE_DAY_SCHEDULE,
    }
    return {
        "company_id": "comp0001",
        "week_start_date": "2026-03-30",  # Monday
        "locations": [location],
        "shift_templates": {"loc00001": shift_template},
        "employees": employees,
        "availability_draft": {},
        "current_location_index": 0,
        "completed_location_ids": [],
        "retry_count": 0,
        "draft_schedules": [],
        "errors": [],
        "current_prompt": "",
        "current_raw_response": "",
        "current_parsed_shifts": [],
        "conflict_notes": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "current_location": location,
        "current_shift_template": shift_template,
        "current_employees": employees,
        "failure_entries": [],
        "role_equivalents": {},
    }


def test_hard_cap_limits_assignments_across_the_week():
    """A weight-1.0 cap of 1 caps e1 at one Floor shift in this hour range,
    even though e1 is the only eligible candidate for all three slots the
    template asks for."""
    e1 = _cap_employee(weight=1.0, max_per_week=1)
    state = _cap_test_state([e1])
    result = local_schedule(state, strategy="random")
    shifts = result["current_parsed_shifts"]
    assert len(shifts) == 1
    assert shifts[0]["employee_id"] == "e1"
    assert shifts[0]["date"] == "2026-03-30"  # Monday only


def test_hard_cap_surplus_slot_is_vacant_and_does_not_raise():
    """The two slots beyond the cap come out VACANT (absent from the
    generated shifts) rather than assigned to e1, and local_schedule
    completes without raising."""
    e1 = _cap_employee(weight=1.0, max_per_week=1)
    state = _cap_test_state([e1])
    result = local_schedule(state, strategy="random")  # must not raise
    shifts = result["current_parsed_shifts"]
    filled_dates = {s["date"] for s in shifts}
    assert "2026-03-31" not in filled_dates  # Tuesday: VACANT
    assert "2026-04-01" not in filled_dates  # Wednesday: VACANT


def test_soft_cap_does_not_hard_block():
    """A weight-0.9 cap of 1 is a soft preference: e1 may still be assigned
    beyond the cap because there is no alternative candidate to prefer
    instead — only a weight-1.0 cap removes the candidate outright."""
    e1 = _cap_employee(weight=0.9, max_per_week=1)
    state = _cap_test_state([e1])
    result = local_schedule(state, strategy="random")
    shifts = result["current_parsed_shifts"]
    assert len(shifts) == 3
    assert all(s["employee_id"] == "e1" for s in shifts)


def test_no_cap_configured_is_unaffected():
    """An employee with no hour_range_caps at all is scheduled normally."""
    e1 = _cap_employee(weight=1.0, max_per_week=1)
    e1["hour_range_caps"] = []
    state = _cap_test_state([e1])
    result = local_schedule(state, strategy="random")
    shifts = result["current_parsed_shifts"]
    assert len(shifts) == 3
