"""Contract for _parse_avail_by_day: availability is stored as *local
wall-clock tagged UTC* (the write paths stamp the local minute-of-day with a
+00:00 offset without converting), and template slot times are bare local
"HH:MM" strings. Both sides share one naive local frame, so _parse_avail_by_day
reads the date + HH:MM directly from the stored value with NO timezone
conversion.

History: a prior version (#33) tried to .astimezone() the avail window into the
location timezone, assuming it was true UTC. That shifted every window off the
(un-converted) template slots and produced zero shifts for any non-UTC location
(see tests/test_avail_local_wallclock.py for the end-to-end regression)."""

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


def test_parse_avail_reads_wallclock_without_conversion():
    """A window stored 09:00–17:00 (tagged +00:00) is read as 09:00–17:00 —
    the +00:00 is a meaningless tag on local wall-clock, not real UTC."""
    windows = [{
        "start": "2026-12-21T09:00:00+00:00",
        "end": "2026-12-21T17:00:00+00:00",
    }]
    result = _parse_avail_by_day(windows, DATE_TO_DAY)
    assert result == {"Monday": [("09:00", "17:00")]}


def test_parse_avail_keeps_window_on_its_own_date():
    """The window's date must not shift (no astimezone roll-back to the
    previous day), even for a midnight-start full-day window."""
    windows = [{
        "start": "2026-12-21T00:00:00+00:00",
        "end": "2026-12-21T23:59:00+00:00",
    }]
    result = _parse_avail_by_day(windows, DATE_TO_DAY)
    assert result == {"Monday": [("00:00", "23:59")]}


def test_local_avail_covers_matching_local_slot():
    windows = [{
        "start": "2026-12-21T09:00:00+00:00",
        "end": "2026-12-21T17:00:00+00:00",
    }]
    day_windows = _parse_avail_by_day(windows, DATE_TO_DAY)
    assert _time_covers(day_windows["Monday"], "09:00", "17:00") is True


def test_local_avail_does_not_cover_when_outside_window():
    windows = [{
        "start": "2026-12-21T09:00:00+00:00",
        "end": "2026-12-21T13:00:00+00:00",
    }]
    day_windows = _parse_avail_by_day(windows, DATE_TO_DAY)
    # Slot 09:00-17:00 — only first 4 hours covered → no.
    assert _time_covers(day_windows["Monday"], "09:00", "17:00") is False
    # Slot 10:00-13:00 — fully inside → yes.
    assert _time_covers(day_windows["Monday"], "10:00", "13:00") is True
