"""Retention cuts the audit log, never the pay record.

Deleting an audit log must never un-export a pay period and make it eligible
for a second push, and sweeping a check-in must never erase what a pay record
was based on. Both directions are asserted here.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, PayrollExport, Region, Role,
    Shift, ShiftSchedule, TimeEntry, User,
)
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.models.time_entry import TIME_ENTRY_CHECKED_IN
from backend.services.data_retention import run_data_retention
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

TODAY = datetime.now(timezone.utc).date()


async def _seed(
    db: AsyncSession, *, export_age_days: int, check_in_age_days: int
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()

    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="UTC"))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com",
                    location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.flush()

    schedule_id, shift_id = _id(), _id()
    start = now - timedelta(days=check_in_age_days)
    db.add(ShiftSchedule(id=schedule_id, company_id=company_id,
                         location_id=location_id, week_start_date=start.date(),
                         status="approved"))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=company_id,
                 shift_schedule_id=schedule_id, location_id=location_id,
                 employee_id=employee_id, role_id=role_id, role_name=role.name,
                 date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=8)))
    check_in_id = _id()
    db.add(EmployeeCheckIn(
        id=check_in_id, company_id=company_id, location_id=location_id,
        employee_id=employee_id, shift_id=shift_id, checked_in_at=start,
        local_date=start.date(), counter=0, status=CHECK_IN_MATCHED,
        minutes_from_start=-4,
    ))
    export_id = _id()
    db.add(PayrollExport(
        id=export_id, company_id=company_id, ownership_group_id=None,
        exported_by_user_id=user_id, format="csv", location_id=None,
        range_start=start.date(), range_end=start.date(), entry_count=1,
        paid_minutes_total=480,
        created_at=now - timedelta(days=export_age_days),
    ))
    await db.flush()
    entry_id = _id()
    db.add(TimeEntry(
        id=entry_id, company_id=company_id, location_id=location_id,
        employee_id=employee_id, shift_id=shift_id, role_id=role_id,
        role_name=role.name, pay_date=start.date(), start_time=start,
        end_time=start + timedelta(hours=8), paid_minutes=480,
        source=TIME_ENTRY_CHECKED_IN, check_in_id=check_in_id,
        checked_in_at=start, lateness_minutes=-4,
        exported_at=now - timedelta(days=export_age_days),
        payroll_export_id=export_id,
    ))
    await db.commit()
    return SimpleNamespace(entry_id=entry_id, export_id=export_id,
                           check_in_id=check_in_id)


async def test_an_old_export_log_row_is_deleted_and_counted(
    db_session: AsyncSession
):
    s = await _seed(
        db_session,
        export_age_days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS + 10,
        check_in_age_days=1,
    )

    summary = await run_data_retention(db_session)

    assert summary["payroll_export_logs_deleted"] == 1
    assert (await db_session.execute(
        select(func.count()).select_from(PayrollExport)
    )).scalar_one() == 0


async def test_the_entries_keep_exported_at_and_lose_only_the_link(
    db_session: AsyncSession
):
    """exported_at survives. Deleting an audit log must never un-export a pay
    period and make it eligible for a second push."""
    s = await _seed(
        db_session,
        export_age_days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS + 10,
        check_in_age_days=1,
    )

    await run_data_retention(db_session)

    entry = await db_session.get(TimeEntry, s.entry_id)
    await db_session.refresh(entry)
    assert entry is not None
    assert entry.exported_at is not None
    assert entry.payroll_export_id is None


async def test_a_recent_export_log_row_survives(db_session: AsyncSession):
    await _seed(db_session, export_age_days=1, check_in_age_days=1)

    summary = await run_data_retention(db_session)

    assert summary["payroll_export_logs_deleted"] == 0
    assert (await db_session.execute(
        select(func.count()).select_from(PayrollExport)
    )).scalar_one() == 1


async def test_sweeping_a_check_in_leaves_the_pay_record_able_to_explain_itself(
    db_session: AsyncSession
):
    """checked_in_at and lateness_minutes are denormalised precisely so the
    pay record outlives the scan. Only the link is cleared."""
    s = await _seed(
        db_session, export_age_days=1,
        check_in_age_days=settings.RETENTION_CHECKINS_DAYS + 10,
    )

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 1
    entry = await db_session.get(TimeEntry, s.entry_id)
    await db_session.refresh(entry)
    assert entry.check_in_id is None
    assert entry.checked_in_at is not None
    assert entry.lateness_minutes == -4


async def test_the_sweep_reports_zero_rather_than_omitting_the_key(
    db_session: AsyncSession
):
    """Callers read the summary by key; a missing key is a KeyError, not a
    zero."""
    summary = await run_data_retention(db_session)
    assert summary["payroll_export_logs_deleted"] == 0


async def test_the_export_log_window_is_a_year():
    """Matching RETENTION_REVOKED_CONSENTS_DAYS: an export is a pay-affecting
    action and "who pulled that file, when, covering which dates" is an
    annual-cycle audit question."""
    assert settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS == 365
