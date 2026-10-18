"""Archiving keeps history and opens or drops only future work (#126)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeCheckIn, EmployeeInvite, Location, Role, Shift, ShiftSchedule,
)
from backend.services.archive import (
    EmployeeErased, active, archive_employee, archive_location, archive_role,
    restore_employee,
)
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


async def _shift(db, *, emp_id, loc_id, role, start, status="approved") -> Shift:
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc_id,
                          week_start_date=start.date() - timedelta(days=start.weekday()),
                          status=status)
    db.add(sched)
    await db.flush()
    s = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc_id,
              employee_id=emp_id, role_id=role.id, role_name=role.name, date=start.date(),
              start_time=start, end_time=start + timedelta(hours=8),
              preference_violations=["x"])
    db.add(s)
    await db.flush()
    return s


async def _reload(db, model, id_):
    return (await db.execute(select(model).where(model.id == id_)
                             .execution_options(populate_existing=True))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_archive_employee_opens_future_keeps_past_and_checked_in(
        db_session: AsyncSession, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    past = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW - timedelta(days=2))
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    locked = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(hours=1))
    db_session.add(EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                                   shift_id=locked.id, checked_in_at=NOW, local_date=NOW.date(),
                                   counter=0, status="matched"))
    await db_session.flush()

    counts = await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    await db_session.commit()

    assert counts.opened == 1
    assert (await _reload(db_session, Shift, past.id)).employee_id == emp.id
    opened = await _reload(db_session, Shift, future.id)
    assert opened.employee_id is None and opened.preference_violations == []
    assert (await _reload(db_session, Shift, locked.id)).employee_id == emp.id
    assert (await _reload(db_session, Employee, emp.id)).archived_at == NOW


@pytest.mark.asyncio
async def test_archive_employee_revokes_pending_invites(db_session, seed_employees):
    emp = seed_employees[0]
    invite = EmployeeInvite(id=_id(), company_id=COMPANY_ID, employee_id=emp.id, token=_id() * 4,
                            email="a@x.test", status="pending",
                            expires_at=NOW + timedelta(days=7))
    db_session.add(invite)
    await db_session.flush()
    await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    await db_session.commit()
    assert (await _reload(db_session, EmployeeInvite, invite.id)).status == "revoked"


@pytest.mark.asyncio
async def test_archive_twice_is_idempotent(db_session, seed_location, seed_roles, seed_employees):
    emp = seed_employees[0]
    await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    again = await archive_employee(db_session, COMPANY_ID, emp, now=NOW + timedelta(days=1))
    await db_session.commit()
    assert again.opened == 0
    assert (await _reload(db_session, Employee, emp.id)).archived_at == NOW


@pytest.mark.asyncio
async def test_restore_active_is_noop(db_session, seed_employees):
    emp = seed_employees[0]
    await restore_employee(db_session, COMPANY_ID, emp)
    assert emp.archived_at is None


@pytest.mark.asyncio
async def test_restore_erased_employee_refused(db_session, seed_employees):
    emp = seed_employees[0]
    emp.archived_at = NOW
    emp.erased_at = NOW
    with pytest.raises(EmployeeErased):
        await restore_employee(db_session, COMPANY_ID, emp)


@pytest.mark.asyncio
async def test_archive_location_drops_future_and_drafts_keeps_past(
        db_session, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    past = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW - timedelta(days=2))
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    draft = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role,
                         start=NOW + timedelta(days=9), status="draft")
    counts = await archive_location(db_session, COMPANY_ID, loc, now=NOW)
    await db_session.commit()
    assert counts.deleted == 2
    assert await _reload(db_session, Shift, past.id) is not None
    assert await _reload(db_session, Shift, future.id) is None
    assert await _reload(db_session, Shift, draft.id) is None
    assert await _reload(db_session, ShiftSchedule, draft.shift_schedule_id) is None
    assert (await _reload(db_session, Location, loc.id)).archived_at == NOW


@pytest.mark.asyncio
async def test_archive_location_keeps_checked_in_future_shift(
        db_session, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    locked = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(hours=1))
    db_session.add(EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                                   shift_id=locked.id, checked_in_at=NOW, local_date=NOW.date(),
                                   counter=0, status="matched"))
    await db_session.flush()
    counts = await archive_location(db_session, COMPANY_ID, loc, now=NOW)
    await db_session.commit()
    assert counts.deleted == 0
    assert await _reload(db_session, Shift, locked.id) is not None


@pytest.mark.asyncio
async def test_archive_role_opens_future_shifts(db_session, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    counts = await archive_role(db_session, COMPANY_ID, role, now=NOW)
    await db_session.commit()
    assert counts.opened == 1
    assert (await _reload(db_session, Shift, future.id)).employee_id is None
    assert (await _reload(db_session, Role, role.id)).archived_at == NOW


@pytest.mark.asyncio
async def test_active_filter(db_session, seed_employees):
    seed_employees[0].archived_at = NOW
    await db_session.commit()
    ids = (await db_session.execute(select(Employee.id).where(
        Employee.company_id == COMPANY_ID, active(Employee)))).scalars().all()
    assert seed_employees[0].id not in ids
    assert seed_employees[1].id in ids
