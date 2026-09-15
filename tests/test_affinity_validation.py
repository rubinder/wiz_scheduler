"""Tests for validate_schedule's hard-negative affinity enforcement (check 8).

A -1.0 affinity between two employees (in either direction) is a hard rule:
they must never end up on overlapping shift windows on the same date at the
same location. Only shifts already accepted in this validation pass count as
coworkers — a shift dropped by an earlier check is not a coworker.
"""

from backend.scheduling.nodes import validate_schedule
from backend.scheduling.state import SchedulingState

TZ = "-04:00"  # America/New_York in late March
ROLE_ID = "role0001"
LOC_ID = "loc00001"


def _win(date: str, start_h: int, end_h: int):
    return {"start": f"{date}T{start_h:02d}:00:00{TZ}", "end": f"{date}T{end_h:02d}:00:00{TZ}"}


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


def _emp(eid: str, windows: list[dict], affinities: list[dict] | None = None):
    return {
        "id": eid,
        "full_name": "Emp",
        "roles": [{"role_name": "Floor Associate", "role_id": ROLE_ID, "skill_level": 3}],
        "affinities": affinities or [],
        "available_windows": windows,
    }


def _validate_state(shifts, employees):
    return SchedulingState(
        company_id="comp0001",
        week_start_date="2026-03-30",
        locations=[{"id": LOC_ID, "name": "L", "timezone": "America/New_York"}],
        shift_templates={LOC_ID: {"weekly_schedule": []}},
        employees=employees,
        availability_draft={},
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
        current_location={"id": LOC_ID, "name": "L", "timezone": "America/New_York"},
        current_shift_template={LOC_ID: {"weekly_schedule": []}},
        current_employees=employees,
        failure_entries=[],
        role_equivalents={},
        num_days=7,
        employee_weekly_hours_draft={},
    )


def _valid_ids(result_shifts):
    return {(s["employee_id"], s["date"]) for s in result_shifts if s["status"] == "ok"}


def _warning_text(result):
    texts: list[str] = []
    for fe in result.get("failure_entries", []):
        detail = fe.get("detail", {})
        texts.extend(detail.get("warnings", []))
    return " ".join(texts)


class TestAffinityHardNegative:
    def test_forward_negative_drops_second_shift(self):
        avail = [_win("2026-03-30", 9, 17)]
        e1 = _emp("e1", avail, affinities=[{"target_id": "e2", "level": -1.0}])
        e2 = _emp("e2", avail, affinities=[])
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e2", "2026-03-30", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e1, e2]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok      # first in list order: kept
        assert ("e2", "2026-03-30") not in ok  # second: dropped
        assert "cannot work together" in _warning_text(result)

    def test_reverse_negative_drops_second_shift(self):
        avail = [_win("2026-03-30", 9, 17)]
        e1 = _emp("e1", avail, affinities=[])
        e2 = _emp("e2", avail, affinities=[{"target_id": "e1", "level": -1.0}])
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e2", "2026-03-30", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e1, e2]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e2", "2026-03-30") not in ok
        assert "cannot work together" in _warning_text(result)

    def test_soft_negative_keeps_both(self):
        avail = [_win("2026-03-30", 9, 17)]
        e1 = _emp("e1", avail, affinities=[{"target_id": "e2", "level": -0.5}])
        e2 = _emp("e2", avail, affinities=[])
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e2", "2026-03-30", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e1, e2]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e2", "2026-03-30") in ok
        assert "cannot work together" not in _warning_text(result)

    def test_positive_affinity_keeps_both(self):
        avail = [_win("2026-03-30", 9, 17)]
        e1 = _emp("e1", avail, affinities=[{"target_id": "e2", "level": 1.0}])
        e2 = _emp("e2", avail, affinities=[])
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e2", "2026-03-30", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e1, e2]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e2", "2026-03-30") in ok
        assert "cannot work together" not in _warning_text(result)

    def test_different_dates_not_flagged(self):
        avail = [_win("2026-03-30", 9, 17), _win("2026-03-31", 9, 17)]
        e1 = _emp("e1", avail, affinities=[{"target_id": "e2", "level": -1.0}])
        e2 = _emp("e2", avail, affinities=[])
        shifts = [_shift("e1", "2026-03-30", 9, 17), _shift("e2", "2026-03-31", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e1, e2]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e2", "2026-03-31") in ok
        assert "cannot work together" not in _warning_text(result)

    def test_dropped_shift_is_not_a_coworker(self):
        # e2 has no availability window, so its shift is dropped by check 3
        # before the affinity check ever sees it as a coworker. e1's shift
        # must survive even though it has a -1.0 toward e2.
        avail_e1 = [_win("2026-03-30", 9, 17)]
        e1 = _emp("e1", avail_e1, affinities=[{"target_id": "e2", "level": -1.0}])
        e2 = _emp("e2", windows=[], affinities=[])
        # e2's shift listed first so it is processed (and dropped) before e1's.
        shifts = [_shift("e2", "2026-03-30", 9, 17), _shift("e1", "2026-03-30", 9, 17)]
        result = validate_schedule(_validate_state(shifts, [e2, e1]))
        ok = _valid_ids(result["current_parsed_shifts"])
        assert ("e1", "2026-03-30") in ok
        assert ("e2", "2026-03-30") not in ok
        assert "cannot work together" not in _warning_text(result)
