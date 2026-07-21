"""Tests for the Fair Workweek minimum-rest ("clopening") constraint.

Covers the pure helper, the local (algorithmic) scheduler's proactive
avoidance, and validate_schedule's hard enforcement — including the
cross-location path via availability_draft and the same-day split-shift
exemption.
"""

from backend.scheduling.local_scheduler import (
    _min_rest_violation,
    _rest_gap_hours,
    local_schedule,
)
from backend.scheduling.nodes import validate_schedule
from backend.scheduling.state import SchedulingState

TZ = "-04:00"  # America/New_York in late March
ROLE_ID = "role0001"
LOC_ID = "loc00001"


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------

def _win(date: str, start_h: int, end_h: int, end_date: str | None = None):
    ed = end_date or date
    return {"start": f"{date}T{start_h:02d}:00:00{TZ}", "end": f"{ed}T{end_h:02d}:00:00{TZ}"}


class TestRestGapHours:
    def test_gap_between_consecutive_day_shifts(self):
        # Mon 15:00-23:00 close, Tue 07:00-15:00 open → 8h rest
        gap = _rest_gap_hours(
            "2026-03-31T07:00:00-04:00", "2026-03-31T15:00:00-04:00",
            "2026-03-30T15:00:00-04:00", "2026-03-30T23:00:00-04:00",
        )
        assert round(gap, 2) == 8.0

    def test_overlap_is_negative(self):
        gap = _rest_gap_hours(
            "2026-03-30T09:00:00-04:00", "2026-03-30T17:00:00-04:00",
            "2026-03-30T12:00:00-04:00", "2026-03-30T20:00:00-04:00",
        )
        assert gap < 0


class TestMinRestViolation:
    def test_clopening_8h_violates_11h_rule(self):
        prev = [_win("2026-03-30", 15, 23)]  # Mon close 23:00
        assert _min_rest_violation(
            f"2026-03-31T07:00:00{TZ}", f"2026-03-31T15:00:00{TZ}", prev, 11
        ) is True

    def test_exactly_11h_is_allowed(self):
        prev = [_win("2026-03-30", 15, 23)]  # Mon close 23:00
        # Tue 10:00 → exactly 11h rest
        assert _min_rest_violation(
            f"2026-03-31T10:00:00{TZ}", f"2026-03-31T18:00:00{TZ}", prev, 11
        ) is False

    def test_12h_rest_allowed(self):
        prev = [_win("2026-03-30", 15, 23)]
        assert _min_rest_violation(
            f"2026-03-31T11:00:00{TZ}", f"2026-03-31T19:00:00{TZ}", prev, 11
        ) is False

    def test_same_day_split_shift_exempt(self):
        # Mon 09:00-12:00 then Mon 14:00-22:00 → 2h gap but same day → OK
        prev = [_win("2026-03-30", 9, 12)]
        assert _min_rest_violation(
            f"2026-03-30T14:00:00{TZ}", f"2026-03-30T22:00:00{TZ}", prev, 11
        ) is False

    def test_none_disables_check(self):
        prev = [_win("2026-03-30", 15, 23)]
        assert _min_rest_violation(
            f"2026-03-31T07:00:00{TZ}", f"2026-03-31T15:00:00{TZ}", prev, None
        ) is False

    def test_far_apart_days_ok(self):
        prev = [_win("2026-03-30", 9, 17)]  # Mon
        assert _min_rest_violation(  # Wed, plenty of rest
            f"2026-04-01T09:00:00{TZ}", f"2026-04-01T17:00:00{TZ}", prev, 11
        ) is False


# ---------------------------------------------------------------------------
# validate_schedule hard enforcement
# ---------------------------------------------------------------------------

def _shift(eid: str, date: str, start_h: int, end_h: int):
    return {
        "employee_id": eid,
        "employee_name": "Emp",
        "role_id": ROLE_ID,
        "role_name": "Floor Associate",
        "location_id": LOC_ID,
        "date": date,
        "start_time": f"{date}T{start_h:02d}:00:00{TZ}",
        "end_time": f"{date}T{end_h:02d}:00:00{TZ}",
        "status": "ok",
    }


