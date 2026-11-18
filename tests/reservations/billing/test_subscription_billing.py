"""AI host subscription: checkout, confirm, status and webhook routing
(#259 step 5, Task 2). Stripe is mocked the way tests/test_billing_integration
.py mocks it (monkeypatched SDK classes, webhook events as dicts)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
import stripe
from sqlalchemy import func, select

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.reservations.agent.models import AgentConversation
from backend.reservations.subscription import AgentUsage, ReservationSubscription
from backend.models import Company
from tests.conftest import COMPANY_ID, LOCATION_ID

OG_ID = "ogsched1"
pytestmark = pytest.mark.asyncio

P_START = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
P_END = datetime(2026, 12, 10, 12, tzinfo=timezone.utc)
SCHED_SUB = "sub_sched"
AGENT_SUB = "sub_agent"
CUSTOMER = "cus_og"
BASE = "/api/v1/billing/reservation-agent"


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "RESERVATIONS_PRICE_ID", "price_agent")
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")


@pytest_asyncio.fixture
async def og(db_session, seed_location, seed_manager) -> OwnershipGroup:
    """A paid scheduling group with a Stripe customer, its scheduling
    subscription, and auto-reload state that must never move."""
    group = OwnershipGroup(id=OG_ID, name="Test Group", ai_credits_usd=0.0,
                           stripe_customer_id=CUSTOMER, stripe_subscription_id=SCHED_SUB)
    db_session.add(group)
    await db_session.flush()
    (await db_session.get(Company, COMPANY_ID)).ownership_group_id = OG_ID
    await db_session.commit()
    return group


async def _row(db_session) -> ReservationSubscription | None:
    db_session.expire_all()
    return (await db_session.execute(
        select(ReservationSubscription)
        .where(ReservationSubscription.ownership_group_id == OG_ID))).scalar_one_or_none()


async def _add_row(db_session, status="active", sub_id=AGENT_SUB) -> None:
    db_session.add(ReservationSubscription(
        ownership_group_id=OG_ID, stripe_subscription_id=sub_id, status=status,
        current_period_start=P_START, current_period_end=P_END))
    await db_session.commit()


async def _assert_scheduling_untouched(db_session) -> None:
    db_session.expire_all()
    g = await db_session.get(OwnershipGroup, OG_ID)
    assert g.autoreload_failed_at is None
    assert g.canceled_at is None
    assert g.stripe_subscription_id == SCHED_SUB


def _subscription(sub_id=AGENT_SUB, status="active", *, items_period=True,
                  cancel=False, metadata=None) -> dict:
    """An API 2025-03-31+ subscription: period on the items."""
    sub = {"id": sub_id, "object": "subscription", "customer": CUSTOMER,
           "status": status, "cancel_at_period_end": cancel,
           "metadata": metadata if metadata is not None
           else {"product": "reservation_agent", "og_id": OG_ID},
           "items": {"data": [{"current_period_start": _ts(P_START),
                               "current_period_end": _ts(P_END)}]}}
    if not items_period:
        sub["items"] = {"data": []}
        sub["current_period_start"] = _ts(P_START)
        sub["current_period_end"] = _ts(P_END)
    return sub


def _session(sub_id=AGENT_SUB, *, customer=CUSTOMER, mode="subscription",
             product="reservation_agent") -> dict:
    return {"id": "cs_1", "object": "checkout.session", "mode": mode,
            "customer": customer, "subscription": sub_id, "status": "complete",
            "payment_status": "paid",
            "metadata": {"product": product, "og_id": OG_ID}}


def _invoice(sub_id=AGENT_SUB, *, new_api=True) -> dict:
    inv = {"id": "in_1", "object": "invoice", "customer": CUSTOMER,
           "lines": {"data": []}}
    if new_api:
        inv["parent"] = {"subscription_details": {"subscription": sub_id, "metadata": {}}}
    else:
        inv["subscription"] = sub_id
    return inv


async def _post(client, monkeypatch, event_type: str, obj: dict):
    event = {"type": event_type, "data": {"object": obj}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: event)
    return await client.post("/api/v1/webhooks/stripe", content=b"{}", headers={
        "stripe-signature": "t=1,v1=x", "content-type": "application/json"})


@pytest.fixture
def retrieve_sub(monkeypatch):
    """stripe.Subscription.retrieve returns whatever `state["sub"]` holds."""
    state = {"sub": _subscription(), "calls": []}

    def fake(sub_id):
        state["calls"].append(sub_id)
        return state["sub"]
    monkeypatch.setattr(stripe.Subscription, "retrieve", fake)
    return state


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


async def test_checkout_session_parameters(client, manager_token, og, monkeypatch):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(id="cs_1", url="https://checkout.stripe.test/cs_1")
    monkeypatch.setattr(stripe.checkout.Session, "create", create)

    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://checkout.stripe.test/cs_1"}
    assert seen["mode"] == "subscription"
    assert seen["customer"] == CUSTOMER
    assert seen["line_items"] == [{"price": "price_agent", "quantity": 1}]
    assert "payment_method_types" not in seen
    md = {"product": "reservation_agent", "og_id": OG_ID}
    assert seen["subscription_data"] == {"metadata": md}
    assert seen["metadata"] == md
    assert "agent_checkout=success" in seen["success_url"]
    assert "session_id={CHECKOUT_SESSION_ID}" in seen["success_url"]
    assert "agent_checkout=cancel" in seen["cancel_url"]


async def test_checkout_creates_a_customer_for_a_group_without_one(
    client, manager_token, og, db_session, monkeypatch,
):
    og.stripe_customer_id = None
    og.stripe_subscription_id = None
    await db_session.commit()
    created = {}

    def make_customer(**kw):
        created.update(kw)
        return SimpleNamespace(id="cus_new")
    seen = {}
    monkeypatch.setattr(stripe.Customer, "create", make_customer)
    monkeypatch.setattr(stripe.checkout.Session, "create",
                        lambda **kw: seen.update(kw) or SimpleNamespace(url="u"))

    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert created["metadata"] == {"og_id": OG_ID}
    assert seen["customer"] == "cus_new"
    db_session.expire_all()
    assert (await db_session.get(OwnershipGroup, OG_ID)).stripe_customer_id == "cus_new"


async def test_checkout_503_when_price_unset(client, manager_token, og, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_PRICE_ID", "")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 503
    assert r.json()["detail"] == "not_configured"


async def test_checkout_404_when_flag_off(client, manager_token, og, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    assert (await client.post(f"{BASE}/checkout", headers=_auth(manager_token))).status_code == 404
    assert (await client.get(BASE, headers=_auth(manager_token))).status_code == 404


async def test_checkout_refused_to_a_regular_manager(client, regular_token, og):
    r = await client.post(f"{BASE}/checkout", headers=_auth(regular_token))
    assert r.status_code == 403


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
async def test_second_subscribe_while_live_is_409(
    client, manager_token, og, db_session, monkeypatch, status,
):
    await _add_row(db_session, status)
    monkeypatch.setattr(stripe.checkout.Session, "create",
                        lambda **kw: pytest.fail("no session for a live subscription"))
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 409
    assert r.json()["detail"] == "already_subscribed"


async def test_resubscribe_after_canceled_replaces_the_row(
    client, manager_token, og, db_session, monkeypatch, retrieve_sub,
):
    await _add_row(db_session, "canceled", sub_id="sub_old")
    monkeypatch.setattr(stripe.checkout.Session, "create", lambda **kw: SimpleNamespace(url="u"))
    assert (await client.post(f"{BASE}/checkout", headers=_auth(manager_token))).status_code == 200

    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: _session("sub_new"))
    retrieve_sub["sub"] = _subscription("sub_new")
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == ("sub_new", "active")
    assert (await db_session.execute(
        select(func.count()).select_from(ReservationSubscription))).scalar_one() == 1


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------


async def test_confirm_records_the_subscription_then_the_webhook_changes_nothing(
    client, manager_token, og, db_session, monkeypatch, retrieve_sub,
):
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: _session())
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    row = await _row(db_session)
    first = (row.id, row.stripe_subscription_id, row.status,
             row.current_period_start, row.current_period_end)
    assert first[1:] == (AGENT_SUB, "active", P_START, P_END)

    r = await _post(client, monkeypatch, "checkout.session.completed", _session())
    assert r.status_code == 200
    assert r.json()["reservation_agent"]["handled"] is True
    row = await _row(db_session)
    assert (row.id, row.stripe_subscription_id, row.status,
            row.current_period_start, row.current_period_end) == first
    await _assert_scheduling_untouched(db_session)


async def test_webhook_first_then_confirm_is_one_row(
    client, manager_token, og, db_session, monkeypatch, retrieve_sub,
):
    assert (await _post(client, monkeypatch, "checkout.session.completed", _session())).status_code == 200
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: _session())
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert (await db_session.execute(
        select(func.count()).select_from(ReservationSubscription))).scalar_one() == 1
    assert (await _row(db_session)).status == "active"


@pytest.mark.parametrize("session", [
    _session(customer="cus_someone_else"),
    _session(mode="payment"),
    _session(product="something_else"),
    _session(sub_id=None),
])
async def test_confirm_rejects_a_session_that_is_not_ours(
    client, manager_token, og, db_session, monkeypatch, retrieve_sub, session,
):
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: session)
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 400
    assert await _row(db_session) is None


# ---------------------------------------------------------------------------
# Webhook: each agent event updates only the agent row
# ---------------------------------------------------------------------------


async def test_subscription_created_inserts_the_row_from_metadata(
    client, og, db_session, monkeypatch,
):
    r = await _post(client, monkeypatch, "customer.subscription.created",
                    _subscription(status="incomplete"))
    assert r.json()["reservation_agent"]["handled"] is True
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == (AGENT_SUB, "incomplete")
    assert (row.current_period_start, row.current_period_end) == (P_START, P_END)
    await _assert_scheduling_untouched(db_session)


async def test_subscription_created_for_another_customer_is_ignored(
    client, og, db_session, monkeypatch,
):
    sub = _subscription()
    sub["customer"] = "cus_intruder"
    await _post(client, monkeypatch, "customer.subscription.created", sub)
    assert await _row(db_session) is None
    await _assert_scheduling_untouched(db_session)


async def test_subscription_updated_syncs_status_period_and_cancel_flag(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "incomplete")
    # Older API version: period at the top level; no metadata, matched by id.
    sub = _subscription(items_period=False, cancel=True, metadata={})
    r = await _post(client, monkeypatch, "customer.subscription.updated", sub)
    assert r.json()["reservation_agent"]["handled"] is True
    row = await _row(db_session)
    assert row.status == "active"
    assert row.cancel_at_period_end is True
    assert (row.current_period_start, row.current_period_end) == (P_START, P_END)
    await _assert_scheduling_untouched(db_session)


async def test_subscription_deleted_cancels_only_the_agent(client, og, db_session, monkeypatch):
    await _add_row(db_session, "active")
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    assert r.json()["reservation_agent"]["handled"] is True
    assert (await _row(db_session)).status == "canceled"
    await _assert_scheduling_untouched(db_session)


async def test_deleting_an_older_agent_subscription_keeps_the_current_one(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "active", sub_id="sub_current")
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription("sub_older", status="canceled"))
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == ("sub_current", "active")
    await _assert_scheduling_untouched(db_session)


async def test_agent_deletion_on_a_free_group_never_cancels_scheduling(
    client, og, db_session, monkeypatch,
):
    """The scheduling branch cancels a group with no stripe_subscription_id
    on any deletion of its customer; the agent's deletion must not reach it."""
    og.stripe_subscription_id = None
    await db_session.commit()
    await _add_row(db_session, "active")
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription(status="canceled"))
    db_session.expire_all()
    g = await db_session.get(OwnershipGroup, OG_ID)
    assert g.canceled_at is None
    assert (await _row(db_session)).status == "canceled"


