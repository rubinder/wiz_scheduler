"""Plan + upgrade endpoints on the billing router."""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, User
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def free_tenant(db_session: AsyncSession) -> dict:
    og_id, company_id, user_id = _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(User(id=user_id, company_id=company_id,
                        email="m@x.test", hashed_password="x",
                        full_name="M", user_role="manager"))
    await db_session.commit()
    return {
        "og_id": og_id,
        "company_id": company_id,
        "token": _make_token(user_id, company_id, "manager"),
    }


async def test_get_plan_returns_free_state(
    client: AsyncClient, free_tenant: dict
):
    resp = await client.get(
        "/api/v1/billing/plan",
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "free"
    assert body["employees"]["limit"] == 5
    assert body["locations"]["limit"] == 1
    assert body["can_generate_ai"] is False


async def test_upgrade_checkout_rejects_already_paid(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict, monkeypatch
):
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(_s, "STRIPE_PRICE_ID", "price_x")

    og = await db_session.get(OwnershipGroup, free_tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    await db_session.commit()

    resp = await client.post(
        "/api/v1/billing/upgrade-checkout",
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 400
    assert "already" in resp.json()["detail"].lower()


async def test_upgrade_checkout_free_group_uses_customer_email(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict, monkeypatch
):
    """A never-touched-Stripe free group has no stripe_customer_id, so Checkout
    must be created with customer_email (the manager's email), not `customer`."""
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(_s, "STRIPE_PRICE_ID", "price_x")

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return MagicMock(id="cs_test_upgrade_1", url="https://checkout.stripe.com/cs_test_upgrade_1")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)

    resp = await client.post(
        "/api/v1/billing/upgrade-checkout",
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "cs_test_upgrade_1"
    assert body["url"] == "https://checkout.stripe.com/cs_test_upgrade_1"

    assert "customer" not in captured
    assert captured["customer_email"] == "m@x.test"
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_x", "quantity": 1}]


async def test_confirm_upgrade_happy_path_attaches_customer_and_subscription(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict, monkeypatch
):
    """confirm-upgrade with a paid session attaches the SPECIFIC mocked
    customer/subscription ids to the ownership group row."""
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_new_upgrade_777",
        subscription="sub_new_upgrade_888",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/billing/confirm-upgrade",
        json={"session_id": "cs_test_upgrade_1"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upgraded"] is True
    assert body["subscription_id"] == "sub_new_upgrade_888"

    og = await db_session.get(OwnershipGroup, free_tenant["og_id"])
    await db_session.refresh(og)
    assert og.stripe_customer_id == "cus_new_upgrade_777"
    assert og.stripe_subscription_id == "sub_new_upgrade_888"
    assert og.canceled_at is None


async def test_confirm_upgrade_rejects_unpaid_session_and_leaves_group_unchanged(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict, monkeypatch
):
    """An unpaid session is rejected with 400 and does not mutate the group's
    Stripe fields."""
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    fake_session = MagicMock(
        payment_status="unpaid",
        customer="cus_should_not_be_used",
        subscription="sub_should_not_be_used",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/billing/confirm-upgrade",
        json={"session_id": "cs_test_unpaid"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 400

    og = await db_session.get(OwnershipGroup, free_tenant["og_id"])
    await db_session.refresh(og)
    assert og.stripe_customer_id is None
    assert og.stripe_subscription_id is None


async def test_confirm_upgrade_rejects_session_for_different_customer(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict, monkeypatch
):
    """When the group already has a stripe_customer_id (e.g. previously
    canceled), a paid session belonging to a different customer is rejected."""
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    og = await db_session.get(OwnershipGroup, free_tenant["og_id"])
    og.stripe_customer_id = "cus_existing_owner"
    await db_session.commit()

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_someone_else",
        subscription="sub_someone_else",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/billing/confirm-upgrade",
        json={"session_id": "cs_test_mismatch"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 400

    await db_session.refresh(og)
    assert og.stripe_customer_id == "cus_existing_owner"
    assert og.stripe_subscription_id is None
