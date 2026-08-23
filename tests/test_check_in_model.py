"""The employee_check_ins table.

The unique constraint is the interesting part: it is what makes a code
single-use, and it is deliberately enforced by the database rather than by
application logic that a future caller could forget.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, EmployeeCheckIn, Location, Region
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE,
    CHECK_IN_MATCHED,
    CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _tenant(db: AsyncSession) -> dict:
    company_id, region_id, location_id, employee_id = _id(), _id(), _id(), _id()
    db.add(Company(id=company_id, name="C", slug=_id()))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="America/New_York"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    await db.commit()
    return {"company_id": company_id, "location_id": location_id,
            "employee_id": employee_id}


def _row(t: dict, counter: int, **kw) -> EmployeeCheckIn:
    return EmployeeCheckIn(
        id=_id(),
        company_id=t["company_id"],
        location_id=t["location_id"],
        employee_id=t["employee_id"],
        checked_in_at=datetime(2026, 8, 23, 9, 3, tzinfo=timezone.utc),
        local_date=date(2026, 8, 23),
        counter=counter,
        status=kw.pop("status", CHECK_IN_MATCHED),
        **kw,
    )


async def test_a_check_in_persists(db_session: AsyncSession):
    t = await _tenant(db_session)
    db_session.add(_row(t, 0, minutes_from_start=3))
    await db_session.commit()

    row = (await db_session.execute(select(EmployeeCheckIn))).scalar_one()
    assert row.status == CHECK_IN_MATCHED
    assert row.minutes_from_start == 3
    assert row.shift_id is None


async def test_two_scans_at_the_same_counter_collide(db_session: AsyncSession):
    """Single use, enforced by the database.

    Two employees scanning the same displayed code both present counter 0.
    Without this constraint both requests read COUNT(*) == 0, both verify,
    and both record.
    """
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()

    db_session.add(_row(t, 0))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_the_next_counter_is_free(db_session: AsyncSession):
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()
    db_session.add(_row(t, 1))
    await db_session.commit()  # does not raise


async def test_the_same_counter_on_another_day_is_free(db_session: AsyncSession):
    """The counter restarts each local day, so it only has to be unique
    within one."""
    t = await _tenant(db_session)
    db_session.add(_row(t, 0))
    await db_session.commit()

    row = _row(t, 0)
    row.local_date = date(2026, 8, 24)
    db_session.add(row)
    await db_session.commit()  # does not raise


async def test_status_constants_are_the_four_documented_values():
    assert {CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
            CHECK_IN_WRONG_LOCATION, CHECK_IN_DUPLICATE} == {
        "matched", "no_shift", "wrong_location", "duplicate"}
