"""Counter, shift matching, and the four statuses.

The timezone cases are load-bearing. A suite that only exercises UTC
locations proves nothing about local_date, and the same blind spot let a
-05:00 availability bug reach production (see PR #77).
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Shift, ShiftSchedule,
)
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE, CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.services.check_in import (
    CheckInRejected, current_counter, issue_token, local_date_for,
    record_check_in,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    company_id, region_id, employee_id = _id(), _id(), _id()
    slug = "acme-corp"
    db_session.add(Company(id=company_id, name="C", slug=slug))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    here = Location(id=_id(), company_id=company_id, region_id=region_id,
                    name="Here", timezone="America/New_York")
    there = Location(id=_id(), company_id=company_id, region_id=region_id,
                     name="There", timezone="America/New_York")
    db_session.add_all([here, there])
    db_session.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                            email=f"{employee_id}@example.com",
                            location_ids=[here.id, there.id]))
    await db_session.commit()
    return {"company_id": company_id, "slug": slug, "employee_id": employee_id,
            "here": here, "there": there}


async def _add_shift(db: AsyncSession, t: dict, location: Location,
                     start: datetime) -> Shift:
    sched_id = _id()
    db.add(ShiftSchedule(id=sched_id, company_id=t["company_id"],
                         location_id=location.id,
                         week_start_date=start.date(), status="draft"))
    await db.flush()
    shift = Shift(id=_id(), company_id=t["company_id"], shift_schedule_id=sched_id,
                  location_id=location.id, employee_id=t["employee_id"],
                  role_id=_id(), role_name="Floor Associate", date=start.date(),
                  start_time=start, end_time=start + timedelta(hours=8))
    db.add(shift)
    await db.commit()
    return shift


async def _scan(db: AsyncSession, t: dict, location: Location,
                now: datetime) -> EmployeeCheckIn:
    # The same clock for both calls. The local date is inside the signed
    # message, so issuing against the real clock and recording against a
    # pinned one would never verify.
    token, _ = await issue_token(db, t["slug"], location, now=now)
    return await record_check_in(db, t["company_id"], t["employee_id"],
                                 location, t["slug"], token, now=now)


# --- local_date -------------------------------------------------------------

def test_local_date_uses_the_locations_timezone():
    """23:30 UTC is still the 22nd in New York."""
    loc = Location(id="l", company_id="c", region_id="r", name="L",
                   timezone="America/New_York")
    at = datetime(2026, 8, 23, 3, 30, tzinfo=timezone.utc)
    assert local_date_for(loc, at) == date(2026, 8, 22)


def test_local_date_differs_from_utc_date_across_the_line():
    loc = Location(id="l", company_id="c", region_id="r", name="L",
                   timezone="Asia/Tokyo")
    at = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert at.date() == date(2026, 8, 23)
    assert local_date_for(loc, at) == date(2026, 8, 24)


# --- counter ----------------------------------------------------------------

async def test_counter_starts_at_zero(db_session: AsyncSession, tenant: dict):
    assert await current_counter(
        db_session, tenant["here"].id, date(2026, 8, 23)) == 0


async def test_counter_advances_with_each_recorded_scan(
    db_session: AsyncSession, tenant: dict
):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    assert await current_counter(
        db_session, tenant["here"].id, local_date_for(tenant["here"], now)) == 1


# --- single use -------------------------------------------------------------

async def test_a_spent_token_is_rejected(db_session: AsyncSession, tenant: dict):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    token, _ = await issue_token(db_session, tenant["slug"], tenant["here"],
                                 now=now)
    await record_check_in(db_session, tenant["company_id"], tenant["employee_id"],
                          tenant["here"], tenant["slug"], token, now=now)

    with pytest.raises(CheckInRejected) as exc:
        await record_check_in(db_session, tenant["company_id"],
                              tenant["employee_id"], tenant["here"],
                              tenant["slug"], token, now=now)
    assert exc.value.code == "code_already_used"


async def test_a_garbage_token_is_rejected(db_session: AsyncSession, tenant: dict):
    with pytest.raises(CheckInRejected) as exc:
        await record_check_in(db_session, tenant["company_id"],
                              tenant["employee_id"], tenant["here"],
                              tenant["slug"], "not-a-real-token",
                              now=datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc))
    assert exc.value.code == "invalid_token"


# --- matching ---------------------------------------------------------------

async def test_a_scan_near_the_shift_start_matches(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    shift = await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(minutes=4))

    assert row.status == CHECK_IN_MATCHED
    assert row.shift_id == shift.id
    assert row.minutes_from_start == 4


async def test_arriving_early_is_negative(db_session: AsyncSession, tenant: dict):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start - timedelta(minutes=7))
    assert row.minutes_from_start == -7


async def test_a_shift_crossing_midnight_matches_without_special_casing(
    db_session: AsyncSession, tenant: dict
):
    """22:00-06:00 scanned at 21:55. Matching on the timestamp means the
    calendar dates either side of midnight never enter the query."""
    start = datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc)  # 22:00 ET on 23rd
    shift = await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start - timedelta(minutes=5))
    assert row.status == CHECK_IN_MATCHED
    assert row.shift_id == shift.id
    assert row.minutes_from_start == -5


async def test_a_scan_outside_the_window_is_no_shift(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(hours=settings.CHECKIN_MATCH_WINDOW_HOURS + 1))
    assert row.status == CHECK_IN_NO_SHIFT
    assert row.shift_id is None
    assert row.minutes_from_start is None


async def test_the_nearest_shift_wins(db_session: AsyncSession, tenant: dict):
    early = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    late = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], early)
    near = await _add_shift(db_session, tenant, tenant["here"], late)

    row = await _scan(db_session, tenant, tenant["here"],
                      late + timedelta(minutes=2))
    assert row.shift_id == near.id


async def test_scanning_where_you_are_not_scheduled_is_wrong_location(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["there"], start)

    row = await _scan(db_session, tenant, tenant["here"],
                      start + timedelta(minutes=2))
    assert row.status == CHECK_IN_WRONG_LOCATION
    assert row.shift_id is None


async def test_no_shift_anywhere_is_no_shift_not_wrong_location(
    db_session: AsyncSession, tenant: dict
):
    row = await _scan(db_session, tenant, tenant["here"],
                      datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc))
    assert row.status == CHECK_IN_NO_SHIFT


# --- duplicates -------------------------------------------------------------

async def test_the_second_scan_of_the_day_is_a_duplicate(
    db_session: AsyncSession, tenant: dict
):
    start = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _add_shift(db_session, tenant, tenant["here"], start)

    first = await _scan(db_session, tenant, tenant["here"],
                        start + timedelta(minutes=1))
    second = await _scan(db_session, tenant, tenant["here"],
                         start + timedelta(minutes=90))

    assert first.status == CHECK_IN_MATCHED
    assert second.status == CHECK_IN_DUPLICATE
    # The first scan keeps the punctuality number.
    assert first.minutes_from_start == 1


async def test_a_duplicate_still_advances_the_counter(
    db_session: AsyncSession, tenant: dict
):
    """It is a recorded check-in, and the rule is that the code moves on every
    recorded scan — leaving a demonstrably-scanned code live would be worse."""
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    await _scan(db_session, tenant, tenant["here"], now + timedelta(minutes=30))

    assert await current_counter(
        db_session, tenant["here"].id, local_date_for(tenant["here"], now)) == 2


async def test_every_scan_is_recorded(db_session: AsyncSession, tenant: dict):
    now = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    await _scan(db_session, tenant, tenant["here"], now)
    await _scan(db_session, tenant, tenant["here"], now + timedelta(minutes=30))

    rows = (await db_session.execute(select(EmployeeCheckIn))).scalars().all()
    assert len(rows) == 2
