"""The two rules payroll turns on, tested without a database.

These call the functions DIRECTLY on datetimes built with ZoneInfo tzinfo.
They never go through a SQLite round-trip, which strips tzinfo — so they test
the rule rather than the driver.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.services.time_entries import paid_minutes, pay_date_for

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
TOKYO = ZoneInfo("Asia/Tokyo")


# --- paid_minutes: a wall-clock contract, not an elapsed-instant measure ---

def test_an_ordinary_day_is_eight_hours():
    start = datetime(2026, 9, 15, 9, 0, tzinfo=NY)
    end = datetime(2026, 9, 15, 17, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_a_midnight_crossing_written_with_the_same_date():
    """The generator may write the end date as the start date; the faces are
    what count."""
    start = datetime(2026, 9, 15, 22, 0, tzinfo=NY)
    end = datetime(2026, 9, 15, 6, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_a_midnight_crossing_written_with_the_next_date():
    start = datetime(2026, 9, 15, 22, 0, tzinfo=NY)
    end = datetime(2026, 9, 16, 6, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_spring_forward_still_pays_eight_hours():
    """Clocks go forward at 02:00 on 2026-03-08 in America/New_York, so the
    elapsed instant is seven hours. Scheduled hours are a wall-clock contract
    between an employer and an employee, not an elapsed-instant measurement.
    This assertion is what makes a future 'fix' to instant arithmetic fail
    loudly instead of quietly under-paying a night shift once a year."""
    start = datetime(2026, 3, 7, 22, 0, tzinfo=NY)
    end = datetime(2026, 3, 8, 6, 0, tzinfo=NY)
    # Subtracting two aware datetimes built with the SAME cached ZoneInfo
    # object ignores tzinfo and compares wall-clock fields (CPython's
    # documented behavior for `datetime.__sub__`), so `end - start` alone
    # would silently give the wall-clock 8 hours here, masking the DST jump
    # this test exists to name. Converting both sides to UTC first forces
    # the real elapsed-instant comparison.
    instant_hours = (
        end.astimezone(ZoneInfo("UTC")) - start.astimezone(ZoneInfo("UTC"))
    ).total_seconds() / 3600
    assert instant_hours == 7                  # the instant truth
    assert paid_minutes(start, end) == 480     # the contract we pay


def test_equal_faces_raise_rather_than_paying_a_full_day():
    """_shift_duration_hours returns 24 here. Silently paying someone for a
    full day because two timestamps matched is the worst available failure."""
    start = datetime(2026, 9, 15, 9, 0, tzinfo=NY)
    with pytest.raises(ValueError):
        paid_minutes(start, start)


# --- pay_date_for: the midnight-crossing rule --------------------------------

def test_a_late_start_west_of_utc_pays_on_the_local_start_date():
    """The UTC instant is 2026-01-05T03:00Z. A naive .date() on a UTC value
    returns the 5th — this is the test that fails if anyone drops the
    astimezone."""
    start = datetime(2026, 1, 4, 22, 0, tzinfo=NY)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 1, 5)
    assert pay_date_for("America/New_York", start) == date(2026, 1, 4)


def test_a_second_timezone_west_of_utc():
    start = datetime(2026, 3, 1, 23, 0, tzinfo=LA)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 3, 2)
    assert pay_date_for("America/Los_Angeles", start) == date(2026, 3, 1)


def test_east_of_utc_too():
    """Tokyo rolls over nine hours before UTC: an 08:00 local start on the
    11th is still the 10th in UTC."""
    start = datetime(2026, 5, 11, 8, 0, tzinfo=TOKYO)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 5, 10)
    assert pay_date_for("Asia/Tokyo", start) == date(2026, 5, 11)
