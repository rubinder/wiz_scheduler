"""Deputy locations get Deputy's zone, else the importer's browser zone."""

from backend.routers.import_deputy import _deputy_location_timezone


def test_uses_a_zone_carried_by_the_payload():
    assert _deputy_location_timezone({"Timezone": "America/Chicago"}, "America/Denver") == "America/Chicago"
    assert _deputy_location_timezone(
        {"_DPMetaData": {"Timezone": "Australia/Sydney"}}, None,
    ) == "Australia/Sydney"


def test_falls_back_to_the_callers_zone():
    assert _deputy_location_timezone({}, "America/Denver") == "America/Denver"


def test_ignores_invalid_values():
    assert _deputy_location_timezone({"Timezone": "AEST"}, "Not/AZone") == "America/New_York"


def test_last_resort_keeps_the_previous_default():
    assert _deputy_location_timezone({}, None) == "America/New_York"
