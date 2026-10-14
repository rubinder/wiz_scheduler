"""Timezone helpers, and the per-date offset generated shifts carry."""

from backend.scheduling.local_scheduler import local_schedule
from backend.utils.tz import is_valid_timezone, utc_offset_on


def test_valid_and_invalid_zones():
    assert is_valid_timezone("America/New_York")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("")
    assert not is_valid_timezone(None)
    assert not is_valid_timezone("Eastern Time")
    assert not is_valid_timezone("../etc/passwd")


def test_offset_follows_the_date_not_a_fixed_one():
    assert utc_offset_on("America/New_York", "2026-01-15") == "-05:00"
    assert utc_offset_on("America/New_York", "2026-07-15") == "-04:00"
    assert utc_offset_on("Asia/Kolkata", "2026-01-15") == "+05:30"


def test_offset_on_bad_input_is_utc():
    assert utc_offset_on("Not/AZone", "2026-01-15") == "+00:00"
    assert utc_offset_on("America/New_York", "not-a-date") == "+00:00"


def _state(week_start: str) -> dict:
    location = {"id": "loc00001", "name": "L", "timezone": "America/New_York"}
    template = {
        "id": "tmpl0001", "name": "T", "location_id": "loc00001",
        "weekly_schedule": {"Monday": [{
            "role_name": "Floor", "role_id": "role0001", "headcount": 1,
            "start_time": "09:00", "end_time": "17:00",
        }]},
    }
    emp = {
        "id": "e001", "full_name": "A", "email": "a@test.com",
        "location_ids": ["loc00001"],
        "roles": [{"role_id": "role0001", "role_name": "Floor", "skill_level": 3}],
        "affinities": [],
        "available_windows": [{
            "start": f"{week_start}T09:00:00+00:00",
            "end": f"{week_start}T17:00:00+00:00",
        }],
    }
    return {
        "company_id": "comp0001", "week_start_date": week_start,
        "locations": [location], "shift_templates": {"loc00001": template},
        "employees": [emp], "availability_draft": {},
        "current_location_index": 0, "completed_location_ids": [],
        "retry_count": 0, "draft_schedules": [], "errors": [],
        "current_prompt": "", "current_raw_response": "",
        "current_parsed_shifts": [], "conflict_notes": "",
        "total_input_tokens": 0, "total_output_tokens": 0,
        "current_location": location, "current_shift_template": template,
        "current_employees": [emp], "failure_entries": [],
        "role_equivalents": {},
    }


def test_a_winter_local_shift_carries_the_winter_offset():
    shifts = local_schedule(_state("2026-01-12"), strategy="random")["current_parsed_shifts"]
    assert shifts[0]["start_time"] == "2026-01-12T09:00:00-05:00"


def test_a_summer_local_shift_carries_the_summer_offset():
    shifts = local_schedule(_state("2026-07-13"), strategy="random")["current_parsed_shifts"]
    assert shifts[0]["start_time"] == "2026-07-13T09:00:00-04:00"