def _emp_with_windows(eid: str, windows: list[dict]):
    return {
        "id": eid,
        "full_name": "Emp",
        "roles": [{"role_name": "Floor Associate", "role_id": ROLE_ID, "skill_level": 3}],
        "affinities": [],
        "available_windows": windows,
    }


def _validate_state(shifts, employees, *, min_rest_hours, availability_draft=None):
    # Availability windows must cover the shifts (validate checks availability).
    return SchedulingState(
        company_id="comp0001",
        week_start_date="2026-03-30",
        locations=[{"id": LOC_ID, "name": "L", "timezone": "America/New_York",
                    "min_rest_hours": min_rest_hours}],
        shift_templates={LOC_ID: {"weekly_schedule": []}},
        employees=employees,
        availability_draft=availability_draft or {},
        current_location_index=0,
        completed_location_ids=[],
        retry_count=0,
        draft_schedules=[],
        errors=[],
        current_prompt="",
        current_raw_response="",
        current_parsed_shifts=shifts,
        conflict_notes="",
        total_input_tokens=0,
        total_output_tokens=0,
        current_location={"id": LOC_ID, "name": "L", "timezone": "America/New_York",
                          "min_rest_hours": min_rest_hours},
        current_shift_template={LOC_ID: {"weekly_schedule": []}},
        current_employees=employees,
        failure_entries=[],
        role_equivalents={},
        num_days=7,
        employee_weekly_hours_draft={},
    )


def _valid_ids(result_shifts):
    return {(s["employee_id"], s["date"]) for s in result_shifts if s["status"] == "ok"}


class TestValidateScheduleClopening:
    def test_clopening_dropped_when_rule_set(self):
        # Mon close 15-23, Tue open 07-15 → 8h rest < 11 → Tue shift dropped
        avail = [_win("2026-03-30", 15, 23), _win("2026-03-31", 7, 15)]
        emp = _emp_with_windows("e1", avail)
        shifts = [_shift("e1", "2026-03-30", 15, 23), _shift("e1", "2026-03-31", 7, 15)]
        result = validate_schedule(_validate_state(shifts, [emp], min_rest_hours=11))
        valid = result["current_parsed_shifts"]
        ok = _valid_ids(valid)
        assert ("e1", "2026-03-30") in ok        # close shift kept
        assert ("e1", "2026-03-31") not in ok    # opening clopening dropped

    def test_no_rule_keeps_both(self):
        avail = [_win("2026-03-30", 15, 23), _win("2026-03-31", 7, 15)]
        emp = _emp_with_windows("e1", avail)
        shifts = [_shift("e1", "2026-03-30", 15, 23), _shift("e1", "2026-03-31", 7, 15)]
        result = validate_schedule(_validate_state(shifts, [emp], min_rest_hours=None))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e1", "2026-03-31") in ok

    def test_sufficient_rest_keeps_both(self):
        # Mon 09-17, Tue 09-17 → 16h rest → both OK
        avail = [_win("2026-03-30", 9, 17), _win("2026-03-31", 9, 17)]
        emp = _emp_with_windows("e1", avail)
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e1", "2026-03-31", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [emp], min_rest_hours=11))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e1", "2026-03-31") in ok

    def test_cross_location_clopening_dropped(self):
        # Prior location committed a Mon close 15-23 (in availability_draft).
        # This location tries Tue open 07-15 → dropped.
        prior = {"e1": [_win("2026-03-30", 15, 23)]}
        avail = [_win("2026-03-31", 7, 15)]
        emp = _emp_with_windows("e1", avail)
        shifts = [_shift("e1", "2026-03-31", 7, 15)]
        result = validate_schedule(
            _validate_state(shifts, [emp], min_rest_hours=11, availability_draft=prior)
        )
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-31") not in ok

    def test_same_day_split_shift_not_dropped(self):
        # Two same-day shifts with a short gap must NOT be treated as clopening.
        avail = [_win("2026-03-30", 6, 11), _win("2026-03-30", 13, 22)]
        emp = _emp_with_windows("e1", avail)
        shifts = [_shift("e1", "2026-03-30", 6, 11), _shift("e1", "2026-03-30", 13, 22)]
        result = validate_schedule(_validate_state(shifts, [emp], min_rest_hours=11))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert len([s for s in result["current_parsed_shifts"] if s["status"] == "ok"]) == 2


