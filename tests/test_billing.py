"""Tests for backend/services/billing.py."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, TokenUsage
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import (
    add_purchased_credits,
    calculate_cost,
    calculate_employee_charge,
    calculate_schedule_charge,
    calculate_storage_charge,
    check_ai_credits,
    check_and_record_usage,
    check_schedule_quota,
    count_employees_for_group,
    deduct_credits_for_overage,
    get_ownership_group_id,
)
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OG_ID = _id()


@pytest_asyncio.fixture
async def seed_og(db_session: AsyncSession):
    """Ownership group with a company linked to it."""
    og = OwnershipGroup(id=OG_ID, name="Test Group", ai_credits_usd=0.0)
    db_session.add(og)
    await db_session.flush()

    company = Company(id=COMPANY_ID, name="Test Corp", slug="test123", ownership_group_id=OG_ID)
    db_session.add(company)
    await db_session.commit()
    return og


# ---------------------------------------------------------------------------
# Pure calculation helpers
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_zero_tokens(self):
        assert calculate_cost(0, 0) == 0.0

    def test_known_values(self):
        # 1M input = $3, 1M output = $15
        cost = calculate_cost(1_000_000, 1_000_000)
        assert cost == 18.0

    def test_small_usage(self):
        cost = calculate_cost(1000, 500)
        assert cost > 0
        assert cost < 0.02


class TestCalculateStorageCharge:
    def test_within_free_tier(self):
        assert calculate_storage_charge(0.3) == 0.0

    def test_at_free_tier_boundary(self):
        assert calculate_storage_charge(settings.STORAGE_FREE_GB) == 0.0

    def test_over_free_tier(self):
        charge = calculate_storage_charge(1.5)
        expected = round((1.5 - settings.STORAGE_FREE_GB) * settings.STORAGE_COST_PER_GB, 4)
        assert charge == expected


class TestCalculateEmployeeCharge:
    def test_within_free_tier(self):
        assert calculate_employee_charge(500) == 0.0

    def test_at_free_tier(self):
        assert calculate_employee_charge(settings.EMPLOYEE_FREE_TIER) == 0.0

    def test_one_block_over(self):
        count = settings.EMPLOYEE_FREE_TIER + 1
        charge = calculate_employee_charge(count)
        assert charge == settings.EMPLOYEE_COST_PER_BLOCK

    def test_exact_block_boundary(self):
        count = settings.EMPLOYEE_FREE_TIER + settings.EMPLOYEE_BLOCK_SIZE
        charge = calculate_employee_charge(count)
        assert charge == settings.EMPLOYEE_COST_PER_BLOCK


class TestCalculateScheduleCharge:
    def test_within_free_tier(self):
        assert calculate_schedule_charge(10) == 0.0

    def test_at_free_tier(self):
        assert calculate_schedule_charge(settings.SCHEDULE_FREE_TIER) == 0.0

    def test_over_free_tier(self):
        count = settings.SCHEDULE_FREE_TIER + 1
        charge = calculate_schedule_charge(count)
        assert charge == settings.SCHEDULE_COST_PER_BLOCK


# ---------------------------------------------------------------------------
# Async DB-dependent tests
# ---------------------------------------------------------------------------


async def test_get_ownership_group_id(db_session: AsyncSession, seed_og):
    og_id = await get_ownership_group_id(db_session, COMPANY_ID)
    assert og_id == OG_ID


async def test_get_ownership_group_id_missing(db_session: AsyncSession):
    og_id = await get_ownership_group_id(db_session, "NOCOMP")
    assert og_id is None


async def test_check_and_record_usage_no_og(db_session: AsyncSession):
    """Company with no ownership group returns zero billing."""
    company = Company(id=COMPANY_ID, name="Solo Corp", slug="solo123")
    db_session.add(company)
    await db_session.commit()

    result = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert result["cost_usd"] == 0
    assert result["charged_usd"] == 0


async def test_check_and_record_usage_within_free_tier(db_session: AsyncSession, seed_og):
    result = await check_and_record_usage(db_session, COMPANY_ID, 1000, 500)
    assert result["cost_usd"] > 0
    assert result["charged_usd"] == 0.0
    assert result["is_over_free_tier"] is False
    assert result["free_remaining_usd"] > 0


async def test_check_and_record_usage_over_free_tier(db_session: AsyncSession, seed_og):
    """Pre-load usage to exhaust free tier, then verify overage markup."""
    now = datetime.now(timezone.utc)
    usage = TokenUsage(
        ownership_group_id=OG_ID,
        year=now.year,
        month=now.month,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=settings.LLM_FREE_TIER_USD + 1.0,
        charged_usd=0.0,
    )
    db_session.add(usage)
    await db_session.commit()

    result = await check_and_record_usage(db_session, COMPANY_ID, 100_000, 10_000)
    assert result["is_over_free_tier"] is True
    assert result["charged_usd"] > 0
    assert result["free_remaining_usd"] == 0


async def test_check_ai_credits_within_free_tier(db_session: AsyncSession, seed_og):
    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["can_generate"] is True
    assert result["is_over_free_tier"] is False
    assert result["free_remaining_usd"] == settings.LLM_FREE_TIER_USD


async def test_check_ai_credits_over_free_tier_no_purchased(db_session: AsyncSession, seed_og):
    now = datetime.now(timezone.utc)
    usage = TokenUsage(
        ownership_group_id=OG_ID,
        year=now.year,
        month=now.month,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=settings.LLM_FREE_TIER_USD + 1.0,
        charged_usd=0.0,
    )
    db_session.add(usage)
    await db_session.commit()

    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["is_over_free_tier"] is True
    assert result["can_generate"] is False


async def test_check_ai_credits_over_free_tier_with_purchased(db_session: AsyncSession, seed_og):
    now = datetime.now(timezone.utc)
    usage = TokenUsage(
        ownership_group_id=OG_ID,
        year=now.year,
        month=now.month,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost_usd=settings.LLM_FREE_TIER_USD + 1.0,
        charged_usd=0.0,
    )
    db_session.add(usage)
    await db_session.flush()

    await add_purchased_credits(db_session, OG_ID, 10.0)
    await db_session.commit()

    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["is_over_free_tier"] is True
    assert result["can_generate"] is True
    assert result["purchased_credits_usd"] == 10.0


async def test_add_purchased_credits(db_session: AsyncSession, seed_og):
    new_balance = await add_purchased_credits(db_session, OG_ID, 5.0)
    assert new_balance == 5.0

    new_balance = await add_purchased_credits(db_session, OG_ID, 3.0)
    assert new_balance == 8.0


async def test_add_purchased_credits_missing_og(db_session: AsyncSession):
    result = await add_purchased_credits(db_session, "NOPE", 10.0)
    assert result == 0.0


async def test_deduct_credits_for_overage(db_session: AsyncSession, seed_og):
    await add_purchased_credits(db_session, OG_ID, 10.0)
    await db_session.commit()

    await deduct_credits_for_overage(db_session, COMPANY_ID, 3.0)
    await db_session.commit()

    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["purchased_credits_usd"] == 7.0


async def test_deduct_credits_zero_charge(db_session: AsyncSession, seed_og):
    await add_purchased_credits(db_session, OG_ID, 10.0)
    await db_session.commit()

    await deduct_credits_for_overage(db_session, COMPANY_ID, 0.0)
    await db_session.commit()

    result = await check_ai_credits(db_session, COMPANY_ID)
    assert result["purchased_credits_usd"] == 10.0


async def test_count_employees_for_group(db_session: AsyncSession, seed_og):
    e1 = Employee(id=_id(), company_id=COMPANY_ID, full_name="A")
    e2 = Employee(id=_id(), company_id=COMPANY_ID, full_name="B")
    db_session.add_all([e1, e2])
    await db_session.commit()

    count = await count_employees_for_group(db_session, OG_ID)
    assert count == 2


async def test_check_schedule_quota_within_free_tier(db_session: AsyncSession, seed_og):
    result = await check_schedule_quota(db_session, COMPANY_ID)
    assert result["can_generate"] is True
    assert result["is_over_free_tier"] is False
    assert result["schedules_used"] == 0
