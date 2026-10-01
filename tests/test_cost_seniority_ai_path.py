"""Cost, seniority, and overtime reach the AI path via eligible_for_slot's
attached scores (Task 3) plus build_schedule_prompt's rendering (#134)."""
from datetime import date

from backend.scheduling.prompts import build_schedule_prompt

LOCATION = {"id": "loc1", "name": "Test Cafe", "timezone": "America/New_York"}
MON_9_17 = {"start": "2026-08-31T09:00:00+00:00", "end": "2026-08-31T17:00:00+00:00"}

SHIFT_TEMPLATE = {
    "id": "tmpl1",
    "name": "T",
    "weekly_schedule": {
        "Monday": [
            {"role_name": "Floor", "role_id": "role1",
             "start_time": "09:00", "end_time": "17:00", "headcount": 1}
        ],
    },
}


def _employee(eid, **overrides):
    base = {
        "id": eid,
        "full_name": eid,
        "email": f"{eid}@test.com",
        "location_ids": ["loc1"],
        "roles": [{"role_name": "Floor", "role_id": "role1", "skill_level": 1}],
        "affinities": [],
        "available_windows": [MON_9_17],
        "day_blackouts": [],
        "day_preferences": [],
        "hour_range_preferences": [],
        "hour_range_caps": [],
        "pay_rate": None,
        "hire_date": None,
        "seniority_rank": None,
    }
    base.update(overrides)
    return base


def _eligible_line(prompt: str) -> str:
    return next(line for line in prompt.splitlines() if line.strip().startswith("Eligible:"))


def test_no_data_configured_prompt_unchanged():
    employees = [_employee("e1"), _employee("e2")]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    assert "cost=" not in prompt
    assert "seniority" not in prompt
    assert "hours_committed" not in prompt


def test_cheaper_candidate_ordered_first_and_shown():
    employees = [_employee("e_pricey", pay_rate=30.0), _employee("e_cheap", pay_rate=10.0)]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    line = _eligible_line(prompt)
    assert line.index("e_cheap") < line.index("e_pricey")
    assert "cost=" in prompt


def test_more_senior_candidate_ordered_first_via_hire_date():
    employees = [
        _employee("e_junior", hire_date=date(2024, 1, 1)),
        _employee("e_senior", hire_date=date(2020, 1, 1)),
    ]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    line = _eligible_line(prompt)
    assert line.index("e_senior") < line.index("e_junior")
    assert "seniority_rank: 1" in prompt


def test_overtime_pushes_committed_employee_later_and_shows_hours():
    # Overtime scoring is opt-in: it must be explicitly configured via
    # overtime_threshold_hours, not inferred from the mere presence of
    # committed hours (see test_overtime_disabled_when_no_threshold_configured
    # below for the no-op guarantee this protects).
    location_with_threshold = {**LOCATION, "overtime_threshold_hours": 40.0}
    employees = [_employee("e_committed"), _employee("e_fresh")]
    prompt = build_schedule_prompt(
        location_with_threshold, SHIFT_TEMPLATE, employees, "2026-08-31",
        employee_hours_committed={"e_committed": 38.0},
    )
    line = _eligible_line(prompt)
    assert line.index("e_fresh") < line.index("e_committed")
    assert "hours_committed=38.0" in prompt


def test_overtime_disabled_when_no_threshold_configured():
    """Regression: overtime scoring must be truly inert (byte-identical
    prompt) when no overtime_threshold_hours is configured anywhere, even
    when an employee has committed hours well past 40 this run. Previously
    the threshold silently defaulted to 40h, so any tenant who never
    configured overtime still got overtime-driven reordering/rendering."""
    employees = [_employee("e_committed"), _employee("e_fresh")]
    prompt_with_hours = build_schedule_prompt(
        LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31",
        employee_hours_committed={"e_committed": 45.0},
    )
    prompt_without_hours = build_schedule_prompt(
        LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31",
        employee_hours_committed=None,
    )
    assert prompt_with_hours == prompt_without_hours
