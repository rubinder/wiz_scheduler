"""Regression for the non-UTC zero-shifts bug.

Availability is persisted as *local wall-clock tagged UTC* (the write paths —
employees.create_availability, import_7shifts, import_deputy — stamp the local
minute-of-day with tzinfo=UTC without converting). "Available all day" is stored
as 00:00-23:59+00:00. Template slot times are bare "HH:MM" local strings. Both
sides are therefore naive local wall-clock, so the scheduler must compare them
directly and NOT re-convert availability into the location timezone — otherwise
every window for a non-UTC location (e.g. America/Toronto, -4h) shifts off its
slot, no employee is eligible, and the pipeline emits 0 shifts.
"""

from backend.scheduling.local_scheduler import local_schedule
from backend.scheduling.nodes import validate_schedule


def _state_for_toronto_all_day_monday():
    """One employee available all day Monday (stored local-tagged-UTC), one
    Monday 16:45-23:00 dinner slot, at an America/Toronto location."""
    return {
        "week_start_date": "2026-06-29",  # Monday
        "num_days": 7,
        "availability_draft": {},
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "current_location": {
            "id": "loc1",
            "name": "Raku Soho",
            "timezone": "America/Toronto",
        },
        "current_shift_template": {
            "weekly_schedule": {
                "Monday": [{
                    "role_name": "1 Server",
                    "role_id": "r1",
                    "start_time": "16:45",
                    "end_time": "23:00",
                    "headcount": 1,
                }],
            },
        },
        "current_employees": [{
            "id": "e1",
            "full_name": "Server A",
            "roles": [{"role_name": "1 Server", "role_id": "r1", "skill_level": 3}],
            "available_windows": [{
                "start": "2026-06-29T00:00:00+00:00",
                "end": "2026-06-29T23:59:00+00:00",
            }],
            "affinities": [],
            "day_blackouts": [],
            "max_hours_per_week": None,
        }],
    }


def test_local_schedule_assigns_all_day_employee_at_non_utc_location():
    state = _state_for_toronto_all_day_monday()
    result = local_schedule(state, strategy="rotation")
    shifts = result["current_parsed_shifts"]
    assert len(shifts) == 1
    assert shifts[0]["employee_id"] == "e1"
    assert shifts[0]["role_name"] == "1 Server"
    assert shifts[0]["date"] == "2026-06-29"


def test_validate_schedule_keeps_all_day_employee_shift_non_utc():
    """The second-pass validator must not drop the assigned shift as
    'not available' — the availability window and shift are the same naive
    wall-clock and must be compared without timezone conversion."""
    state = _state_for_toronto_all_day_monday()
    gen = local_schedule(state, strategy="rotation")
    state["current_parsed_shifts"] = gen["current_parsed_shifts"]

    out = validate_schedule(state)
    shifts = out["current_parsed_shifts"]
    ok = [s for s in shifts if s.get("status") == "ok" and s.get("employee_id") == "e1"]
    assert len(ok) == 1
    vacant = [s for s in shifts if s.get("employee_id") == "VACANT"]
    assert vacant == []
