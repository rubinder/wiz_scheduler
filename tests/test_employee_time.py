"""Employee-zone -> location-zone conversion for availability and blackouts."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.scheduling.employee_time import (
    availability_to_location,
    blackouts_to_location,
    instant_to_employee_face,
    needs_conversion,
)

NY, LA = "America/New_York", "America/Los_Angeles"


def w(start: str, end: str) -> dict:
    return {"start": f"{start}+00:00", "end": f"{end}+00:00"}


# -- no conversion ---------------------------------------------------------

def test_none_or_same_zone_returns_the_very_same_list():
    windows = [w("2026-03-02T09:00:00", "2026-03-02T17:00:00")]
    assert availability_to_location(windows, None, LA) is windows
    assert availability_to_location(windows, NY, NY) is windows
    assert not needs_conversion(None, LA)
    assert not needs_conversion(NY, NY)


def test_invalid_zone_means_no_conversion():
    windows = [w("2026-03-02T09:00:00", "2026-03-02T17:00:00")]
    assert availability_to_location(windows, "Not/AZone", LA) is windows
    assert availability_to_location(windows, NY, "garbage") is windows


# -- availability ----------------------------------------------------------

def test_ny_window_seen_from_la():
    out = availability_to_location([w("2026-03-02T09:00:00", "2026-03-02T17:00:00")], NY, LA)
    assert out == [w("2026-03-02T06:00:00", "2026-03-02T14:00:00")]


def test_window_split_at_midnight_going_east():
    # 20:00-23:00 LA is 23:00-02:00 NY: two pieces, the first ending at 23:59.
    out = availability_to_location([w("2026-03-02T20:00:00", "2026-03-02T23:00:00")], LA, NY)
    assert out == [
        w("2026-03-02T23:00:00", "2026-03-02T23:59:00"),
        w("2026-03-03T00:00:00", "2026-03-03T02:00:00"),
    ]


def test_window_split_at_midnight_going_west():
    # 00:00-04:00 NY is 21:00-01:00 LA (previous evening into the day).
    out = availability_to_location([w("2026-03-03T00:00:00", "2026-03-03T04:00:00")], NY, LA)
    assert out == [
        w("2026-03-02T21:00:00", "2026-03-02T23:59:00"),
        w("2026-03-03T00:00:00", "2026-03-03T01:00:00"),
    ]


def test_end_of_day_23_59_is_treated_as_midnight():
    # NY "until end of day" is 21:00 in LA -- not 20:59.
    out = availability_to_location([w("2026-03-02T09:00:00", "2026-03-02T23:59:00")], NY, LA)
    assert out == [w("2026-03-02T06:00:00", "2026-03-02T21:00:00")]


def test_unparseable_window_is_kept_as_is():
    bad = {"start": "nonsense", "end": "x"}
    assert availability_to_location([bad], NY, LA) == [bad]


def test_dst_week_uses_each_dates_own_offset():
    # 2026-03-08: US spring-forward. NY and LA switch together, so the gap
    # stays 3h on both sides of the change.
    before = availability_to_location([w("2026-03-07T09:00:00", "2026-03-07T17:00:00")], NY, LA)
    after = availability_to_location([w("2026-03-09T09:00:00", "2026-03-09T17:00:00")], NY, LA)
    assert before == [w("2026-03-07T06:00:00", "2026-03-07T14:00:00")]
    assert after == [w("2026-03-09T06:00:00", "2026-03-09T14:00:00")]


def test_zones_with_different_dst_rules():
    # Europe/London springs forward 2026-03-29; NY already did on 03-08.
    # On 03-16 the gap is 4h, on 03-30 it is back to 5h.
    mid = availability_to_location([w("2026-03-16T12:00:00", "2026-03-16T16:00:00")], "Europe/London", NY)
    late = availability_to_location([w("2026-03-30T12:00:00", "2026-03-30T16:00:00")], "Europe/London", NY)
    assert mid == [w("2026-03-16T08:00:00", "2026-03-16T12:00:00")]
    assert late == [w("2026-03-30T07:00:00", "2026-03-30T11:00:00")]


def test_nonexistent_spring_forward_face_does_not_raise():
    out = availability_to_location([w("2026-03-08T02:30:00", "2026-03-08T05:00:00")], NY, LA)
    assert out and all(p["start"] < p["end"] for p in out)


def test_ambiguous_fall_back_face_does_not_raise():
    out = availability_to_location([w("2026-11-01T01:30:00", "2026-11-01T05:00:00")], NY, LA)
    assert out and all(p["start"] < p["end"] for p in out)


# -- blackouts -------------------------------------------------------------

WEEK = "2026-03-02"  # a Monday


def test_blackouts_none_or_same_zone_unchanged():
    bos = [{"day": "Tuesday", "start": "14:00", "end": "18:00"}]
    assert blackouts_to_location(bos, WEEK, 7, None, LA) is bos
    assert blackouts_to_location(bos, WEEK, 7, NY, NY) is bos


def test_blackout_shifts_hours():
    bos = [{"day": "Tuesday", "start": "14:00", "end": "18:00"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Tuesday", "start": "11:00", "end": "15:00"},
    ]


def test_blackout_moves_to_another_weekday():
    bos = [{"day": "Wednesday", "start": "01:00", "end": "02:00"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Tuesday", "start": "22:00", "end": "23:00"},
    ]


def test_blackout_to_end_of_day_moves_whole_to_the_next_day():
    # LA Tue 22:00-midnight is NY Wed 01:00-03:00.
    bos = [{"day": "Tuesday", "start": "22:00", "end": "23:59"}]
    assert blackouts_to_location(bos, WEEK, 7, LA, NY) == [
        {"day": "Wednesday", "start": "01:00", "end": "03:00"},
    ]


def test_blackout_split_at_midnight():
    # LA Tue 20:00-23:00 is NY Tue 23:00 - Wed 02:00.
    bos = [{"day": "Tuesday", "start": "20:00", "end": "23:00"}]
    assert blackouts_to_location(bos, WEEK, 7, LA, NY) == [
        {"day": "Tuesday", "start": "23:00", "end": "23:59"},
        {"day": "Wednesday", "start": "00:00", "end": "02:00"},
    ]


def test_blackout_crossing_week_edge_lands_on_the_right_sunday():
    # Monday 00:30-01:30 NY is Sunday 21:30-22:30 LA. This week's Monday
    # instance falls on the PREVIOUS Sunday (outside the week) and is dropped;
    # NEXT Monday's instance falls on THIS week's Sunday and is kept.
    bos = [{"day": "Monday", "start": "00:30", "end": "01:30"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Sunday", "start": "21:30", "end": "22:30"},
    ]


def test_blackout_with_bad_times_is_kept():
    bos = [{"day": "Tuesday", "start": "late", "end": "later"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == bos


# -- instants --------------------------------------------------------------

def test_instant_to_employee_face():
    instant = datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc)  # 09:00 LA
    assert instant_to_employee_face(instant, NY) == datetime(2026, 3, 2, 12, 0)


def test_naive_instant_is_returned_unchanged():
    naive = datetime(2026, 3, 2, 9, 0)
    assert instant_to_employee_face(naive, NY) is naive
