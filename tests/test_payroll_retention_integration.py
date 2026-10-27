"""Retention cuts the audit log, never the pay record.

Deleting an audit log must never un-export a pay period and make it eligible
for a second push, and sweeping a check-in must never erase what a pay record
was based on. Both directions are asserted here.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
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
    await db.flush()
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
        role_name=role.name, employee_name="Test Employee",
        location_name="Test Location", location_timezone="UTC",
        pay_date=start.date(), start_time=start,
        end_time=start + timedelta(hours=8), paid_minutes=480,
        source=TIME_ENTRY_CHECKED_IN, check_in_id=check_in_id,
        checked_in_at=start, lateness_minutes=-4,
        exported_at=now - timedelta(days=export_age_days),
        payroll_export_id=export_id,
    ))
    await db.commit()
    return SimpleNamespace(entry_id=entry_id, export_id=export_id,
                           check_in_id=check_in_id, company_id=company_id,
                           location_id=location_id, role_id=role_id,
                           employee_id=employee_id, schedule_id=schedule_id)


async def _add_entry(db: AsyncSession, s: SimpleNamespace, *, pay_date: date,
                     employee_id: str | None = None) -> str:
    """One more pay record on the seeded schedule, dated ``pay_date``."""
    start = datetime.combine(pay_date, time(9), tzinfo=timezone.utc)
    shift_id, entry_id = _id(), _id()
    db.add(Shift(id=shift_id, company_id=s.company_id,
                 shift_schedule_id=s.schedule_id, location_id=s.location_id,
                 employee_id=employee_id or s.employee_id, role_id=s.role_id,
                 role_name="Role A", date=pay_date, start_time=start,
                 end_time=start + timedelta(hours=8)))
    await db.flush()
    db.add(TimeEntry(
        id=entry_id, company_id=s.company_id, location_id=s.location_id,
        employee_id=employee_id or s.employee_id, shift_id=shift_id,
        role_id=s.role_id, role_name="Role A", employee_name="Test Employee",
        location_name="Test Location", location_timezone="UTC",
        pay_date=pay_date, start_time=start,
        end_time=start + timedelta(hours=8), paid_minutes=480,
        source=TIME_ENTRY_CHECKED_IN, checked_in_at=start, lateness_minutes=0,
    ))
    await db.commit()
    return entry_id


def _pay_cutoff() -> date:
    """The oldest pay_date the sweep keeps (it deletes strictly before)."""
    return (datetime.now(timezone.utc)
            - timedelta(days=settings.RETENTION_TIME_ENTRIES_DAYS)).date()


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


async def test_a_pay_record_past_seven_years_is_deleted_and_a_newer_one_kept(
    db_session: AsyncSession
):
    """#172: pay records have an end of life. One older than the window goes,
    one inside it stays."""
    s = await _seed(db_session, export_age_days=1, check_in_age_days=1)
    old = await _add_entry(
        db_session, s, pay_date=_pay_cutoff() - timedelta(days=1)
    )
    kept = await _add_entry(
        db_session, s, pay_date=_pay_cutoff() + timedelta(days=1)
    )

    summary = await run_data_retention(db_session)

    assert summary["time_entries_deleted"] == 1
    remaining = set((await db_session.execute(
        select(TimeEntry.id).where(TimeEntry.company_id == s.company_id)
    )).scalars().all())
    assert old not in remaining
    assert kept in remaining
    assert s.entry_id in remaining


async def test_a_pay_record_dated_exactly_on_the_cutoff_is_kept(
    db_session: AsyncSession
):
    """The comparison is strictly less-than; GET /payroll/retention's
    due_count uses the same one."""
    s = await _seed(db_session, export_age_days=1, check_in_age_days=1)
    boundary = await _add_entry(db_session, s, pay_date=_pay_cutoff())

    summary = await run_data_retention(db_session)

    assert summary["time_entries_deleted"] == 0
    assert await db_session.get(TimeEntry, boundary) is not None


async def test_an_archived_employees_old_pay_record_is_swept_by_date(
    db_session: AsyncSession
):
    """Archived and erased employees' rows age out like anyone else's."""
    s = await _seed(db_session, export_age_days=1, check_in_age_days=1)
    archived_id = _id()
    db_session.add(Employee(
        id=archived_id, company_id=s.company_id, full_name="Gone",
        email=f"{archived_id}@example.com", location_ids=[s.location_id],
        archived_at=datetime.now(timezone.utc) - timedelta(days=3000),
    ))
    await db_session.commit()
    old = await _add_entry(
        db_session, s, pay_date=_pay_cutoff() - timedelta(days=30),
        employee_id=archived_id,
    )

    summary = await run_data_retention(db_session)

    assert summary["time_entries_deleted"] == 1
    assert (await db_session.execute(
        select(func.count()).select_from(TimeEntry).where(TimeEntry.id == old)
    )).scalar_one() == 0


async def test_the_time_entry_key_is_reported_at_zero(db_session: AsyncSession):
    summary = await run_data_retention(db_session)
    assert summary["time_entries_deleted"] == 0


async def test_the_pay_record_window_is_seven_years():
    """7 x 365 + 2 leap days. Pinned so a change is a decision, not a drift."""
    assert settings.RETENTION_TIME_ENTRIES_DAYS == 2557


async def test_a_swept_pay_record_is_not_derived_or_queued_again(
    db_session: AsyncSession,
):
    """Approved shifts are kept, and the Payroll page derives entries and
    lists exceptions on every load. A shift past the retention window must
    come back neither as a pay record nor as an exception to attest after
    the sweep deleted its record (#172)."""
    from backend.services.time_entries import derive_time_entries, list_exceptions

    s = await _seed(db_session, export_age_days=1, check_in_age_days=1)
    old_day = _pay_cutoff() - timedelta(days=1)
    new_day = _pay_cutoff() + timedelta(days=1)
    await _add_entry(db_session, s, pay_date=old_day)
    await _add_entry(db_session, s, pay_date=new_day)
    await run_data_retention(db_session)
    # Delete the in-window record too, so its shift shows what an unswept
    # shift without a record looks like: an exception to attest.
    await db_session.execute(
        delete(TimeEntry).where(TimeEntry.company_id == s.company_id,
                                TimeEntry.pay_date == new_day)
    )
    await db_session.commit()

    await derive_time_entries(db_session, s.company_id, old_day, new_day)
    exceptions = await list_exceptions(db_session, s.company_id, old_day, new_day)

    entry_days = set((await db_session.execute(
        select(TimeEntry.pay_date).where(TimeEntry.company_id == s.company_id)
    )).scalars().all())
    assert old_day not in entry_days
    exception_days = {e.pay_date for e in exceptions}
    assert old_day not in exception_days
    assert new_day in exception_days
