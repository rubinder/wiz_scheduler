"""emit_result is where an unfilled slot becomes an open shift (#174).

Inside the graph it is still the VACANT placeholder, so the node-level
tests (test_preferences_ai_path.py, test_affinity_validation.py, ...) are
unchanged; only what leaves the graph changes.
"""

from backend.scheduling.nodes import emit_result


def _shift(eid, status="ok", name=None):
    return {
        "employee_id": eid, "employee_name": name or eid, "role_id": "r1",
        "role_name": "Cook", "location_id": "loc1", "date": "2026-08-31",
        "start_time": "2026-08-31T09:00:00-04:00",
        "end_time": "2026-08-31T17:00:00-04:00", "status": status,
    }


def _state(shifts):
    return {
        "current_location": {"id": "loc1", "name": "Main"},
        "current_parsed_shifts": shifts,
        "errors": [],
        "draft_schedules": [],
        "completed_location_ids": [],
        "current_location_index": 0,
        "failure_entries": [],
    }


def test_a_staffing_shortage_leaves_the_graph_as_an_open_shift():
    out = emit_result(_state([
        _shift("e1"),
        _shift("VACANT", status="VACANT", name="VACANT-Staffing Shortage"),
    ]))
    result = out["draft_schedules"][0]
    filled, open_ = result["shifts"]
    assert filled["employee_id"] == "e1" and filled["status"] == "ok"
    assert open_["employee_id"] is None
    assert open_["employee_name"] is None
    assert open_["status"] == "OPEN"
    assert (open_["role_id"], open_["date"], open_["start_time"]) == (
        "r1", "2026-08-31", "2026-08-31T09:00:00-04:00",
    )
    assert result["open_count"] == 1
    assert result["status"] == "ok"


def test_a_cap_trimmed_shift_is_open_and_no_longer_names_anyone():
    out = emit_result(_state([
        _shift("VACANT", status="VACANT", name="VACANT-Frequency Cap"),
        _shift("VACANT", status="VACANT", name="VACANT-Preference"),
    ]))
    shifts = out["draft_schedules"][0]["shifts"]
    assert [s["status"] for s in shifts] == ["OPEN", "OPEN"]
    assert all(s["employee_name"] is None for s in shifts)
    assert out["draft_schedules"][0]["open_count"] == 2


def test_a_conflict_stays_a_conflict():
    out = emit_result(_state([_shift("e1", status="CONFLICT")]))
    result = out["draft_schedules"][0]
    assert result["shifts"][0]["status"] == "CONFLICT"
    assert result["shifts"][0]["employee_id"] == "e1"
    assert result["open_count"] == 0
    assert result["status"] == "CONFLICT"


def test_no_vacancies_means_zero():
    out = emit_result(_state([_shift("e1")]))
    assert out["draft_schedules"][0]["open_count"] == 0
