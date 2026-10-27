"""GDPR erasure: personal data goes, pay records stay (#126)."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeAffinity, EmployeeAvailability, EmployeeCheckIn, EmployeeDayBlackout,
    EmployeeCompany, EmployeeDayPreference, EmployeeHourRangeCap, EmployeeHourRangePreference,
    EmployeeInvite, EmployeeRoleMinutes, Location, EmployeeRole, EmailVerificationToken,
    PasswordResetToken, ScheduleLock, Shift, ShiftSchedule, TimeEntry, User, UserConsent,
)
from backend.models.payroll_export import PayrollExport
from backend.config import settings
from backend.services.check_in import issue_token, local_date_for, record_check_in
from tests.conftest import COMPANY_ID, EMPLOYEE_USER_ID, MANAGER_USER_ID, _id

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


async def _shift(db, *, emp_id, loc_id, role, start) -> Shift:
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc_id,
                          week_start_date=start.date() - timedelta(days=start.weekday()),
                          status="approved")
    db.add(sched)
    await db.flush()
    s = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc_id,
              employee_id=emp_id, role_id=role.id, role_name=role.name, date=start.date(),
              start_time=start, end_time=start + timedelta(hours=8))
    db.add(s)
    await db.flush()
    return s


def _time_entry(shift: Shift, emp: Employee, loc, **kw) -> TimeEntry:
    return TimeEntry(
        id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
        shift_id=shift.id, role_id=shift.role_id, role_name=shift.role_name,
        employee_name=emp.full_name, location_name=loc.name, location_timezone=loc.timezone,
        pay_date=shift.date, start_time=shift.start_time, end_time=shift.end_time,
        paid_minutes=480, **{"source": "checked_in", **kw},
    )


async def _reload(db, model, id_):
    return (await db.execute(select(model).where(model.id == id_)
                             .execution_options(populate_existing=True))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_employee_erasure_keeps_shifts_and_pay(client, db_session: AsyncSession, employee_token,
                                                     manager_token, seed_employee_user, seed_employees,
                                                     seed_location, seed_roles):
    emp, other, loc, role = seed_employees[0], seed_employees[1], seed_location, seed_roles[0]
    emp.user_id = EMPLOYEE_USER_ID
    emp.external_id = "ext-1"
    emp.pay_rate = 20
    emp.hire_date = date(2024, 1, 1)
    emp.seniority_rank = 2
    emp.timezone = "America/New_York"
    emp.location_ids = [loc.id]
    past = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role,
                        start=NOW - timedelta(days=3))
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role,
                          start=NOW + timedelta(days=3))
    check_in = EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                               shift_id=past.id, checked_in_at=past.start_time,
                               local_date=past.start_time.date(), counter=0, status="matched")
    db_session.add(check_in)
    await db_session.flush()
    entry = _time_entry(past, emp, loc, check_in_id=check_in.id, checked_in_at=past.start_time)
    db_session.add_all([
        entry,
        EmployeeInvite(id=_id(), company_id=COMPANY_ID, employee_id=emp.id, token=_id() * 4,
                       email="alice@test.com", status="accepted", expires_at=NOW + timedelta(days=1)),
        EmployeeDayBlackout(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                            day_of_week=0, start_time="20:00", end_time="22:00"),
        EmployeeDayPreference(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                              day_of_week=1, weight=0.5),
        EmployeeAffinity(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                         target_employee_id=other.id, level=1, entry_date=NOW.date()),
        EmployeeAffinity(id=_id(), company_id=COMPANY_ID, employee_id=other.id,
                         target_employee_id=emp.id, level=1, entry_date=NOW.date()),
        EmployeeAvailability(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                             year=NOW.year, month=NOW.month, day=1,
                             start_time=NOW, end_time=NOW + timedelta(hours=8)),
        EmployeeHourRangeCap(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                             start_time="16:00", end_time="22:00", max_per_week=3),
        EmployeeHourRangePreference(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                                    start_time="08:00", end_time="12:00", weight=0.5),
        EmployeeRoleMinutes(id=_id(), company_id=COMPANY_ID, employee_id=emp.id, role_id=role.id,
                            month_start=NOW.date().replace(day=1), total_minutes=60.0),
        EmployeeCompany(id=_id(), employee_id=emp.id, company_id=COMPANY_ID),
        UserConsent(id=_id(), user_id=EMPLOYEE_USER_ID, company_id=COMPANY_ID,
                    consent_type="privacy_policy"),
    ])
    await db_session.commit()
    emp_id, past_id, future_id, entry_id, ci_id = emp.id, past.id, future.id, entry.id, check_in.id
    full_name = emp.full_name

    resp = await client.delete("/api/v1/gdpr/delete-account",
                               headers={"Authorization": f"Bearer {employee_token}"})
    assert resp.status_code == 200

    db_session.expire_all()
    erased = await _reload(db_session, Employee, emp_id)
    assert erased is not None
    assert erased.archived_at is not None and erased.erased_at is not None
    assert erased.email is None and erased.user_id is None and erased.external_id is None
    assert erased.pay_rate is None and erased.timezone is None
    assert erased.hire_date is None and erased.seniority_rank is None
    assert erased.location_ids is None
    assert erased.full_name == full_name

    assert (await _reload(db_session, Shift, past_id)).employee_id == emp_id
    assert (await _reload(db_session, Shift, future_id)).employee_id is None
    kept = await _reload(db_session, TimeEntry, entry_id)
    assert kept is not None and kept.check_in_id is None and kept.employee_name == full_name
    assert await _reload(db_session, EmployeeCheckIn, ci_id) is None
    assert await _reload(db_session, User, EMPLOYEE_USER_ID) is None

    for model in (EmployeeInvite, EmployeeDayBlackout, EmployeeDayPreference, EmployeeAvailability,
                  EmployeeRole, EmployeeHourRangeCap, EmployeeHourRangePreference,
                  EmployeeRoleMinutes, EmployeeCompany):
        rows = (await db_session.execute(
            select(model).where(model.employee_id == emp_id))).scalars().all()
        assert rows == [], model.__name__
    affinities = (await db_session.execute(select(EmployeeAffinity).where(
        (EmployeeAffinity.employee_id == emp_id) | (EmployeeAffinity.target_employee_id == emp_id)
    ))).scalars().all()
    assert affinities == []
    assert (await db_session.execute(
        select(UserConsent).where(UserConsent.user_id == EMPLOYEE_USER_ID))).first() is None

    restore = await client.post(f"/api/v1/employees/{emp_id}/restore",
                                headers={"Authorization": f"Bearer {manager_token}"})
    assert restore.status_code == 409
    assert restore.json()["detail"]["code"] == "employee_erased"


@pytest.mark.asyncio
async def test_manager_erasure_nulls_attribution(client, db_session: AsyncSession, manager_token,
                                                 seed_manager, seed_employees, seed_location,
                                                 seed_roles):
    emp, loc, role = seed_employees[0], seed_location, seed_roles[0]
    shift = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role,
                         start=NOW - timedelta(days=3))
    entry = _time_entry(shift, emp, loc, source="manager_attested",
                        attested_by_user_id=MANAGER_USER_ID, attested_at=NOW,
                        approved_by_user_id=MANAGER_USER_ID, approved_at=NOW)
    export = PayrollExport(id=_id(), company_id=COMPANY_ID, exported_by_user_id=MANAGER_USER_ID,
                           format="csv", range_start=NOW.date() - timedelta(days=7),
                           range_end=NOW.date(), entry_count=1, paid_minutes_total=480)
    db_session.add_all([
        entry, export,
        ScheduleLock(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                     lock_group=_id(), locked_by_user_id=MANAGER_USER_ID,
                     operation="generate", expires_at=NOW + timedelta(minutes=5)),
        PasswordResetToken(id=_id(), user_id=MANAGER_USER_ID, token=_id() * 4,
                           expires_at=NOW + timedelta(minutes=30)),
        EmailVerificationToken(id=_id(), user_id=MANAGER_USER_ID, token=_id() * 4,
                               expires_at=NOW + timedelta(hours=48)),
    ])
    await db_session.commit()
    entry_id, export_id = entry.id, export.id

    resp = await client.delete("/api/v1/gdpr/delete-account",
                               headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200

    db_session.expire_all()
    assert await _reload(db_session, User, MANAGER_USER_ID) is None
    kept = await _reload(db_session, TimeEntry, entry_id)
    assert kept is not None
    assert kept.approved_by_user_id is None and kept.attested_by_user_id is None
    kept_export = await _reload(db_session, PayrollExport, export_id)
    assert kept_export is not None and kept_export.exported_by_user_id is None
    assert (await db_session.execute(select(ScheduleLock).where(
        ScheduleLock.locked_by_user_id == MANAGER_USER_ID))).first() is None
    for model in (PasswordResetToken, EmailVerificationToken):
        assert (await db_session.execute(select(model).where(
            model.user_id == MANAGER_USER_ID))).first() is None


@pytest.fixture
def _qr_secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest.mark.asyncio
async def test_erasure_keeps_todays_check_ins_so_the_qr_counter_stays_unique(
        client, db_session: AsyncSession, employee_token, seed_employee_user, seed_employees,
        seed_location, seed_roles, _qr_secret):
    emp, other, loc = seed_employees[0], seed_employees[1], seed_location
    emp.user_id = EMPLOYEE_USER_ID
    third = Employee(id=_id(), company_id=COMPANY_ID, full_name="Carol Third")
    db_session.add(third)
    today = local_date_for(loc, NOW)

    def ci(employee, local_date, counter):
        return EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                               employee_id=employee.id, checked_in_at=NOW, local_date=local_date,
                               counter=counter, status="no_shift")

    past_row, today_row, other_row = ci(emp, today - timedelta(days=2), 0), ci(emp, today, 0), ci(other, today, 1)
    db_session.add_all([past_row, today_row, other_row])
    await db_session.commit()
    past_id, today_id, third_id, loc_id = past_row.id, today_row.id, third.id, loc.id

    resp = await client.delete("/api/v1/gdpr/delete-account",
                               headers={"Authorization": f"Bearer {employee_token}"})
    assert resp.status_code == 200

    db_session.expire_all()
    assert await _reload(db_session, EmployeeCheckIn, past_id) is None
    assert await _reload(db_session, EmployeeCheckIn, today_id) is not None

    # The next scan at the location still gets a fresh counter (2), not a collision.
    location = await _reload(db_session, Location, loc_id)
    token, counter = await issue_token(db_session, "slug", location, now=NOW)
    assert counter == 2
    recorded = await record_check_in(db_session, COMPANY_ID, third_id, location, "slug", token, now=NOW)
    assert recorded is not None
