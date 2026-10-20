"""Open-entry helpers (#174): which draft entries are open shifts."""

from backend.scheduling.open_shifts import OPEN_STATUS, is_open_entry, to_open_entry


def test_open_status_is_open():
    assert is_open_entry({"status": "OPEN", "employee_id": None})


def test_null_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": None})


def test_empty_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": ""})


def test_legacy_vacant_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": "VACANT"})


def test_legacy_vacant_status_is_open_even_naming_a_real_employee():
    # A cap trim from before the trim reset employee_id left this shape.
    assert is_open_entry({"status": "VACANT", "employee_id": "e1"})


def test_filled_shift_is_not_open():
    assert not is_open_entry({"status": "ok", "employee_id": "e1"})


def test_conflict_is_never_open():
    assert not is_open_entry({"status": "CONFLICT", "employee_id": "VACANT"})


def test_to_open_entry_clears_the_person_and_keeps_the_slot():
    entry = {
        "employee_id": "VACANT", "employee_name": "VACANT-Staffing Shortage",
        "role_id": "r1", "role_name": "Cook", "location_id": "l1",
        "date": "2026-08-31", "start_time": "2026-08-31T09:00:00",
        "end_time": "2026-08-31T17:00:00", "status": "VACANT",
        "preference_violations": [{"kind": "day"}],
    }
    out = to_open_entry(entry)
    assert out == {
        **entry,
        "employee_id": None,
        "employee_name": None,
        "status": OPEN_STATUS,
        "preference_violations": [],
    }
    assert entry["employee_id"] == "VACANT"  # input untouched