# ---------------------------------------------------------------------------
# local_scheduler proactive avoidance
# ---------------------------------------------------------------------------

def _local_state(employees, weekly_schedule, *, min_rest_hours, availability_draft=None):
    location = {"id": LOC_ID, "name": "L", "timezone": "UTC", "min_rest_hours": min_rest_hours}
    return {
        "company_id": "comp0001",
        "week_start_date": "2026-03-30",
        "locations": [location],
        "shift_templates": {LOC_ID: {"weekly_schedule": weekly_schedule}},
        "employees": employees,
        "availability_draft": availability_draft or {},
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
        "current_shift_template": {"id": "t", "location_id": LOC_ID, "weekly_schedule": weekly_schedule},
        "current_employees": employees,
        "failure_entries": [],
        "role_equivalents": {},
        "num_days": 7,
        "employee_weekly_hours_draft": {},
    }


class TestLocalSchedulerAvoidsClopening:
    def test_prefers_non_clopening_employee(self):
        # Two-day template: Mon 15:00-23:00 (close) and Tue 07:00-15:00 (open).
        # e1 is available both; e2 only Tue with fresh rest. With an 11h rule,
        # the Tue opener must NOT be e1 (would be an 8h clopening) → e2 fills it.
        weekly = {
            "Monday": [{"role_name": "Floor Associate", "role_id": ROLE_ID,
                        "headcount": 1, "start_time": "15:00", "end_time": "23:00"}],
            "Tuesday": [{"role_name": "Floor Associate", "role_id": ROLE_ID,
                         "headcount": 1, "start_time": "07:00", "end_time": "15:00"}],
        }
        e1 = _emp_with_windows("e1", [
            {"start": "2026-03-30T15:00:00+00:00", "end": "2026-03-30T23:00:00+00:00"},
            {"start": "2026-03-31T07:00:00+00:00", "end": "2026-03-31T15:00:00+00:00"},
        ])
        e2 = _emp_with_windows("e2", [
            {"start": "2026-03-31T07:00:00+00:00", "end": "2026-03-31T15:00:00+00:00"},
        ])
        result = local_schedule(_local_state([e1, e2], weekly, min_rest_hours=11))
        shifts = result["current_parsed_shifts"]
        tue = [s for s in shifts if s["date"] == "2026-03-31"]
        assert len(tue) == 1
        assert tue[0]["employee_id"] == "e2"  # e1 avoided (clopening)

    def test_leaves_vacant_rather_than_clopening(self):
        # Only e1 exists and is available both; the Tue opener would be a
        # clopening, so it must be left unfilled rather than assigned illegally.
        weekly = {
            "Monday": [{"role_name": "Floor Associate", "role_id": ROLE_ID,
                        "headcount": 1, "start_time": "15:00", "end_time": "23:00"}],
            "Tuesday": [{"role_name": "Floor Associate", "role_id": ROLE_ID,
                         "headcount": 1, "start_time": "07:00", "end_time": "15:00"}],
        }
        e1 = _emp_with_windows("e1", [
            {"start": "2026-03-30T15:00:00+00:00", "end": "2026-03-30T23:00:00+00:00"},
            {"start": "2026-03-31T07:00:00+00:00", "end": "2026-03-31T15:00:00+00:00"},
        ])
        result = local_schedule(_local_state([e1], weekly, min_rest_hours=11))
        shifts = result["current_parsed_shifts"]
        assert any(s["date"] == "2026-03-30" for s in shifts)       # close filled
        assert not any(s["date"] == "2026-03-31" for s in shifts)   # opener left vacant
