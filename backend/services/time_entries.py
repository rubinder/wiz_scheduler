"""Deriving payable hours from approved shifts and the arrivals that gate them.

The pure functions come first and take primitives rather than ORM rows, so
their tests never go through a SQLite round-trip (which strips tzinfo) and
therefore test the rule rather than the driver.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def paid_minutes(start: datetime, end: datetime) -> int:
    """Scheduled length in minutes, read off the WALL-CLOCK FACES.

    tzinfo is dropped deliberately and `end <= start` is treated as crossing
    midnight by adding 24 hours — the same convention as
    _shift_duration_hours in backend/scheduling/local_scheduler.py. A
    22:00 -> 06:00 shift is 480 minutes whether or not the generator wrote the
    end date as the next day, and whether or not a DST transition falls inside
    it. Scheduled hours are a wall-clock contract between an employer and an
    employee, not an elapsed-instant measurement, and a payroll file reporting
    7 hours for a "22:00-06:00" shift because the clocks went forward is the
    kind of surprise that generates a support ticket per location per year.

    Differs from _shift_duration_hours in one place: EQUAL FACES RAISE. No
    shift template produces a 24-hour shift, the schedule update handlers
    already reject start_time == end_time with a 422, and silently paying
    someone for a full day because two timestamps matched is the worst
    available failure mode. Callers treat this as a malformed shift.
    """
    start_face = start.hour * 60 + start.minute
    end_face = end.hour * 60 + end.minute
    if end_face == start_face:
        raise ValueError(
            f"shift start and end share a wall-clock face ({start_face // 60:02d}:"
            f"{start_face % 60:02d}); refusing to pay it as 24 hours"
        )
    if end_face < start_face:
        end_face += 24 * 60
    return end_face - start_face


def pay_date_for(timezone_name: str, start: datetime) -> date:
    """The location-local calendar date the shift STARTED.

    The whole shift's hours land here; no shift is ever split across two pay
    dates. Derived from the location's timezone, never from Shift.date (which
    is LLM-supplied and can drift) and never from the UTC instant.

    A 22:00 Sunday -> 06:00 Monday shift is paid entirely on Sunday. This
    agrees with EmployeeCheckIn.local_date (the local date of the scan, which
    happens at the start of the shift) and with the one day a human would name
    if you asked them when that shift was.
    """
    return start.astimezone(ZoneInfo(timezone_name)).date()
