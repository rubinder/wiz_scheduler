"""A comped AI host subscription (#259 step 11): the agent answers, usage is
recorded as cost, and Stripe never sees it. Every Stripe path in
services/billing_reservations leaves the row alone without one Stripe call."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.config import settings
from backend.models import OwnershipGroup
from backend.reservations import subscription
from backend.reservations.subscription import ReservationSubscription
from backend.services import billing_reservations as br
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = pytest.mark.asyncio

CUSTOMER = "cus_comped"
NOW = datetime(2026, 11, 19, 15, tzinfo=timezone.utc)


class _NoStripe:
    """A Stripe client whose every call fails the test."""

    def __getattr__(self, name: str):
        pytest.fail(f"Stripe was called for a comped subscription ({name})")


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_PRICE_ID", "price_agent")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(br, "_client", lambda: _NoStripe())


@pytest_asyncio.fixture
async def comped(db_session, seed_location) -> ReservationSubscription:
    sub = await subscribe(db_session, "comped")
    # A Stripe customer from the scheduling side, so an agent object naming
    # this group by metadata would be accepted as the group's.
    (await db_session.get(OwnershipGroup, AGENT_OG_ID)).stripe_customer_id = CUSTOMER
    await db_session.commit()
    return sub


def _snapshot(row: ReservationSubscription) -> tuple:
    return (row.id, row.status, row.stripe_subscription_id, row.current_period_start,
            row.current_period_end, row.cancel_at_period_end, row.updated_at)


async def _row(db_session) -> ReservationSubscription:
    db_session.expire_all()
    return (await db_session.execute(
        select(ReservationSubscription)
        .where(ReservationSubscription.ownership_group_id == AGENT_OG_ID))).scalar_one()


def _agent_subscription(status: str = "active") -> dict:
    return {"id": "sub_other", "object": "subscription", "customer": CUSTOMER,
            "status": status, "cancel_at_period_end": False,
            "metadata": {"product": "reservation_agent", "og_id": AGENT_OG_ID},
            "items": {"data": []}}


async def test_comped_answers_but_is_never_billed():
    assert "comped" in subscription.STATUSES
    assert "comped" in subscription.ANSWERING_STATUSES
    assert "comped" not in subscription.BILLABLE_STATUSES


async def test_a_comped_group_is_answered(db_session, comped):
    found = await subscription.active_for_location(db_session, COMPANY_ID, LOCATION_ID, NOW)
    assert found is not None and found.status == "comped"


async def test_overage_reconcile_leaves_a_comped_row_unchanged(db_session, comped):
    before = _snapshot(await _row(db_session))
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    out = await br.reconcile_agent_overage(db_session, og, NOW)
    assert out["skipped"] == "comped"
    assert _snapshot(await _row(db_session)) == before


async def test_daily_reconcile_leaves_a_comped_row_unchanged(db_session, comped):
    before = _snapshot(await _row(db_session))
    out = await br.reconcile_all_agent_subscriptions(db_session, NOW)
    assert out == {"synced": [], "reconciled": [], "finalized": [], "errors": []}
    assert _snapshot(await _row(db_session)) == before


@pytest.mark.parametrize("event_type", [
    "customer.subscription.created", "customer.subscription.updated",
    "customer.subscription.deleted",
])
async def test_webhook_apply_leaves_a_comped_row_unchanged(db_session, comped, event_type):
    before = _snapshot(await _row(db_session))
    out = await br.handle_agent_event(db_session, event_type, _agent_subscription())
    assert out == {"handled": False, "og_id": AGENT_OG_ID, "skipped": "comped"}
    assert _snapshot(await _row(db_session)) == before


async def test_upcoming_invoice_leaves_a_comped_row_unchanged(db_session, comped):
    before = _snapshot(await _row(db_session))
    invoice = {"id": "in_1", "object": "invoice", "customer": CUSTOMER, "lines": {"data": []},
               "parent": {"subscription_details": {
                   "subscription": "sub_other",
                   "metadata": {"product": "reservation_agent", "og_id": AGENT_OG_ID}}}}
    out = await br.handle_agent_event(db_session, "invoice.upcoming", invoice)
    assert out["skipped"] == "comped"
    assert _snapshot(await _row(db_session)) == before


async def test_final_invoice_skips_a_comped_group(db_session, comped):
    before = _snapshot(await _row(db_session))
    out = await br.finalize_agent_billing(db_session, AGENT_OG_ID, "sub_other")
    assert out["skipped"] == "comped"
    assert _snapshot(await _row(db_session)) == before


async def test_checkout_is_refused_for_a_comped_group(db_session, comped):
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    with pytest.raises(br.AlreadySubscribed):
        await br.create_checkout(db_session, og, "owner@example.com")
    assert (await _row(db_session)).status == "comped"


async def test_a_deleted_comped_group_cancels_nothing_in_stripe(db_session, comped):
    assert await br.cancel_for_deleted_group(db_session, AGENT_OG_ID) is False


async def test_status_summary_says_comped_and_answering(db_session, comped):
    out = await br.status_summary(db_session, AGENT_OG_ID, COMPANY_ID, NOW)
    assert out["status"] == "comped" and out["answering"] is True
    assert out["current_period_end"] is None and out["cancel_at_period_end"] is False


async def test_company_without_comped_group_is_not_answered(db_session, seed_location):
    # The control: the same location with no subscription is not answered.
    assert await subscription.active_for_location(
        db_session, COMPANY_ID, LOCATION_ID, NOW) is None


async def test_checkout_confirm_is_refused_for_a_comped_group(db_session, comped):
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    with pytest.raises(br.AlreadySubscribed):
        await br.confirm_checkout(db_session, og, "cs_1")
    assert (await _row(db_session)).status == "comped"
