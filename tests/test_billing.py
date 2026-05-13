"""Tests for backend/services/billing.py."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from backend.config import settings
from backend.models import Company, Employee, TokenUsage
from backend.models.billing_charge import BillingCharge
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
async def seed_og(db_session: AsyncSession, seed_company: Company):
    """Ownership group linked to the canonical test Company.

    Depends on `seed_company` so we don't double-insert the Company row when
    fixtures like `manager_token` (which also needs the Company) are requested.
    """
    og = OwnershipGroup(id=OG_ID, name="Test Group", ai_credits_usd=0.0)
    db_session.add(og)
    await db_session.flush()

    seed_company.ownership_group_id = OG_ID
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
    # Pre-fund the credit buffer so the new auto-reload guard sees sufficient
    # balance and skips. This test exercises the markup calculation, not reload.
    seed_og.ai_credits_usd = 100.0
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


async def test_billing_charge_model_round_trips(db_session: AsyncSession, seed_og):
    """BillingCharge inserts and reads back via SQLAlchemy."""
    charge = BillingCharge(
        ownership_group_id=OG_ID,
        kind="autoreload",
        amount_usd=10.0,
        stripe_object_id="pi_test_123",
        status="succeeded",
    )
    db_session.add(charge)
    await db_session.commit()

    result = await db_session.execute(
        select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID)
    )
    rows = list(result.scalars())
    assert len(rows) == 1
    assert rows[0].kind == "autoreload"
    assert float(rows[0].amount_usd) == 10.0
    assert rows[0].status == "succeeded"


async def test_cache_default_payment_method_writes_pm_id(
    db_session: AsyncSession, seed_og, monkeypatch
):
    """cache_default_payment_method retrieves the subscription's default PM and stores it on the OG."""
    import stripe

    # Mock the OG having a Stripe subscription_id
    seed_og.stripe_subscription_id = "sub_test_123"
    await db_session.commit()

    fake_sub = MagicMock()
    fake_sub.default_payment_method = "pm_test_card_456"
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: fake_sub)

    from backend.services.billing import cache_default_payment_method
    pm_id = await cache_default_payment_method(db_session, seed_og)

    assert pm_id == "pm_test_card_456"
    await db_session.refresh(seed_og)
    assert seed_og.default_payment_method_id == "pm_test_card_456"


async def test_cache_default_payment_method_no_subscription_returns_none(
    db_session: AsyncSession, seed_og
):
    """If the OG has no stripe_subscription_id, return None without calling Stripe."""
    from backend.services.billing import cache_default_payment_method
    result = await cache_default_payment_method(db_session, seed_og)
    assert result is None


from unittest.mock import MagicMock


@pytest_asyncio.fixture
async def og_with_card(db_session: AsyncSession, seed_og):
    """OG with stripe_customer_id and a cached payment method."""
    seed_og.stripe_customer_id = "cus_test_abc"
    seed_og.stripe_subscription_id = "sub_test_123"
    seed_og.default_payment_method_id = "pm_test_card_456"
    await db_session.commit()
    return seed_og


