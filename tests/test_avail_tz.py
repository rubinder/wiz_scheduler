"""Regression test for the UTC→local timezone conversion in
_parse_avail_by_day. Pre-fix, availability windows stored as UTC
timestamps were compared against template slot times in local wall-clock,
producing zero eligible employees for any non-UTC location."""

from backend.scheduling.prompts import _parse_avail_by_day, _time_covers


DATE_TO_DAY = {
    "2026-12-21": "Monday",
    "2026-12-22": "Tuesday",
    "2026-12-23": "Wednesday",
    "2026-12-24": "Thursday",
    "2026-12-25": "Friday",
    "2026-12-26": "Saturday",
    "2026-12-27": "Sunday",
}


def test_parse_avail_converts_utc_to_location_local():
    """An employee available 09:00–17:00 New York is stored as 14:00–22:00 UTC.
    With timezone conversion, the parsed window is 09:00–17:00 local — which
    is what the scheduler compares against template slot times."""
    windows = [{
        "start": "2026-12-21T14:00:00+00:00",
        "end": "2026-12-21T22:00:00+00:00",
    }]
    result = _parse_avail_by_day(windows, DATE_TO_DAY, location_tz="America/New_York")
    assert result == {"Monday": [("09:00", "17:00")]}


def test_parse_avail_no_tz_falls_back_to_naive_extract():
    """Without a location_tz, behave as before — emit the raw HH:MM from the
    timestamp's own tz (UTC). This preserves backwards compatibility for any
    caller not yet threading the timezone."""
    windows = [{
        "start": "2026-12-21T14:00:00+00:00",
        "end": "2026-12-21T22:00:00+00:00",
    }]
    result = _parse_avail_by_day(windows, DATE_TO_DAY)
    assert result == {"Monday": [("14:00", "22:00")]}


def test_local_avail_covers_local_slot_after_conversion():
    """End-to-end: a UTC-stored window converts to local, _time_covers
    matches a local-wall-clock slot, eligibility succeeds."""
    windows = [{
        "start": "2026-12-21T14:00:00+00:00",
        "end": "2026-12-21T22:00:00+00:00",
    }]
    day_windows = _parse_avail_by_day(windows, DATE_TO_DAY, location_tz="America/New_York")
    ranges = day_windows["Monday"]
    assert _time_covers(ranges, "09:00", "17:00") is True


def test_local_avail_does_NOT_cover_when_outside_window():
    windows = [{
        "start": "2026-12-21T14:00:00+00:00",  # 09:00 EST
        "end": "2026-12-21T18:00:00+00:00",    # 13:00 EST
    }]
    day_windows = _parse_avail_by_day(windows, DATE_TO_DAY, location_tz="America/New_York")
    # Slot 09:00-17:00 — only first 4 hours covered → no.
    assert _time_covers(day_windows["Monday"], "09:00", "17:00") is False
    # Slot 10:00-13:00 (override) — fully inside → yes.
    assert _time_covers(day_windows["Monday"], "10:00", "13:00") is True


def test_parse_avail_handles_dst_boundary_correctly():
    """In December America/New_York is EST (UTC-5). In March DST may apply
    (EDT, UTC-4). _parse_avail_by_day should respect the date's actual offset
    when converting from UTC."""
    # 2026-07-15 is in EDT (UTC-4). 13:00 UTC = 09:00 EDT.
    windows = [{
        "start": "2026-07-15T13:00:00+00:00",
        "end": "2026-07-15T21:00:00+00:00",
    }]
    date_to_day = {"2026-07-15": "Wednesday"}
    result = _parse_avail_by_day(windows, date_to_day, location_tz="America/New_York")
    assert result == {"Wednesday": [("09:00", "17:00")]}
