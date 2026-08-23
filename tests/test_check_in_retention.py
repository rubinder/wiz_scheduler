"""Check-ins are swept like every other retained record.

Issue #63 asks for six months of history; without a sweep the table grows
forever and the "6-month" figure is decoration.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, EmployeeCheckIn, Location, Region
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.services.data_retention import run_data_retention
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _seed(db: AsyncSession, ages_in_days: list[int]) -> str:
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=_id()))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, employee_id = _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="UTC"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    await db.flush()

    now = datetime.now(timezone.utc)
    for i, age in enumerate(ages_in_days):
        at = now - timedelta(days=age)
        db.add(EmployeeCheckIn(
            id=_id(), company_id=company_id, location_id=location_id,
            employee_id=employee_id, checked_in_at=at, local_date=at.date(),
            counter=i, status=CHECK_IN_MATCHED, minutes_from_start=0,
        ))
    await db.commit()
    return company_id


async def test_check_ins_past_the_cutoff_are_deleted(db_session: AsyncSession):
    await _seed(db_session, [settings.RETENTION_CHECKINS_DAYS + 10])

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 1
    assert (await db_session.execute(
        select(func.count()).select_from(EmployeeCheckIn)
    )).scalar_one() == 0


async def test_check_ins_inside_the_window_survive(db_session: AsyncSession):
    await _seed(db_session, [1, 30, settings.RETENTION_CHECKINS_DAYS - 1])

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 0
    assert (await db_session.execute(
        select(func.count()).select_from(EmployeeCheckIn)
    )).scalar_one() == 3


async def test_the_sweep_reports_zero_rather_than_omitting_the_key(
    db_session: AsyncSession
):
    """Callers read the summary by key; a missing key is a KeyError, not a
    zero."""
    summary = await run_data_retention(db_session)
    assert summary["old_check_ins_deleted"] == 0


async def test_retention_window_is_about_six_months():
    assert settings.RETENTION_CHECKINS_DAYS == 180