@pytest.mark.parametrize("new_api", [True, False])
async def test_invoice_payment_failed_sets_past_due_only(
    client, og, db_session, monkeypatch, new_api,
):
    await _add_row(db_session, "active")
    r = await _post(client, monkeypatch, "invoice.payment_failed", _invoice(new_api=new_api))
    assert r.json()["reservation_agent"]["handled"] is True
    assert (await _row(db_session)).status == "past_due"
    await _assert_scheduling_untouched(db_session)


async def test_invoice_payment_failed_leaves_an_incomplete_subscription_incomplete(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "incomplete")
    await _post(client, monkeypatch, "invoice.payment_failed", _invoice())
    assert (await _row(db_session)).status == "incomplete"
    await _assert_scheduling_untouched(db_session)


async def test_invoice_payment_succeeded_syncs_the_period(
    client, og, db_session, monkeypatch, retrieve_sub,
):
    await _add_row(db_session, "past_due")
    nxt_start, nxt_end = P_END, P_END + timedelta(days=31)
    sub = _subscription()
    sub["items"]["data"][0].update(current_period_start=_ts(nxt_start),
                                   current_period_end=_ts(nxt_end))
    retrieve_sub["sub"] = sub
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", _invoice())
    assert r.json()["reservation_agent"]["handled"] is True
    assert retrieve_sub["calls"] == [AGENT_SUB]
    row = await _row(db_session)
    assert row.status == "active"
    assert (row.current_period_start, row.current_period_end) == (nxt_start, nxt_end)
    await _assert_scheduling_untouched(db_session)


