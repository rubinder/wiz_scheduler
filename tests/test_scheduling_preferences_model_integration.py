"""Schema guarantees for the three preference tables.

The weight column is the load-bearing part: `Numeric(2, 1)` plus a 0-1 check
is what makes "0 to 1 in 0.1 increments" a database guarantee rather than a
slider convention. The unique constraints matter because a duplicate row
would contribute its points twice and silently double a preference's effect.

0.1-increment enforcement is additionally asserted at the API edge by a
Pydantic validator.

Every row needs a real company and employee: their FKs are enforced, and a
missing parent would raise the same IntegrityError the rejection tests
expect from the constraint under test.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)

COMPANY_ID = "comp0001"
EMPLOYEE_ID = "empl0001"


@pytest_asyncio.fixture(autouse=True)
async def _parents(db_session: AsyncSession) -> None:
    db_session.add(Company(id=COMPANY_ID, name="C", slug=f"slug-{COMPANY_ID}"))
    await db_session.flush()
    db_session.add(Employee(id=EMPLOYEE_ID, company_id=COMPANY_ID, full_name="E",
                            email=f"{EMPLOYEE_ID}@example.com", location_ids=[]))
    await db_session.commit()


@pytest.mark.asyncio
async def test_weight_defaults_to_seven_tenths(db_session: AsyncSession):
    """0.7 is the create-time value, not a per-employee default."""
    row = EmployeeDayPreference(
        company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=0
    )
    db_session.add(row)
    await db_session.commit()
    assert float(row.weight) == 0.7


@pytest.mark.asyncio
async def test_weight_above_one_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=1, weight=1.5
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_weight_below_zero_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=2, weight=-0.1
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_day_of_week_out_of_range_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=7
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_day_for_one_employee_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=4
        )
    )
    await db_session.commit()
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=4
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_hour_range_is_rejected(db_session: AsyncSession):
    for _ in range(2):
        db_session.add(
            EmployeeHourRangePreference(
                company_id=COMPANY_ID,
                employee_id=EMPLOYEE_ID,
                start_time="13:00",
                end_time="17:00",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cap_stores_max_per_week(db_session: AsyncSession):
    row = EmployeeHourRangeCap(
        company_id=COMPANY_ID,
        employee_id=EMPLOYEE_ID,
        start_time="16:00",
        end_time="22:00",
        max_per_week=3,
    )
    db_session.add(row)
    await db_session.commit()
    result = await db_session.execute(select(EmployeeHourRangeCap))
    assert result.scalars().one().max_per_week == 3
