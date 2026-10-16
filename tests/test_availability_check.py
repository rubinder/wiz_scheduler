"""The one definition of "available" (#174), used by edit warnings and the
candidate ranking, matching the scheduler."""

from datetime import datetime
from types import SimpleNamespace
from datetime import timezone

from backend.services.availability_check import blackout_dicts, is_available, window_dict

SPAN = (datetime(2026, 8, 31, 9), datetime(2026, 8, 31, 17))  # a Monday


def _win(start_h, end_h, day=31):
    return {
        "start": datetime(2026, 8, day, start_h, tzinfo=timezone.utc).isoformat(),
        "end": datetime(2026, 8, day, end_h, tzinfo=timezone.utc).isoformat(),
    }


def _check(windows, other=(), blackouts=(), emp_tz=None, loc_tz=None):
    return is_available(
        windows=list(windows), other_spans=list(other), blackouts=list(blackouts),
        span_start=SPAN[0], span_end=SPAN[1], emp_tz=emp_tz, loc_tz=loc_tz,
    )


def test_a_window_covering_the_span_is_available():
    assert _check([_win(8, 18)])


def test_a_window_covering_part_of_the_span_is_not():
    assert not _check([_win(10, 18)])


def test_another_shift_inside_the_window_makes_it_unavailable():
    assert not _check([_win(8, 18)], other=[(datetime(2026, 8, 31, 12), datetime(2026, 8, 31, 14))])


def test_an_adjacent_shift_does_not():
    assert _check([_win(8, 18)], other=[(datetime(2026, 8, 31, 17), datetime(2026, 8, 31, 20))])


def test_an_overlapping_blackout_makes_it_unavailable():
    assert not _check([_win(8, 18)], blackouts=[{"day": "Monday", "start": "12:00", "end": "13:00"}])


def test_a_blackout_on_another_weekday_does_not():
    assert _check([_win(8, 18)], blackouts=[{"day": "Tuesday", "start": "12:00", "end": "13:00"}])


def test_a_blackout_touching_the_span_does_not():
    assert _check([_win(8, 18)], blackouts=[{"day": "Monday", "start": "17:00", "end": "19:00"}])


def test_employee_zone_windows_are_converted_to_the_location():
    # 06:00-14:00 in Los Angeles is 09:00-17:00 in New York.
    la = [_win(6, 14)]
    assert _check(la, emp_tz="America/Los_Angeles", loc_tz="America/New_York")
    assert not _check(la)


def test_employee_zone_blackouts_are_converted_to_the_location():
    # 06:00-07:00 in Los Angeles is 09:00-10:00 in New York: inside the span.
    blackout = [{"day": "Monday", "start": "06:00", "end": "07:00"}]
    assert not _check(
        [_win(0, 23)], blackouts=blackout,
        emp_tz="America/Los_Angeles", loc_tz="America/New_York",
    )


def test_window_dict_reads_a_midnight_end_as_end_of_day():
    row = SimpleNamespace(
        start_time=datetime(2026, 8, 31, 9), end_time=datetime(2026, 8, 31, 0),
    )
    assert window_dict(row) == {
        "start": "2026-08-31T09:00:00+00:00", "end": "2026-08-31T23:59:00+00:00",
    }


def test_blackout_dicts_names_the_weekday_and_drops_bad_ones():
    rows = [
        SimpleNamespace(day_of_week=0, start_time="20:00", end_time="22:00"),
        SimpleNamespace(day_of_week=9, start_time="20:00", end_time="22:00"),
    ]
    assert blackout_dicts(rows) == [{"day": "Monday", "start": "20:00", "end": "22:00"}]