async def test_invoice_upcoming_reconciles_the_agent_not_the_scheduling_overage(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "active")

    async def scheduling(*a, **k):
        pytest.fail("scheduling overage must not run for an agent invoice")
    monkeypatch.setattr("backend.routers.webhooks.bill_monthly_overages_for_og", scheduling)
    r = await _post(client, monkeypatch, "invoice.upcoming", _invoice())
    body = r.json()["reservation_agent"]
    assert body["handled"] is True
    assert body["overage"] == {"skipped": "not_implemented"}
    await _assert_scheduling_untouched(db_session)


async def test_checkout_completed_webhook_records_the_agent_only(
    client, og, db_session, monkeypatch, retrieve_sub,
):
    r = await _post(client, monkeypatch, "checkout.session.completed", _session())
    assert r.json()["reservation_agent"]["handled"] is True
    assert (await _row(db_session)).status == "active"
    await _assert_scheduling_untouched(db_session)


# ---------------------------------------------------------------------------
# Scheduling events still take the scheduling path
# ---------------------------------------------------------------------------


async def test_scheduling_invoice_failure_still_holds_autoreload_and_leaves_the_agent(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "active")
    r = await _post(client, monkeypatch, "invoice.payment_failed", _invoice(SCHED_SUB))
    assert "reservation_agent" not in r.json()
    db_session.expire_all()
    assert (await db_session.get(OwnershipGroup, OG_ID)).autoreload_failed_at is not None
    assert (await _row(db_session)).status == "active"


