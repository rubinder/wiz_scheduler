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
    # Stripe copies the subscription's metadata onto its invoices.
    md = {} if sub_id == SCHED_SUB else {"product": "reservation_agent", "og_id": OG_ID}
    if new_api:
        inv["parent"] = {"subscription_details": {"subscription": sub_id, "metadata": md}}
    else:
        inv["subscription"] = sub_id
        inv["subscription_details"] = {"metadata": md}
    return inv


async def _post(client, monkeypatch, event_type: str, obj: dict):
    event = {"type": event_type, "data": {"object": obj}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: event)
    return await client.post("/api/v1/webhooks/stripe", content=b"{}", headers={
        "stripe-signature": "t=1,v1=x", "content-type": "application/json"})


class _Page(list):
    def auto_paging_iter(self):
        return iter(list(self))


def _down(*a, **k):
    raise stripe.APIConnectionError("down")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    """A small in-memory Stripe behind billing_reservations' StripeClient:
    subscriptions by id (retrieve, list, cancel), Checkout sessions (create,
    retrieve, list, expire), customers, invoices and refunds. A test
    overrides one call with `fake["hooks"][name] = fn`."""
    from backend.services import billing_reservations as br

    state: dict = {"subs": {AGENT_SUB: _subscription()}, "calls": [], "sessions": [],
                   "cancelled": [], "expired": [], "invoices": {}, "refunds": [],
                   "listed_subs": None, "hooks": {}}
    hooks = state["hooks"]

    def hooked(name, default):
        return lambda *a, **k: hooks.get(name, default)(*a, **k)

    def retrieve(sub_id, params=None, options=None):
        state["calls"].append(sub_id)
        if sub_id not in state["subs"]:
            raise stripe.InvalidRequestError(f"No such subscription: {sub_id}", "id",
                                             code="resource_missing")
        return state["subs"][sub_id]

    def sub_list(params=None, options=None):
        assert params["customer"] == CUSTOMER and params["status"] == "all"
        subs = state["listed_subs"] if state["listed_subs"] is not None else []
        return _Page(subs)

    def cancel(sub_id, params=None, options=None):
        state["cancelled"].append(sub_id)
        if sub_id in state["subs"]:
            state["subs"][sub_id]["status"] = "canceled"
        return {"id": sub_id, "status": "canceled"}

    def refund(params=None, options=None):
        state["refunds"].append({"payment_intent": params["payment_intent"],
                                 "idempotency_key": (options or {}).get("idempotency_key")})
        return {"id": "re_1"}

    def no_call(name):
        def fail(*a, **k):
            pytest.fail(f"unexpected Stripe call: {name}")
        return fail

    client = SimpleNamespace(v1=SimpleNamespace(
        subscriptions=SimpleNamespace(retrieve=hooked("retrieve", retrieve),
                                      list=hooked("sub_list", sub_list),
                                      cancel=hooked("cancel", cancel)),
        checkout=SimpleNamespace(sessions=SimpleNamespace(
            create=hooked("session_create", no_call("checkout.sessions.create")),
            retrieve=hooked("session_retrieve", no_call("checkout.sessions.retrieve")),
            list=hooked("session_list", lambda params=None, options=None: _Page(state["sessions"])),
            expire=hooked("session_expire",
                          lambda sid, params=None, options=None: state["expired"].append(sid)))),
        customers=SimpleNamespace(
            create=hooked("customer_create", no_call("customers.create")),
            # A customer with no invoice default unless a test says otherwise.
            retrieve=hooked("customer_retrieve", lambda cid, params=None, options=None: {
                "id": cid, "invoice_settings": {"default_payment_method": None}})),
        invoices=SimpleNamespace(
            retrieve=hooked(
                "invoice_retrieve", lambda iid, params=None, options=None: state["invoices"][iid]),
            # No final invoices here either.
            list=lambda params=None, options=None: _Page([])),
        refunds=SimpleNamespace(create=hooked("refund", refund)),
        # No overage here (test_overage.py covers it): nothing pending.
        invoice_items=SimpleNamespace(
            list=lambda params=None, options=None: _Page([]),
            create=no_call("invoice_items.create"), delete=no_call("invoice_items.delete"),
            retrieve=no_call("invoice_items.retrieve")),
    ))
    monkeypatch.setattr(br, "_client", lambda: client)
    return state


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


