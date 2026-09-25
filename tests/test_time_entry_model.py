"""The database, not the application, is what keeps payroll honest.

Every constraint here exists because application logic can be forgotten by a
future caller and a constraint cannot — the same posture as
uq_employee_check_ins_location_date_counter.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    Location,
    Region,
    Role,
    Shift,
    ShiftSchedule,
    TimeEntry,
    User,
)
from backend.models.time_entry import (
    TIME_ENTRY_CHECKED_IN,
    TIME_ENTRY_MANAGER_ATTESTED,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _seed(db: AsyncSession) -> SimpleNamespace:
    """A company with one approved 09:00-17:00 UTC shift yesterday.

    Shift timestamps are built with tzinfo=timezone.utc directly, never from
    an offset-bearing ISO string: SQLite strips tzinfo on round-trip and would
    silently shift an offset-bearing value (see _as_utc in
    backend/services/check_in.py).
    """
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()

    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone="America/New_York"))
    # Role name comes from the roles table, never a literal in feature code.
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id, full_name="Dana Okafor",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.flush()

    schedule_id, shift_id = _id(), _id()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    start = yesterday.replace(hour=9, minute=0, second=0, microsecond=0)
    db.add(ShiftSchedule(id=schedule_id, company_id=company_id,
                         location_id=location_id, week_start_date=start.date(),
                         status="approved"))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=company_id, shift_schedule_id=schedule_id,
                 location_id=location_id, employee_id=employee_id, role_id=role_id,
                 role_name=role.name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=8)))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, employee_id=employee_id,
        role_id=role_id, role_name=role.name, user_id=user_id, shift_id=shift_id,
        pay_date=start.date(), start=start, end=start + timedelta(hours=8),
    )


def _entry(s: SimpleNamespace, **overrides) -> TimeEntry:
    fields = dict(
        id=_id(), company_id=s.company_id, location_id=s.location_id,
        employee_id=s.employee_id, shift_id=s.shift_id, role_id=s.role_id,
        role_name=s.role_name, pay_date=s.pay_date, start_time=s.start,
        end_time=s.end, paid_minutes=480, source=TIME_ENTRY_CHECKED_IN,
    )
    fields.update(overrides)
    return TimeEntry(**fields)


async def test_one_entry_per_shift(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s))
    await db_session.commit()

    db_session.add(_entry(s))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_unknown_source_is_rejected(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s, source="guessed"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_attested_entry_needs_an_attester(db_session: AsyncSession):
    """An attested row without an attester is an audit trail with a hole."""
    s = await _seed(db_session)
    db_session.add(_entry(s, source=TIME_ENTRY_MANAGER_ATTESTED,
                          attested_by_user_id=None, attested_at=None))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_attested_entry_with_an_attester_is_accepted(
    db_session: AsyncSession
):
    s = await _seed(db_session)
    db_session.add(_entry(s, source=TIME_ENTRY_MANAGER_ATTESTED,
                          attested_by_user_id=s.user_id,
                          attested_at=datetime.now(timezone.utc)))
    await db_session.commit()


async def test_zero_paid_minutes_is_rejected(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s, paid_minutes=0))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
