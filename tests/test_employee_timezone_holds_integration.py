"""Approved shifts consume an employee-with-timezone's hours on their clock."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Location, Shift, ShiftSchedule
from backend.scheduling.graph import _load_employee_availability
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, REGION_ID, ROLE_FLOOR_ID, _id

DAY = date(2026, 3, 2)  # Monday


async def _seed(db: AsyncSession) -> None:
    la_id = _id()
    db.add(Location(id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
                    name="LA Store", timezone="America/Los_Angeles"))
    await db.flush()
    # Employee available 09:00-17:00 on THEIR (New York) clock.
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=DAY.year, month=DAY.month, day=DAY.day,
        start_time=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc),
    ))
    sched_id = _id()
    db.add(ShiftSchedule(id=sched_id, company_id=COMPANY_ID, location_id=la_id,
                         week_start_date=DAY, status="approved"))
    await db.flush()
    # An approved 06:00-10:00 LA shift = 09:00-13:00 New York.
    la = ZoneInfo("America/Los_Angeles")
    db.add(Shift(
        id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched_id,
        location_id=la_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 3, 2, 6, 0, tzinfo=la),
        end_time=datetime(2026, 3, 2, 10, 0, tzinfo=la),
    ))
    await db.commit()


async def test_hold_is_subtracted_on_the_employees_clock(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed(db_session)
    avail = await _load_employee_availability(
        db_session, COMPANY_ID, DAY.isoformat(), 7,
        employee_timezones={EMPLOYEE1_ID: "America/New_York"},
    )
    windows = avail[EMPLOYEE1_ID]
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T13:00:00+00:00")
    assert windows[0]["end"].endswith("T17:00:00+00:00")


async def test_without_a_timezone_the_hold_keeps_todays_location_face(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed(db_session)
    avail = await _load_employee_availability(db_session, COMPANY_ID, DAY.isoformat(), 7)
    windows = avail[EMPLOYEE1_ID]
    # Today's behaviour: the LA face 06:00-10:00 is carved from 09:00-17:00.
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T10:00:00+00:00")


NEXT_MON = date(2026, 3, 9)  # the Monday AFTER the week starting DAY


async def _seed_next_monday_window(db: AsyncSession) -> None:
    # 00:00-03:00 on the NEXT Monday, on the employee's clock.
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=NEXT_MON.year, month=NEXT_MON.month, day=NEXT_MON.day,
        start_time=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 9, 3, 0, tzinfo=timezone.utc),
    ))
    await db.commit()


async def test_timezone_employee_keeps_window_just_after_the_week(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed_next_monday_window(db_session)
    avail = await _load_employee_availability(
        db_session, COMPANY_ID, DAY.isoformat(), 7,
        employee_timezones={EMPLOYEE1_ID: "America/New_York"},
    )
    assert [w["start"] for w in avail[EMPLOYEE1_ID]] == ["2026-03-09T00:00:00+00:00"]


async def test_no_timezone_employee_drops_window_just_after_the_week(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed_next_monday_window(db_session)
    avail = await _load_employee_availability(db_session, COMPANY_ID, DAY.isoformat(), 7)
    assert avail.get(EMPLOYEE1_ID, []) == []