async def test_scheduling_invoice_upcoming_still_runs_scheduling_overage(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "active")
    calls = []

    async def scheduling(db, g, period=None):
        calls.append(g.id)
        return {}
    monkeypatch.setattr("backend.routers.webhooks.bill_monthly_overages_for_og", scheduling)
    await _post(client, monkeypatch, "invoice.upcoming", _invoice(SCHED_SUB))
    assert calls == [OG_ID]


async def test_scheduling_subscription_deleted_still_cancels_scheduling(
    client, og, db_session, monkeypatch,
):
    await _add_row(db_session, "active")
    sub = _subscription(SCHED_SUB, status="canceled", metadata={})
    sub["ended_at"] = _ts(datetime(2026, 11, 18, tzinfo=timezone.utc))
    r = await _post(client, monkeypatch, "customer.subscription.deleted", sub)
    assert "reservation_agent" not in r.json()
    db_session.expire_all()
    assert (await db_session.get(OwnershipGroup, OG_ID)).canceled_at is not None
    assert (await _row(db_session)).status == "active"


async def test_non_agent_checkout_session_is_not_routed(client, og, db_session, monkeypatch):
    r = await _post(client, monkeypatch, "checkout.session.completed",
                    {"id": "cs_up", "mode": "subscription", "customer": CUSTOMER,
                     "subscription": "sub_upgrade", "metadata": {}})
    assert "reservation_agent" not in r.json()
    assert await _row(db_session) is None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_status_reports_usage_and_spend_against_cap(
    client, manager_token, og, db_session,
):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=3)
    db_session.add(ReservationSubscription(
        ownership_group_id=OG_ID, stripe_subscription_id=AGENT_SUB, status="active",
        current_period_start=start, current_period_end=start + timedelta(days=30),
        cancel_at_period_end=True))
    for billed in ("4.00", "2.50"):
        db_session.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id=OG_ID,
                                  location_id=LOCATION_ID, cost_usd=Decimal("1"),
                                  billed_usd=Decimal(billed), period_start=start))
    db_session.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                     channel="web", status="closed", started_at=now,
                                     cost_usd=Decimal("1.234")))
    await db_session.commit()

    r = await client.get(BASE, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["answering"] is True
    assert body["cancel_at_period_end"] is True
    assert body["usage"]["billed_usd"] == 6.5
    assert body["usage"]["included_usd"] == 5.0
    assert body["usage"]["overage_usd"] == 1.5
    [loc] = body["locations"]
    assert loc["location_id"] == LOCATION_ID
    assert loc["spend_today_usd"] == 1.23
    assert loc["daily_cap_usd"] == 20.0
    assert loc["cap_reached"] is False


async def test_status_without_a_subscription(client, manager_token, og):
    r = await client.get(BASE, headers=_auth(manager_token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] is None and body["answering"] is False
    assert body["usage"]["overage_usd"] == 0.0