async def test_auto_reload_charges_card_and_adds_to_balance(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Successful PaymentIntent should add autoreload_amount_usd to ai_credits_usd
    and record a BillingCharge row with status='succeeded'."""
    import stripe
    from backend.services.billing import auto_reload_if_needed
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    fake_intent = MagicMock(status="succeeded", id="pi_test_999")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kwargs: fake_intent)

    og_with_card.ai_credits_usd = 0.0
    og_with_card.autoreload_amount_usd = 10.0
    og_with_card.autoreload_threshold_usd = 2.0
    await db_session.commit()

    # cost > balance, so reload should fire
    await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd == 10.0
    assert og_with_card.autoreload_failed_at is None

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    charges = list(result.scalars())
    assert len(charges) == 1
    assert charges[0].kind == "autoreload"
    assert charges[0].status == "succeeded"
    assert charges[0].stripe_object_id == "pi_test_999"


async def test_auto_reload_skipped_when_balance_sufficient(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """If balance > cost + threshold, no Stripe call and no charge row."""
    import stripe
    from backend.services.billing import auto_reload_if_needed
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    called = {"count": 0}
    def fake_create(**kwargs):
        called["count"] += 1
        return MagicMock(status="succeeded", id="pi_x")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.ai_credits_usd = 20.0
    og_with_card.autoreload_threshold_usd = 2.0
    await db_session.commit()

    await auto_reload_if_needed(db_session, og_with_card, cost_usd=1.0)
    assert called["count"] == 0

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    assert list(result.scalars()) == []


async def test_auto_reload_failure_sets_failed_at_and_raises(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """A Stripe CardError on PaymentIntent.create should set autoreload_failed_at,
    record a 'failed' BillingCharge, and raise."""
    import stripe
    from backend.services.billing import auto_reload_if_needed, AutoReloadError
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    def fake_create(**kwargs):
        raise stripe.CardError("card declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)

    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadError):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None
    assert og_with_card.ai_credits_usd == 0.0  # nothing added

    result = await db_session.execute(select(BillingCharge).where(BillingCharge.ownership_group_id == OG_ID))
    charges = list(result.scalars())
    assert len(charges) == 1
    assert charges[0].status == "failed"


async def test_auto_reload_disabled_raises_blocked_error(
    db_session: AsyncSession, og_with_card
):
    """When autoreload_enabled=False and a reload is needed, raise AutoReloadDisabled."""
    from backend.services.billing import auto_reload_if_needed, AutoReloadDisabled

    og_with_card.autoreload_enabled = False
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadDisabled):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)


async def test_auto_reload_failed_state_raises_blocked_error(
    db_session: AsyncSession, og_with_card
):
    """When autoreload_failed_at is set, every call raises AutoReloadBlocked."""
    from backend.services.billing import auto_reload_if_needed, AutoReloadBlocked

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    with pytest.raises(AutoReloadBlocked):
        await auto_reload_if_needed(db_session, og_with_card, cost_usd=5.0)


async def test_check_and_record_usage_triggers_reload_when_over_free_tier(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """When LLM usage takes balance below threshold, auto_reload_if_needed is called
    BEFORE the debit, and the debit succeeds against the topped-up balance."""
    import stripe
    from backend.services.billing import check_and_record_usage
    from backend.models.billing_charge import BillingCharge
    from sqlalchemy import select

    fake_intent = MagicMock(status="succeeded", id="pi_reload_1")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    # Pre-exhaust the free tier so any usage triggers a charge
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()
    usage = TokenUsage(
        ownership_group_id=OG_ID,
        year=datetime.now(timezone.utc).year,
        month=datetime.now(timezone.utc).month,
        input_tokens=1_000_000_000,  # absurd to ensure cost > free tier
        output_tokens=0,
        total_tokens=1_000_000_000,
        cost_usd=settings.LLM_FREE_TIER_USD + 5.0,
        charged_usd=5.0,
    )
    db_session.add(usage)
    await db_session.commit()

    # This call should trigger auto-reload before debit
    result = await check_and_record_usage(db_session, str(COMPANY_ID), 1_000_000, 0)

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd > 0  # reload happened, then debit applied
    charges = list((await db_session.execute(select(BillingCharge))).scalars())
    assert any(c.kind == "autoreload" and c.status == "succeeded" for c in charges)


async def test_deduct_credits_for_schedule_triggers_reload(
    db_session: AsyncSession, og_with_card, monkeypatch
):
    """Schedule overage debit triggers auto-reload when balance is insufficient."""
    import stripe
    from backend.services.billing import deduct_credits_for_schedule_overage
    from backend.models import ShiftSchedule

    fake_intent = MagicMock(status="succeeded", id="pi_sched_reload")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    # Create > free tier schedules so overage applies
    now = datetime.now(timezone.utc)
    for _ in range(settings.SCHEDULE_FREE_TIER + 1):
        db_session.add(ShiftSchedule(
            company_id=COMPANY_ID,
            location_id=_id(),
            week_start_date=now.date(),
            status="DRAFT",
            created_at=now,
        ))
    await db_session.commit()

    await deduct_credits_for_schedule_overage(db_session, str(COMPANY_ID))

    await db_session.refresh(og_with_card)
    assert og_with_card.ai_credits_usd > 0  # reload happened


async def test_check_ai_credits_blocked_when_failed_at_set(
    db_session: AsyncSession, og_with_card
):
    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    status = await check_ai_credits(db_session, str(COMPANY_ID))
    assert status["can_generate"] is False
    assert status.get("autoreload_failed") is True


async def test_check_schedule_quota_blocked_when_failed_at_set(
    db_session: AsyncSession, og_with_card
):
    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    status = await check_schedule_quota(db_session, str(COMPANY_ID))
    assert status["can_generate"] is False
    assert status.get("autoreload_failed") is True


# ---------------------------------------------------------------------------
# Router endpoint tests (Tasks 9-12)
# ---------------------------------------------------------------------------

from httpx import AsyncClient


async def test_get_autoreload_returns_settings_and_balance(
    client: AsyncClient, manager_token, og_with_card
):
    response = await client.get(
        "/api/v1/billing/autoreload",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["threshold_usd"] == 2.0
    assert body["amount_usd"] == 10.0
    assert "current_balance_usd" in body
    assert body["failed_at"] is None


async def test_put_autoreload_updates_settings(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    response = await client.put(
        "/api/v1/billing/autoreload",
        json={"enabled": True, "threshold_usd": 5.0, "amount_usd": 25.0},
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_usd"] == 5.0
    assert body["amount_usd"] == 25.0

    await db_session.refresh(og_with_card)
    assert float(og_with_card.autoreload_threshold_usd) == 5.0
    assert float(og_with_card.autoreload_amount_usd) == 25.0


async def test_put_autoreload_rejects_invalid_values(
    client: AsyncClient, manager_token, og_with_card
):
    response = await client.put(
        "/api/v1/billing/autoreload",
        json={"amount_usd": 0.25},  # below Stripe's $0.50 minimum
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 422


async def test_post_autoreload_retry_succeeds(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    fake_intent = MagicMock(status="succeeded", id="pi_retry_ok")
    monkeypatch.setattr(stripe.PaymentIntent, "create", lambda **kw: fake_intent)

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    og_with_card.ai_credits_usd = 0.0
    await db_session.commit()

    response = await client.post(
        "/api/v1/billing/autoreload/retry",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    assert response.json()["failed_at"] is None

    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is None
    assert og_with_card.ai_credits_usd > 0


async def test_post_autoreload_retry_declined_keeps_failed_state(
    client: AsyncClient, manager_token, db_session, og_with_card, monkeypatch
):
    import stripe
    def boom(**kw):
        raise stripe.CardError("declined", "card_declined", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", boom)

    og_with_card.autoreload_failed_at = datetime.now(timezone.utc)
    await db_session.commit()

    response = await client.post(
        "/api/v1/billing/autoreload/retry",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 402
    await db_session.refresh(og_with_card)
    assert og_with_card.autoreload_failed_at is not None


async def test_get_billing_charges_returns_recent_rows(
    client: AsyncClient, manager_token, db_session, og_with_card
):
    for i in range(3):
        db_session.add(BillingCharge(
            ownership_group_id=OG_ID,
            kind="autoreload",
            amount_usd=10.0,
            stripe_object_id=f"pi_{i}",
            status="succeeded",
        ))
    await db_session.commit()

    response = await client.get(
        "/api/v1/billing/charges?limit=10",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["charges"]) == 3
    assert all(c["kind"] == "autoreload" for c in body["charges"])


async def test_get_portal_link_returns_url(
    client: AsyncClient, manager_token, og_with_card, monkeypatch
):
    import stripe
    fake_session = MagicMock(url="https://billing.stripe.com/session_abc")
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: fake_session)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    response = await client.get(
        "/api/v1/billing/portal-link",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://billing.stripe.com/")


async def test_get_portal_link_400_without_stripe_customer(
    client: AsyncClient, manager_token, seed_og
):
    # seed_og (without _with_card) has no stripe_customer_id
    response = await client.get(
        "/api/v1/billing/portal-link",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert response.status_code == 400
