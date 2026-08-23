"""Recording an employee's arrival against their scheduled shift.

The whole decision table lives here so the router stays a thin translation
layer between HTTP and this module.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import EmployeeCheckIn, Location, Shift
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE,
    CHECK_IN_MATCHED,
    CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.services.check_in_token import (
    build_check_in_token,
    verify_check_in_token,
)

logger = logging.getLogger(__name__)


class CheckInRejected(Exception):
    """The scan was not recorded at all.

    Distinct from the four statuses, which all describe scans that WERE
    recorded. Only a token that does not verify lands here.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _as_utc(dt: datetime) -> datetime:
    """Normalize a (possibly naive) datetime from SQLite to UTC-aware.

    SQLAlchemy's DateTime(timezone=True) is honored by Postgres but ignored
    by SQLite, which strips tzinfo on round-trip. We always store UTC, so
    re-attaching UTC tzinfo when missing is correct. Same pattern as
    schedule_lock.py's `_as_utc`.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def local_date_for(location: Location, at: datetime) -> date:
    """The calendar date at *location* when *at* happened.

    "First scan of the day" and the rotation counter are both wall-clock
    questions at the store, not UTC ones. A location in Tokyo rolls over
    nine hours before UTC does.
    """
    return at.astimezone(ZoneInfo(location.timezone)).date()


async def current_counter(
    db: AsyncSession, location_id: str, local_date: date
) -> int:
    """How many check-ins this location has recorded on *local_date*.

    This IS the rotation counter — the exact input issue #63 specified. A
    separate counter row would be a second source of truth that can drift
    from the rows it counts.
    """
    result = await db.execute(
        select(func.count(EmployeeCheckIn.id)).where(
            EmployeeCheckIn.location_id == location_id,
            EmployeeCheckIn.local_date == local_date,
        )
    )
    return result.scalar() or 0


async def issue_token(
    db: AsyncSession,
    company_slug: str,
    location: Location,
    now: datetime | None = None,
) -> tuple[str, int]:
    """The code to display right now, and the counter it stands for.

    *now* is injectable so a test can pin the clock. It has to be: the local
    date is part of the signed message, so a token issued against the real
    clock will not verify inside a test that pins a different date.
    """
    today = local_date_for(location, now or datetime.now(timezone.utc))
    counter = await current_counter(db, location.id, today)
    return build_check_in_token(company_slug, location.id, today, counter), counter


async def _match_shift(
    db: AsyncSession,
    company_id: str,
    employee_id: str,
    location_id: str,
    at: datetime,
) -> tuple[Shift | None, str]:
    """Find the shift this scan belongs to, and say what happened.

    Matching on start_time rather than on the calendar date is what lets a
    22:00-06:00 shift work with no special case: the scan at 21:55 is simply
    five minutes from the start, and the date either side of midnight never
    enters the query.
    """
    window = timedelta(hours=settings.CHECKIN_MATCH_WINDOW_HOURS)

    candidates = (await db.execute(
        select(Shift).where(
            Shift.company_id == company_id,
            Shift.employee_id == employee_id,
            Shift.start_time >= at - window,
            Shift.start_time <= at + window,
        )
    )).scalars().all()

    here = [s for s in candidates if s.location_id == location_id]
    if here:
        nearest = min(here, key=lambda s: abs(_as_utc(s.start_time) - at))
        return nearest, CHECK_IN_MATCHED

    if candidates:
        # Scheduled in this window, but somewhere else.
        return None, CHECK_IN_WRONG_LOCATION

    return None, CHECK_IN_NO_SHIFT


async def record_check_in(
    db: AsyncSession,
    company_id: str,
    employee_id: str,
    location: Location,
    company_slug: str,
    token: str,
    now: datetime | None = None,
) -> EmployeeCheckIn:
    """Validate a scanned code and record the arrival.

    Raises CheckInRejected if the code does not verify. Everything else — no
    shift, wrong location, a second scan — is recorded with a status, because
    an employee who turns up unscheduled is something a manager wants to know
    and refusing the scan would throw that away.
    """
    at = now or datetime.now(timezone.utc)
    today = local_date_for(location, at)
    counter = await current_counter(db, location.id, today)

    if not verify_check_in_token(token, company_slug, location.id, today, counter):
        # A token that fails against the current counter is either forged, or
        # it was a real code for an earlier position that has since rotated
        # out from under it. The two are distinguishable — re-verify it
        # against every counter this token could have been issued for — and
        # worth telling apart: "someone already used that" is the ordinary,
        # non-alarming event at a shift change, while "invalid" should stay
        # reserved for a code that was never real.
        if any(
            verify_check_in_token(token, company_slug, location.id, today, c)
            for c in range(counter)
        ):
            raise CheckInRejected(
                "code_already_used",
                "Someone just used that code. Scan the new one on screen.",
            )
        raise CheckInRejected(
            "invalid_token",
            "That code is no longer valid. Scan the code on screen again.",
        )

    shift, status = await _match_shift(
        db, company_id, employee_id, location.id, at
    )

    already = (await db.execute(
        select(EmployeeCheckIn.id).where(
            EmployeeCheckIn.company_id == company_id,
            EmployeeCheckIn.employee_id == employee_id,
            EmployeeCheckIn.local_date == today,
        ).limit(1)
    )).scalar_one_or_none()

    minutes = None
    if shift is not None:
        minutes = round((at - _as_utc(shift.start_time)).total_seconds() / 60)

    row = EmployeeCheckIn(
        company_id=company_id,
        location_id=location.id,
        employee_id=employee_id,
        shift_id=shift.id if shift is not None else None,
        checked_in_at=at,
        local_date=today,
        counter=counter,
        # A repeat scan is a duplicate whatever it matched: the report filters
        # to `matched`, so this is what keeps a 17:00 re-scan from overwriting
        # an on-time arrival with a nine-hour delay.
        status=CHECK_IN_DUPLICATE if already else status,
        minutes_from_start=minutes,
    )
    db.add(row)

    try:
        await db.commit()
    except IntegrityError:
        # Someone else took this counter between our read and our write. The
        # unique constraint is the arbiter; this is the losing side of a race
        # two people scanning the same displayed code will hit routinely.
        await db.rollback()
        raise CheckInRejected(
            "code_already_used",
            "Someone just used that code. Scan the new one on screen.",
        )

    await db.refresh(row)
    return row
