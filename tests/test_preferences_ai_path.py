"""Preferences reach the AI path two ways.

Hard weights are already handled: prompts.eligible_for_slot filters them, so
the model is never offered the candidate. Soft weights order the Eligible list
so the model reads better candidates first.

The frequency cap is the one parameter that cannot be pre-filtered here — a
whole week generates in a single call, so per-slot counts do not exist
beforehand. It is enforced after generation instead.
"""

from backend.scheduling.nodes import validate_and_update_availability


def _shift(eid, date, start, end, status="ok"):
    return {
        "employee_id": eid,
        "employee_name": eid,
        "role_id": "r1",
        "role_name": "Cook",
        "location_id": "loc1",
        "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00",
        "status": status,
    }


def test_cap_violations_beyond_the_allowance_are_vacated():
    """Four 16:00-22:00 shifts against a cap of 3 -> the fourth is VACANT."""
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
        _shift("e1", "2026-09-02", "16:00", "22:00"),
        _shift("e1", "2026-09-03", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 3, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses.count("VACANT") == 1
    assert statuses[:3] == ["ok", "ok", "ok"]


def test_a_cap_within_its_allowance_vacates_nothing():
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 3, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    assert all(s["status"] == "ok" for s in out["current_parsed_shifts"])


def test_no_preferences_leaves_shifts_untouched():
    shifts = [_shift("e1", "2026-08-31", "16:00", "22:00")]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {},
    }
    out = validate_and_update_availability(state)
    assert out["current_parsed_shifts"][0]["status"] == "ok"