async def test_checkout_session_parameters(client, manager_token, og, monkeypatch, fake):
    seen = {}

    def create(params, options=None):
        seen.update(params)
        return SimpleNamespace(id="cs_1", url="https://checkout.stripe.test/cs_1")
    fake["hooks"]["session_create"] = create

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
    # Never client_reference_id: the scheduling confirm-upgrade binds by it,
    # and an agent session must not look like a scheduling one (final review).
    assert "client_reference_id" not in seen
    assert "agent_checkout=success" in seen["success_url"]
    assert "session_id={CHECKOUT_SESSION_ID}" in seen["success_url"]
    assert "agent_checkout=cancel" in seen["cancel_url"]


async def test_checkout_creates_a_customer_for_a_group_without_one(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    og.stripe_customer_id = None
    og.stripe_subscription_id = None
    await db_session.commit()
    created = {}

    def make_customer(params, options=None):
        created.update(params)
        return SimpleNamespace(id="cus_new")
    seen = {}
    fake["hooks"]["customer_create"] = make_customer
    fake["hooks"]["session_create"] = lambda params, options=None: (
        seen.update(params) or SimpleNamespace(url="u"))

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
    client, manager_token, og, db_session, monkeypatch, status, fake,
):
    await _add_row(db_session, status)
    fake["hooks"]["session_create"] = lambda *a, **k: pytest.fail("no session for a live subscription")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 409
    assert r.json()["detail"] == "already_subscribed"


async def test_resubscribe_after_canceled_replaces_the_row(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "canceled", sub_id="sub_old")
    fake["hooks"]["session_create"] = lambda params, options=None: SimpleNamespace(url="u")
    assert (await client.post(f"{BASE}/checkout", headers=_auth(manager_token))).status_code == 200

    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session("sub_new")
    fake["subs"]["sub_new"] = _subscription("sub_new")
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
    client, manager_token, og, db_session, monkeypatch, fake,
):
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session()
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
    client, manager_token, og, db_session, monkeypatch, fake,
):
    assert (await _post(client, monkeypatch, "checkout.session.completed", _session())).status_code == 200
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session()
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
    client, manager_token, og, db_session, monkeypatch, session, fake,
):
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: session
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 400
    assert await _row(db_session) is None


# ---------------------------------------------------------------------------
# Webhook: each agent event updates only the agent row
# ---------------------------------------------------------------------------


async def test_subscription_created_inserts_the_row_from_metadata(
    client, og, db_session, monkeypatch, fake,
):
    fake["subs"][AGENT_SUB] = _subscription(status="incomplete")
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
    client, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "incomplete")
    # Older API version: period at the top level; no metadata, matched by id.
    sub = _subscription(items_period=False, cancel=True, metadata={})
    fake["subs"][AGENT_SUB] = sub
    r = await _post(client, monkeypatch, "customer.subscription.updated", sub)
    assert r.json()["reservation_agent"]["handled"] is True
    row = await _row(db_session)
    assert row.status == "active"
    assert row.cancel_at_period_end is True
    assert (row.current_period_start, row.current_period_end) == (P_START, P_END)
    await _assert_scheduling_untouched(db_session)


async def test_subscription_deleted_cancels_only_the_agent(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    assert r.json()["reservation_agent"]["handled"] is True
    assert (await _row(db_session)).status == "canceled"
    await _assert_scheduling_untouched(db_session)


async def test_deleting_an_older_agent_subscription_keeps_the_current_one(
    client, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "active", sub_id="sub_current")
    fake["subs"]["sub_older"] = _subscription("sub_older", status="canceled")
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription("sub_older", status="canceled"))
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == ("sub_current", "active")
    await _assert_scheduling_untouched(db_session)


async def test_agent_deletion_on_a_free_group_never_cancels_scheduling(
    client, og, db_session, monkeypatch, fake,
):
    """The scheduling branch cancels a group with no stripe_subscription_id
    on any deletion of its customer; the agent's deletion must not reach it."""
    og.stripe_subscription_id = None
    await db_session.commit()
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription(status="canceled"))
    db_session.expire_all()
    g = await db_session.get(OwnershipGroup, OG_ID)
    assert g.canceled_at is None
    assert (await _row(db_session)).status == "canceled"


@pytest.mark.parametrize("new_api", [True, False])
async def test_invoice_payment_failed_sets_past_due_only(
    client, og, db_session, monkeypatch, new_api, fake,
):
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "past_due"
    r = await _post(client, monkeypatch, "invoice.payment_failed", _invoice(new_api=new_api))
    assert r.json()["reservation_agent"]["handled"] is True
    assert (await _row(db_session)).status == "past_due"
    await _assert_scheduling_untouched(db_session)


