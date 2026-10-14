"""Conversion at the scheduling boundary: only for an employee with a timezone."""

from copy import deepcopy

from backend.scheduling.employee_time import needs_conversion
from backend.scheduling.local_scheduler import local_schedule
from backend.scheduling.nodes import (
    _filter_availability_against_draft,
    load_location_context,
)

NY, LA = "America/New_York", "America/Los_Angeles"
MON = "2026-03-02"
LOC_NY = {"id": "locny001", "name": "NY", "timezone": NY}
LOC_LA = {"id": "locla001", "name": "LA", "timezone": LA}
ROLE = {"role_id": "role0001", "role_name": "Floor", "skill_level": 3}


def _template(loc_id: str, start: str, end: str) -> dict:
    return {
        "id": f"t-{loc_id}", "name": "T", "location_id": loc_id,
        "weekly_schedule": {"Monday": [{
            "role_name": "Floor", "role_id": "role0001", "headcount": 1,
            "start_time": start, "end_time": end,
        }]},
    }


def _emp(timezone: str | None, windows: list[dict], blackouts=None) -> dict:
    return {
        "id": "e001", "full_name": "A", "email": "a@test.com",
        "location_ids": ["locny001", "locla001"], "roles": [ROLE],
        "affinities": [], "available_windows": windows,
        "day_blackouts": blackouts or [], "timezone": timezone,
    }


def _state(emp: dict, location_index: int, templates: dict) -> dict:
    locations = [LOC_NY, LOC_LA]
    return {
        "company_id": "comp0001", "week_start_date": MON, "num_days": 7,
        "locations": locations, "shift_templates": templates,
        "employees": [emp], "availability_draft": {},
        "current_location_index": location_index, "completed_location_ids": [],
        "retry_count": 0, "draft_schedules": [], "errors": [],
        "current_prompt": "", "current_raw_response": "",
        "current_parsed_shifts": [], "conflict_notes": "",
        "total_input_tokens": 0, "total_output_tokens": 0,
        "current_location": locations[location_index],
        "current_shift_template": {}, "current_employees": [],
        "failure_entries": [], "role_equivalents": {},
    }


NY_9_17 = {"start": f"{MON}T09:00:00+00:00", "end": f"{MON}T17:00:00+00:00"}


def test_no_timezone_at_two_zones_is_unchanged():
    emp = _emp(None, [NY_9_17])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "09:00", "17:00")}))
    assert ctx["current_employees"][0]["available_windows"] == [NY_9_17]


def test_with_a_timezone_the_location_sees_converted_windows():
    emp = _emp(NY, [NY_9_17])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")}))
    assert ctx["current_employees"][0]["available_windows"] == [
        {"start": f"{MON}T06:00:00+00:00", "end": f"{MON}T14:00:00+00:00"},
    ]


def test_conversion_never_leaks_into_the_shared_employee():
    emp = _emp(NY, [NY_9_17])
    state = _state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")})
    load_location_context(state)
    assert state["employees"][0]["available_windows"] == [NY_9_17]


def test_second_location_gets_its_own_conversion_not_a_double_one():
    emp = _emp(NY, [NY_9_17])
    templates = {
        "locny001": _template("locny001", "09:00", "17:00"),
        "locla001": _template("locla001", "06:00", "14:00"),
    }
    state = _state(emp, 0, templates)
    first = load_location_context(state)
    assert first["current_employees"][0]["available_windows"] == [NY_9_17]
    state = {**state, "current_location_index": 1, "current_location": LOC_LA}
    second = load_location_context(state)
    assert second["current_employees"][0]["available_windows"] == [
        {"start": f"{MON}T06:00:00+00:00", "end": f"{MON}T14:00:00+00:00"},
    ]


def test_blackouts_are_converted_for_the_location():
    emp = _emp(NY, [NY_9_17], blackouts=[{"day": "Monday", "start": "14:00", "end": "18:00"}])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")}))
    assert ctx["current_employees"][0]["day_blackouts"] == [
        {"day": "Monday", "start": "11:00", "end": "15:00"},
    ]


def test_draft_spans_are_subtracted_on_the_employees_clock():
    # Booked 09:00-13:00 at the NY store (real offset -05:00). For an employee
    # on NY time that consumes 09:00-13:00 of their NY-clock availability.
    emp = _emp(NY, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T09:00:00-05:00", "end": f"{MON}T13:00:00-05:00"}]}
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T13:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_draft_spans_from_another_zone_are_converted_before_subtracting():
    # Booked 06:00-10:00 at the LA store (-08:00) = 09:00-13:00 NY time.
    emp = _emp(NY, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T06:00:00-08:00", "end": f"{MON}T10:00:00-08:00"}]}
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T13:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_draft_without_timezone_keeps_face_subtraction():
    emp = _emp(None, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T06:00:00-08:00", "end": f"{MON}T10:00:00-08:00"}]}
    # Face subtraction (today's behaviour): 06:00-10:00 carved from 09:00-17:00.
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T10:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_local_scheduler_output_identical_for_none_and_same_zone():
    templates = {"locny001": _template("locny001", "09:00", "17:00")}

    def run(tz):
        state = _state(_emp(tz, [NY_9_17]), 0, templates)
        ctx = load_location_context(state)
        state = {**state, **ctx}
        return local_schedule(deepcopy(state), strategy="random")["current_parsed_shifts"]

    assert run(None) == run(NY)


def test_draft_with_invalid_timezone_matches_no_timezone():
    draft = {"e001": [{"start": f"{MON}T06:00:00-08:00", "end": f"{MON}T10:00:00-08:00"}]}
    assert _filter_availability_against_draft(
        _emp("Not/AZone", [NY_9_17]), draft
    ) == _filter_availability_against_draft(_emp(None, [NY_9_17]), draft)


def test_tzdata_directory_name_does_not_raise(monkeypatch):
    def boom(name):
        raise IsADirectoryError(name)

    monkeypatch.setattr("backend.scheduling.employee_time.ZoneInfo", boom)
    assert needs_conversion("America", LA) is False
    emp = _emp("America", [NY_9_17])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")}))
    assert ctx["current_employees"][0]["available_windows"] == [NY_9_17]
