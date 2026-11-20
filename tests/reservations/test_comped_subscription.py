"""A comped AI host subscription (#259 step 11): the agent answers, usage is
recorded as cost, and Stripe never sees it. Every Stripe path in
services/billing_reservations leaves the row alone without one Stripe call."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.config import settings
from backend.models import Location, OwnershipGroup
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


# ---------------------------------------------------------------------------
# Hard spend ceiling: comped usage is never invoiced, so the operator pays it.
# ---------------------------------------------------------------------------


async def _cap_and_spend(db_session, cap: float, spent: str) -> None:
    from decimal import Decimal

    from backend.reservations.agent.models import AgentConversation
    from backend.reservations.rules import get_rules, save_rules

    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.daily_spend_cap_usd = cap
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    db_session.add(AgentConversation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, channel="web", status="closed",
        outcome="booked", started_at=NOW, cost_usd=Decimal(spent)))
    await db_session.commit()


async def test_a_comped_location_stops_at_the_comped_ceiling_whatever_its_rules_say(
        db_session, comped, monkeypatch):
    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    await _cap_and_spend(db_session, 50, "3.00")
    loc = await db_session.get(Location, LOCATION_ID)
    assert await subscription.cap_reached(db_session, COMPANY_ID, loc, NOW, 0)
    out = await br.status_summary(db_session, AGENT_OG_ID, COMPANY_ID, NOW)
    assert out["locations"][0]["daily_cap_usd"] == 3.0 and out["locations"][0]["cap_reached"]


async def test_a_comped_location_under_the_ceiling_still_answers(db_session, comped):
    await _cap_and_spend(db_session, 50, "2.50")
    loc = await db_session.get(Location, LOCATION_ID)
    assert not await subscription.cap_reached(db_session, COMPANY_ID, loc, NOW, 0)


async def test_a_paying_location_keeps_its_own_cap(db_session, seed_location):
    await subscribe(db_session, "active")
    await _cap_and_spend(db_session, 50, "3.00")
    loc = await db_session.get(Location, LOCATION_ID)
    assert not await subscription.cap_reached(db_session, COMPANY_ID, loc, NOW, 0)
    out = await br.status_summary(db_session, AGENT_OG_ID, COMPANY_ID, NOW)
    assert out["locations"][0]["daily_cap_usd"] == 50.0


async def test_a_live_stripe_subscription_for_a_comped_group_alerts_the_operator(
        db_session, comped, monkeypatch):
    from backend.services import operator_alerts

    sent: list[tuple] = []
    monkeypatch.setattr(operator_alerts, "send_agent_comped_stripe_alert",
                        lambda og_id, sub_id, event_type: sent.append((og_id, sub_id, event_type)))
    before = _snapshot(await _row(db_session))
    out = await br.handle_agent_event(
        db_session, "customer.subscription.created", _agent_subscription("active"))
    assert out["skipped"] == "comped"
    assert sent == [(AGENT_OG_ID, "sub_other", "customer.subscription.created")]
    assert _snapshot(await _row(db_session)) == before
    # An ended one is nothing to act on.
    await br.handle_agent_event(
        db_session, "customer.subscription.deleted", _agent_subscription("canceled"))
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# Today's spend survives erasure: an erased conversation's metered cost still
# counts, so chat, book, erase cannot reset the comped ceiling.
# ---------------------------------------------------------------------------

ERASE_PHONE = "+12125550142"


async def _spend_to_the_ceiling_and_book(db_session, monkeypatch) -> datetime:
    from datetime import timedelta
    from decimal import Decimal

    from backend.reservations.agent.models import AgentConversation
    from backend.reservations.models import BOOKED, SOURCE_WEB_AGENT, Reservation

    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    now = datetime.now(timezone.utc)
    conv = AgentConversation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, channel="web", status="closed",
        outcome="booked", started_at=now, cost_usd=Decimal("3.00"))
    db_session.add(conv)
    await db_session.flush()
    start = now + timedelta(days=3)
    db_session.add(Reservation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=2, start_at=start,
        end_at=start + timedelta(hours=2), status=BOOKED, diner_name="Eve",
        diner_phone=ERASE_PHONE, source=SOURCE_WEB_AGENT, confirmation_code="EVE234",
        conversation_id=conv.id))
    conv.reservation_id = None
    await subscription.meter(db_session, conv, now)
    await db_session.commit()
    loc = await db_session.get(Location, LOCATION_ID)
    assert await subscription.cap_reached(db_session, COMPANY_ID, loc, now, 0)
    return now


async def _conversations_left(db_session) -> int:
    from sqlalchemy import func

    from backend.reservations.agent.models import AgentConversation

    return (await db_session.execute(
        select(func.count()).select_from(AgentConversation)
        .where(AgentConversation.location_id == LOCATION_ID))).scalar_one()


async def test_erasing_by_code_does_not_lift_the_comped_ceiling(
        db_session, comped, monkeypatch):
    from backend.reservations.diner_data import erase_by_code

    now = await _spend_to_the_ceiling_and_book(db_session, monkeypatch)
    await erase_by_code(db_session, COMPANY_ID, LOCATION_ID, "EVE234")
    await db_session.commit()
    assert await _conversations_left(db_session) == 0
    loc = await db_session.get(Location, LOCATION_ID)
    assert await subscription.cap_reached(db_session, COMPANY_ID, loc, now, 0)


async def test_erasing_by_phone_does_not_lift_the_comped_ceiling(
        db_session, comped, monkeypatch):
    from backend.reservations.diner_data import erase_diner_data

    now = await _spend_to_the_ceiling_and_book(db_session, monkeypatch)
    await erase_diner_data(db_session, COMPANY_ID, LOCATION_ID, ERASE_PHONE)
    await db_session.commit()
    assert await _conversations_left(db_session) == 0
    loc = await db_session.get(Location, LOCATION_ID)
    assert await subscription.cap_reached(db_session, COMPANY_ID, loc, now, 0)