async def test_invoice_payment_failed_leaves_an_incomplete_subscription_incomplete(
    client, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "incomplete")
    fake["subs"][AGENT_SUB]["status"] = "incomplete"
    await _post(client, monkeypatch, "invoice.payment_failed", _invoice())
    assert (await _row(db_session)).status == "incomplete"
    await _assert_scheduling_untouched(db_session)


async def test_invoice_payment_succeeded_syncs_the_period(
    client, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "past_due")
    nxt_start, nxt_end = P_END, P_END + timedelta(days=31)
    sub = _subscription()
    sub["items"]["data"][0].update(current_period_start=_ts(nxt_start),
                                   current_period_end=_ts(nxt_end))
    fake["subs"][AGENT_SUB] = sub
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", _invoice())
    assert r.json()["reservation_agent"]["handled"] is True
    assert fake["calls"] == [AGENT_SUB]
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
    assert body["overage"]["action"] == "skip_zero"  # no usage: nothing billed
    await _assert_scheduling_untouched(db_session)


async def test_checkout_completed_webhook_records_the_agent_only(
    client, og, db_session, monkeypatch,
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
    # Metered earlier in the period: an orphaned usage row (an erased
    # conversation) metered today would count toward today's spend.
    for billed in ("4.00", "2.50"):
        db_session.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id=OG_ID,
                                  location_id=LOCATION_ID, cost_usd=Decimal("1"),
                                  billed_usd=Decimal(billed), period_start=start,
                                  created_at=start))
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
    # The billing card's price line is rendered from these, not hardcoded.
    assert body["price_usd_monthly"] == 10.0
    assert body["usage_markup"] == 1.5
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


# ---------------------------------------------------------------------------
# Review fixes: stale events, duplicates, paused, deletion
# ---------------------------------------------------------------------------


async def test_replayed_updated_after_deleted_stays_canceled(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription(status="canceled"))
    # A late, stale update from before the cancellation.
    await _post(client, monkeypatch, "customer.subscription.updated", _subscription(status="active"))
    assert (await _row(db_session)).status == "canceled"
    await _assert_scheduling_untouched(db_session)


async def test_a_terminal_status_is_one_way_for_its_subscription(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "canceled")
    # Even if Stripe were to answer active for it, a canceled id stays canceled.
    await _post(client, monkeypatch, "customer.subscription.updated", _subscription(status="active"))
    assert (await _row(db_session)).status == "canceled"


async def test_replayed_payment_failed_after_recovery_stays_active(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "active")
    await _post(client, monkeypatch, "invoice.payment_failed", _invoice())
    assert (await _row(db_session)).status == "active"
    await _assert_scheduling_untouched(db_session)


async def test_a_terminal_status_for_another_subscription_never_replaces_the_row(
    client, og, db_session, monkeypatch, fake,
):
    await _add_row(db_session, "incomplete", sub_id="sub_new")
    fake["subs"]["sub_old"] = _subscription("sub_old", status="canceled")
    await _post(client, monkeypatch, "customer.subscription.deleted",
                _subscription("sub_old", status="canceled"))
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == ("sub_new", "incomplete")


