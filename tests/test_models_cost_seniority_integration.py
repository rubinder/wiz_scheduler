"""New nullable cost/seniority columns default NULL and round-trip."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location


@pytest.mark.asyncio
async def test_employee_cost_seniority_fields_default_null(
    db_session: AsyncSession, seed_company, seed_location
):
    emp = Employee(company_id=seed_company.id, full_name="Dana Okafor")
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    assert emp.pay_rate is None
    assert emp.hire_date is None
    assert emp.seniority_rank is None


@pytest.mark.asyncio
async def test_employee_cost_seniority_fields_can_be_set(
    db_session: AsyncSession, seed_company, seed_location
):
    emp = Employee(
        company_id=seed_company.id, full_name="Dana Okafor",
        pay_rate=24.50, hire_date=date(2022, 3, 1), seniority_rank=1,
    )
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    assert emp.pay_rate == 24.50
    assert emp.hire_date == date(2022, 3, 1)
    assert emp.seniority_rank == 1


@pytest.mark.asyncio
async def test_company_overtime_fields_default_null(
    db_session: AsyncSession, seed_company
):
    await db_session.refresh(seed_company)
    assert seed_company.overtime_threshold_hours is None
    assert seed_company.overtime_premium_multiplier is None


@pytest.mark.asyncio
async def test_location_overtime_fields_can_be_set(
    db_session: AsyncSession, seed_location
):
    seed_location.overtime_threshold_hours = 35.0
    seed_location.overtime_premium_multiplier = 2.0
    await db_session.commit()
    await db_session.refresh(seed_location)
    assert seed_location.overtime_threshold_hours == 35.0
    assert seed_location.overtime_premium_multiplier == 2.0