async def test_paused_is_stored_and_does_not_answer(client, manager_token, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "paused"
    await _post(client, monkeypatch, "customer.subscription.updated", _subscription(status="paused"))
    assert (await _row(db_session)).status == "paused"
    body = (await client.get(BASE, headers=_auth(manager_token))).json()
    assert body["status"] == "paused" and body["answering"] is False


# Prevention (checkout, under the group lock) -------------------------------


async def test_checkout_adopts_a_live_agent_subscription_stripe_has(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    fake["listed_subs"] = [_subscription(SCHED_SUB, metadata={}), fake["subs"][AGENT_SUB]]
    fake["hooks"]["session_create"] = lambda *a, **k: pytest.fail("no session while one is live")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 409
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == (AGENT_SUB, "active")
    assert fake["cancelled"] == []
    await _assert_scheduling_untouched(db_session)


async def test_checkout_cancels_incomplete_and_expires_open_agent_sessions(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    incomplete = _subscription("sub_inc", status="incomplete")
    fake["subs"]["sub_inc"] = incomplete
    fake["listed_subs"] = [incomplete, _subscription("sub_gone", status="canceled")]
    fake["sessions"] = [
        {"id": "cs_agent", "metadata": {"product": "reservation_agent", "og_id": OG_ID}},
        {"id": "cs_upgrade", "metadata": {}},
    ]
    fake["hooks"]["session_create"] = lambda params, options=None: SimpleNamespace(url="u")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert fake["cancelled"] == ["sub_inc"]
    assert fake["expired"] == ["cs_agent"]


# Backstop (webhook / confirm) ----------------------------------------------


def _paid_duplicate(fake, sub_id="sub_dup", *, new_api=True) -> dict:
    sub = _subscription(sub_id)
    sub["latest_invoice"] = "in_dup"
    inv = {"id": "in_dup", "amount_paid": 1000}
    if new_api:
        inv["payments"] = {"data": [{"payment": {"payment_intent": "pi_dup"}}]}
    else:
        inv["payment_intent"] = "pi_dup"
    fake["subs"][sub_id] = sub
    fake["invoices"]["in_dup"] = inv
    return sub


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr("backend.services.operator_alerts.send_agent_duplicate_subscription_alert",
                        lambda *a, **kw: sent.append((a, kw)) or True)
    return sent


@pytest.mark.parametrize("event_type", ["customer.subscription.created",
                                        "customer.subscription.updated"])
@pytest.mark.parametrize("new_api", [True, False])
async def test_a_second_live_subscription_is_cancelled_refunded_and_alerted(
    client, og, db_session, monkeypatch, fake, alerts, event_type, new_api,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake, new_api=new_api)
    r = await _post(client, monkeypatch, event_type, dict(sub))
    dup = r.json()["reservation_agent"]["duplicate"]
    assert (dup["outcome"], dup["cancelled"], dup["refund"]) == ("handled", True, "refunded")
    assert fake["cancelled"] == ["sub_dup"]
    assert fake["refunds"] == [{"payment_intent": "pi_dup",
                                "idempotency_key": "agent-dup-refund-sub_dup"}]
    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == (AGENT_SUB, "active")
    [charge] = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalars().all()
    assert (charge.stripe_object_id, charge.status, float(charge.amount_usd)) == ("sub_dup", "succeeded", 10.0)
    assert len(alerts) == 1 and alerts[0][0][:3] == (OG_ID, "sub_dup", AGENT_SUB)
    assert alerts[0][1] == {"cancelled": True, "refund": "refunded", "errors": []}
    await _assert_scheduling_untouched(db_session)

    # Replays: Stripe now says canceled, so nothing happens again.
    for replay in ("customer.subscription.created", "customer.subscription.updated",
                   "customer.subscription.deleted", "invoice.payment_succeeded"):
        obj = _invoice("sub_dup") if replay.startswith("invoice.") else dict(sub)
        assert (await _post(client, monkeypatch, replay, obj)).status_code == 200
    assert len(fake["refunds"]) == 1 and len(alerts) == 1
    assert (await _row(db_session)).stripe_subscription_id == AGENT_SUB


async def test_a_failed_cancel_is_retried_on_later_events(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["hooks"]["cancel"] = _down
    await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    await _post(client, monkeypatch, "customer.subscription.updated", dict(sub))
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "failed" and "cancel failed" in charge.error_message
    # Same outcome twice: one alert, which says the cancel failed.
    assert len(alerts) == 1 and alerts[0][1]["cancelled"] is False
    # Every refund attempt carries the one idempotency key: Stripe refunds once.
    assert {r["idempotency_key"] for r in fake["refunds"]} == {"agent-dup-refund-sub_dup"}

    del fake["hooks"]["cancel"]
    await _post(client, monkeypatch, "invoice.payment_succeeded", _invoice("sub_dup"))
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "succeeded"
    assert charge.error_message == "cancelled=yes refund=refunded"
    assert fake["cancelled"] == ["sub_dup"]
    assert len(alerts) == 2 and alerts[1][1]["cancelled"] is True
    assert (await _row(db_session)).stripe_subscription_id == AGENT_SUB


async def test_a_failed_refund_is_retried_after_the_cancel(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["hooks"]["refund"] = _down
    await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    assert fake["cancelled"] == ["sub_dup"]
    assert alerts[0][1]["refund"] == "failed"
    # The cancelled duplicate's next event: Stripe says canceled, but the
    # failed record sends it back to the backstop for the refund.
    del fake["hooks"]["refund"]
    await _post(client, monkeypatch, "customer.subscription.deleted", dict(sub))
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "succeeded"
    assert len(fake["refunds"]) == 1 and fake["cancelled"] == ["sub_dup"]
    assert alerts[-1][1] == {"cancelled": True, "refund": "refunded", "errors": []}


async def test_an_unreadable_invoice_is_refund_unknown_not_nothing_to_refund(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["hooks"]["invoice_retrieve"] = _down
    r = await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    dup = r.json()["reservation_agent"]["duplicate"]
    assert (dup["refund"], dup["outcome"]) == ("unknown", "needs_attention")
    assert alerts[0][1]["refund"] == "unknown"
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "failed" and "not read" in charge.error_message


async def test_duplicate_alert_states_the_outcomes(monkeypatch):
    from backend.services import operator_alerts

    sent = []
    monkeypatch.setattr(operator_alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append(body) or True)
    operator_alerts.send_agent_duplicate_subscription_alert(
        OG_ID, "sub_dup", AGENT_SUB, None, 10.0, cancelled=False, refund="unknown",
        errors=["cancel failed: down"])
    operator_alerts.send_agent_duplicate_subscription_alert(
        OG_ID, "sub_dup", AGENT_SUB, "pi", 10.0, cancelled=True, refund="failed", errors=[])
    operator_alerts.send_agent_duplicate_subscription_alert(
        OG_ID, "sub_dup", AGENT_SUB, "pi", 10.0, cancelled=True, refund="refunded", errors=[])
    assert "cancel FAILED" in sent[0] and "still billing" in sent[0]
    assert "refund status unknown" in sent[0] and "check Stripe" in sent[0]
    assert "FAILED: refund it in Stripe" in sent[1] and "was cancelled" in sent[1]
    assert "was refunded ($10.00)" in sent[2]


async def test_a_duplicate_paid_through_checkout_is_backstopped_and_confirm_is_409(
    client, manager_token, og, db_session, monkeypatch, fake, alerts,
):
    await _add_row(db_session, "active")
    _paid_duplicate(fake)
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session("sub_dup")
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 409
    r = await _post(client, monkeypatch, "checkout.session.completed", _session("sub_dup"))
    assert r.status_code == 200
    assert fake["cancelled"] == ["sub_dup"] and len(fake["refunds"]) == 1
    assert (await _row(db_session)).stripe_subscription_id == AGENT_SUB


# Deletion -------------------------------------------------------------------


async def test_group_deletion_cancels_the_live_agent_subscription(og, db_session, fake):
    from backend.services.billing import delete_og_and_dependents

    await _add_row(db_session, "active")
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert fake["cancelled"] == [AGENT_SUB]
    assert await _row(db_session) is None


async def test_group_deletion_goes_ahead_when_stripe_cancel_fails(og, db_session, monkeypatch, fake):
    from backend.services.billing import delete_og_and_dependents

    await _add_row(db_session, "active")
    fake["hooks"]["cancel"] = _down
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    db_session.expire_all()
    assert await db_session.get(OwnershipGroup, OG_ID) is None


async def test_group_deletion_skips_a_canceled_agent_subscription(og, db_session, fake):
    from backend.services.billing import delete_og_and_dependents

    await _add_row(db_session, "canceled")
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert fake["cancelled"] == []


# Scheduling events with an agent row present --------------------------------


async def test_scheduling_invoice_payment_succeeded_still_flips_its_charges(
    client, og, db_session, monkeypatch, fake,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    db_session.add(BillingCharge(ownership_group_id=OG_ID, kind="invoice_item_storage",
                                 amount_usd=3, stripe_object_id="ii_1", period="2026-11",
                                 status="pending"))
    await db_session.commit()
    inv = _invoice(SCHED_SUB)
    inv["lines"] = {"data": [{"invoice_item": "ii_1",
                              "metadata": {"kind": "invoice_item_storage"}}]}
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", inv)
    assert "reservation_agent" not in r.json()
    assert r.json()["updated"] == 1
    assert fake["calls"] == []
    assert (await _row(db_session)).status == "active"


async def test_scheduling_subscription_updated_is_not_routed(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session, "active")
    r = await _post(client, monkeypatch, "customer.subscription.updated",
                    _subscription(SCHED_SUB, metadata={}))
    assert "reservation_agent" not in r.json()
    assert fake["calls"] == []
    await _assert_scheduling_untouched(db_session)


# Review 2: missing subscriptions, leftovers, locked reads, timeouts, charges --


async def test_a_subscription_stripe_does_not_have_is_acknowledged_unhandled(
    client, og, db_session, monkeypatch, fake,
):
    sub = _subscription("sub_ghost")
    r = await _post(client, monkeypatch, "customer.subscription.updated", sub)
    assert r.status_code == 200
    assert r.json()["reservation_agent"]["handled"] is False
    assert await _row(db_session) is None


async def test_checkout_cancels_leftover_unpaid_and_paused_agent_subscriptions(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    fake["listed_subs"] = [_subscription("sub_unpaid", status="unpaid"),
                           _subscription("sub_paused", status="paused"),
                           _subscription("sub_sched_unpaid", status="unpaid", metadata={})]
    fake["hooks"]["session_create"] = lambda params, options=None: SimpleNamespace(url="u")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert fake["cancelled"] == ["sub_unpaid", "sub_paused"]


async def test_locked_reads_see_another_writers_commit(og, db_session):
    """The A->B replacement race: a session that loaded the row before
    another writer committed must not act on its stale copy under the lock."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.reservations.subscription import for_group
    from backend.services import billing_reservations as br

    await _add_row(db_session, "active", sub_id="sub_a")
    stale = await for_group(db_session, OG_ID)
    stale_og = await db_session.get(OwnershipGroup, OG_ID)
    await db_session.commit()
    async with AsyncSession(db_session.bind) as other:
        row = await for_group(other, OG_ID)
        row.stripe_subscription_id, row.status = "sub_b", "trialing"
        (await other.get(OwnershipGroup, OG_ID)).stripe_customer_id = "cus_moved"
        await other.commit()
    assert stale.stripe_subscription_id == "sub_a"  # the identity map's copy
    og_now = await br._lock_group(db_session, OG_ID)
    fresh = await br._locked_row(db_session, OG_ID)
    assert og_now is stale_og and og_now.stripe_customer_id == "cus_moved"
    assert fresh is stale and (fresh.stripe_subscription_id, fresh.status) == ("sub_b", "trialing")
    await db_session.rollback()


async def test_the_stripe_client_has_a_short_timeout_and_one_retry(monkeypatch):
    """Calls made under the group lock go through a dedicated StripeClient;
    nothing global in the stripe module is changed."""
    from backend.services import billing_reservations as br

    before = (stripe.default_http_client, stripe.api_key, stripe.max_network_retries)
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_one")
    c1 = br.stripe_client()
    assert br.stripe_client() is c1
    assert c1._requestor._options.api_key == "sk_test_one"
    assert c1._requestor._options.max_network_retries == 1
    assert c1._requestor._client._timeout == br.STRIPE_TIMEOUT_S == 15
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_two")
    c2 = br.stripe_client()
    assert c2 is not c1 and c2._requestor._options.api_key == "sk_test_two"
    assert (stripe.default_http_client, stripe.api_key, stripe.max_network_retries) == before


async def test_duplicate_refund_records_are_not_listed_as_charges(
    client, manager_token, og, db_session,
):
    from backend.models.billing_charge import BillingCharge

    db_session.add_all([
        BillingCharge(ownership_group_id=OG_ID, kind="agent_duplicate_refund", amount_usd=10,
                      stripe_object_id="sub_dup", status="succeeded"),
        BillingCharge(ownership_group_id=OG_ID, kind="purchase", amount_usd=25,
                      stripe_object_id="pi_1", status="succeeded"),
    ])
    await db_session.commit()
    r = await client.get("/api/v1/billing/charges", headers=_auth(manager_token))
    assert [c["kind"] for c in r.json()["charges"]] == ["purchase"]


# Review 3: superseded duplicates, retries keep what they knew -------------


async def test_a_duplicate_that_became_the_only_subscription_is_left_alone(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["hooks"]["cancel"] = _down
    fake["hooks"]["refund"] = _down
    await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    assert alerts[-1][1]["cancelled"] is False and alerts[-1][1]["refund"] == "failed"

    # The kept subscription ends; the row moves onto the duplicate.
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    await _post(client, monkeypatch, "customer.subscription.deleted", _subscription(status="canceled"))
    del fake["hooks"]["cancel"], fake["hooks"]["refund"]
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", _invoice("sub_dup"))
    assert r.status_code == 200

    row = await _row(db_session)
    assert (row.stripe_subscription_id, row.status) == ("sub_dup", "active")
    assert fake["cancelled"] == [] and fake["refunds"] == []
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "succeeded" and "superseded" in charge.error_message
    assert alerts[-1][1].get("superseded") is True
    n = len(alerts)
    # Later events for it are ordinary events for the account's subscription.
    await _post(client, monkeypatch, "customer.subscription.updated", dict(sub))
    assert fake["cancelled"] == [] and fake["refunds"] == [] and len(alerts) == n
    assert (await _row(db_session)).status == "active"


async def test_superseded_alert_says_no_action(monkeypatch):
    from backend.services import operator_alerts

    sent = []
    monkeypatch.setattr(operator_alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append(body) or True)
    operator_alerts.send_agent_duplicate_subscription_alert(
        OG_ID, "sub_dup", "sub_dup", None, 10.0, cancelled=False, refund="failed",
        errors=[], superseded=True)
    assert "became the account's subscription" in sent[0] and "no action taken" in sent[0]


async def test_a_retry_keeps_the_amount_and_alerts_only_on_a_new_outcome(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["hooks"]["refund"] = _down
    await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    assert alerts[-1][1]["refund"] == "failed"
    # Retry: the invoice cannot be read now. The $10 found earlier is kept,
    # and the outcome changed (failed -> unknown), so one more alert.
    fake["hooks"]["invoice_retrieve"] = _down
    await _post(client, monkeypatch, "customer.subscription.deleted", dict(sub))
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert float(charge.amount_usd) == 10.0 and charge.status == "failed"
    assert len(alerts) == 2 and alerts[-1][1]["refund"] == "unknown"
    # Same outcome with a different error text: no new alert.
    fake["hooks"]["invoice_retrieve"] = lambda *a, **k: (_ for _ in ()).throw(
        stripe.APIConnectionError("still down, differently"))
    await _post(client, monkeypatch, "customer.subscription.deleted", dict(sub))
    assert len(alerts) == 2


async def test_confirm_answers_400_when_stripe_has_no_such_subscription(
    client, manager_token, og, db_session, fake,
):
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session("sub_ghost")
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 400
    assert await _row(db_session) is None


async def test_checkout_treats_a_vanished_listed_subscription_as_nothing_to_clear(
    client, manager_token, og, db_session, fake,
):
    fake["listed_subs"] = [_subscription("sub_vanished")]  # listed, then gone
    fake["hooks"]["session_create"] = lambda params, options=None: SimpleNamespace(url="u")
    r = await client.post(f"{BASE}/checkout", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert await _row(db_session) is None


# Task 3 carries: a retry re-reads an unpaid invoice; the row's own
# subscription is never backstopped ---------------------------------------


async def test_a_retry_rereads_an_invoice_that_was_unpaid(
    client, og, db_session, monkeypatch, fake, alerts,
):
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, "active")
    sub = _paid_duplicate(fake)
    fake["invoices"]["in_dup"]["amount_paid"] = 0  # not paid yet
    fake["hooks"]["cancel"] = _down
    await _post(client, monkeypatch, "customer.subscription.created", dict(sub))
    assert alerts[-1][1]["refund"] == "none" and fake["refunds"] == []

    # Stripe then collected it; the retry must see that and refund.
    fake["invoices"]["in_dup"]["amount_paid"] = 1000
    del fake["hooks"]["cancel"]
    await _post(client, monkeypatch, "customer.subscription.updated", dict(sub))
    assert fake["refunds"] == [{"payment_intent": "pi_dup",
                                "idempotency_key": "agent-dup-refund-sub_dup"}]
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert (charge.status, float(charge.amount_usd)) == ("succeeded", 10.0)
    assert charge.error_message == "cancelled=yes refund=refunded"


@pytest.mark.parametrize("status", ["unpaid", "paused", "incomplete"])
async def test_the_rows_own_subscription_is_never_backstopped(
    client, og, db_session, monkeypatch, fake, alerts, status,
):
    """A failed duplicate record for the subscription the row now holds,
    whatever its status: nothing cancelled or refunded, record closed."""
    from backend.models.billing_charge import BillingCharge

    await _add_row(db_session, status, sub_id="sub_dup")
    db_session.add(BillingCharge(ownership_group_id=OG_ID, kind="agent_duplicate_refund",
                                 stripe_object_id="sub_dup", amount_usd=10, status="failed",
                                 error_message="cancelled=no refund=failed; cancel failed: down"))
    await db_session.commit()
    sub = _paid_duplicate(fake)
    sub["status"] = status
    r = await _post(client, monkeypatch, "customer.subscription.updated", dict(sub))
    assert r.status_code == 200
    assert fake["cancelled"] == [] and fake["refunds"] == []
    db_session.expire_all()
    charge = (await db_session.execute(select(BillingCharge).where(
        BillingCharge.kind == "agent_duplicate_refund"))).scalar_one()
    assert charge.status == "succeeded" and "superseded" in charge.error_message
    assert (await _row(db_session)).stripe_subscription_id == "sub_dup"
    n = len(alerts)
    await _post(client, monkeypatch, "customer.subscription.updated", dict(sub))
    assert fake["cancelled"] == [] and len(alerts) == n


# ---------------------------------------------------------------------------
# Voice numbers follow the subscription (#259 step 6)
# ---------------------------------------------------------------------------


def _connect(monkeypatch):
    from tests.reservations.test_voice_numbers import FakeConnect
    from backend.services import connect_numbers

    connect = FakeConnect()
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONNECT_INSTANCE_ARN", "arn:aws:connect:r:1:instance/i")
    # No claim without a flow to route it (connect_numbers.can_claim).
    monkeypatch.setattr(settings, "CONNECT_CONTACT_FLOW_ID", "flow-1")
    monkeypatch.setattr(connect_numbers, "_make_client", lambda: connect)
    connect_numbers.reset_client()
    return connect


async def _number(db_session):
    from backend.reservations.voice_number_models import VoiceNumber

    db_session.expire_all()
    return (await db_session.execute(
        select(VoiceNumber).where(VoiceNumber.location_id == LOCATION_ID))
    ).scalar_one_or_none()


async def test_webhooks_request_and_hold_the_number_but_never_claim(
    client, og, db_session, monkeypatch, fake,
):
    from backend.reservations import voice_numbers

    connect = _connect(monkeypatch)
    await _add_row(db_session, "incomplete")
    r = await _post(client, monkeypatch, "customer.subscription.updated", _subscription())
    assert r.status_code == 200
    assert (await _number(db_session)).status == "requested"
    assert connect.calls == []  # the daily step claims, not the webhook

    await voice_numbers.run_daily(db_session)
    assert (await _number(db_session)).status == "active"

    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    assert r.status_code == 200
    held = await _number(db_session)
    assert held.status == "releasing" and held.release_after is not None


async def test_confirm_claims_and_a_claim_failure_still_returns_200(
    client, manager_token, og, db_session, monkeypatch, fake,
):
    connect = _connect(monkeypatch)
    connect.fail_claim = True
    fake["hooks"]["session_retrieve"] = lambda sid, *a, **k: _session()
    r = await client.post(f"{BASE}/confirm", json={"session_id": "cs_1"},
                          headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    number = await _number(db_session)
    assert number.status == "requested" and number.connect_phone_number_id is None
    assert "claim" in connect.names()


async def test_a_voice_number_failure_never_fails_the_webhook(
    client, og, db_session, monkeypatch, fake,
):
    from backend.reservations import voice_numbers

    async def broken(db, og_id, now=None, **kw):
        raise RuntimeError("db gone")
    monkeypatch.setattr(voice_numbers, "ensure_for_group", broken)
    await _add_row(db_session, "incomplete")
    r = await _post(client, monkeypatch, "customer.subscription.updated", _subscription())
    assert r.status_code == 200
    assert (await _row(db_session)).status == "active"


async def test_subscription_deleted_runs_final_billing_before_the_voice_step(
    client, og, db_session, monkeypatch, fake,
):
    from backend.reservations import voice_numbers
    from backend.services import billing_reservations as br

    order: list[str] = []

    async def finalize(db, og_id, sub_id):
        order.append("finalize")
        return {"og": og_id}

    async def broken(db, og_id, now=None, **kw):
        order.append("voice")
        raise RuntimeError("db gone")
    monkeypatch.setattr(br, "finalize_agent_billing", finalize)
    monkeypatch.setattr(voice_numbers, "ensure_for_group", broken)
    await _add_row(db_session, "active")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    assert r.status_code == 200
    assert r.json()["reservation_agent"]["final"] == {"og": OG_ID}
    assert order == ["finalize", "voice"]
