"""Daily Stripe reconciliation: the safety net behind the subscription webhooks.

From 2026-09-28 the webhook endpoint pointed at the marketing site and was
never subscribed to customer.subscription.deleted, so a subscription Stripe
cancelled for failed payments stayed paid in the app for a month.
"""
from datetime import datetime, timezone

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services import stripe_reconcile as rec
from tests.conftest import _id

pytestmark = [pytest.mark.integration]

OCT_4 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo; compare in UTC either way.
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


@pytest.fixture
def emails(monkeypatch):
    sent: list[str] = []

    async def fake_send(og):
        sent.append(og.id)

    monkeypatch.setattr("backend.services.stripe_reconcile.send_subscription_ended_email", fake_send)
    return sent


@pytest.fixture
def subs(monkeypatch):
    """Subscription id -> the object Stripe returns, or an exception to raise."""
    table: dict[str, object] = {}

    def retrieve(sub_id, *a, **kw):
        got = table[sub_id]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(stripe.Subscription, "retrieve", retrieve)
    return table


async def _paid_group(db: AsyncSession, sub_id: str, canceled_at: datetime | None = None) -> OwnershipGroup:
    og = OwnershipGroup(id=_id(), name=f"Group {sub_id}", stripe_customer_id=f"cus_{sub_id}",
                        stripe_subscription_id=sub_id, canceled_at=canceled_at)
    db.add(og)
    await db.commit()
    return og


@pytest.mark.asyncio
async def test_a_subscription_stripe_ended_is_cancelled_from_its_end_date(db_session, subs, emails):
    og = await _paid_group(db_session, "sub_ended")
    subs["sub_ended"] = {"status": "canceled", "ended_at": int(OCT_4.timestamp()), "canceled_at": None}

    summary = await rec.reconcile_subscriptions(db_session)

    await db_session.refresh(og)
    assert _utc(og.canceled_at) == OCT_4  # the grace period counts from when billing stopped
    assert _utc(og.notified_subscription_ended_at) == OCT_4
    assert emails == [og.id]
    assert [e["og"] for e in summary["ended"]] == [og.id]


@pytest.mark.asyncio
async def test_active_overdue_and_unreadable_subscriptions_are_left_alone(db_session, subs, emails):
    active = await _paid_group(db_session, "sub_active")
    overdue = await _paid_group(db_session, "sub_overdue")
    missing = await _paid_group(db_session, "sub_missing")
    subs["sub_active"] = {"status": "active"}
    subs["sub_overdue"] = {"status": "past_due"}
    subs["sub_missing"] = stripe.InvalidRequestError("No such subscription", "id")

    summary = await rec.reconcile_subscriptions(db_session)

    for og in (active, overdue, missing):
        await db_session.refresh(og)
        assert og.canceled_at is None
    assert emails == []
    assert summary["overdue"] == [{"og": overdue.id, "status": "past_due"}]
    assert [e["og"] for e in summary["errors"]] == [missing.id]  # reported, never cancelled on


@pytest.mark.asyncio
async def test_already_cancelled_groups_are_not_checked_or_emailed_again(db_session, subs, emails):
    og = await _paid_group(db_session, "sub_done", canceled_at=OCT_4)
    summary = await rec.reconcile_subscriptions(db_session)
    assert og.id not in summary["checked"] and emails == []


@pytest.mark.asyncio
async def test_ended_without_a_stripe_time_uses_now(db_session, subs, emails):
    og = await _paid_group(db_session, "sub_no_time")
    subs["sub_no_time"] = {"status": "incomplete_expired", "ended_at": None, "canceled_at": None}
    before = datetime.now(timezone.utc)
    await rec.reconcile_subscriptions(db_session)
    await db_session.refresh(og)
    assert _utc(og.canceled_at) >= before.replace(microsecond=0)


def _endpoints(monkeypatch, *endpoints):
    class Page:
        def auto_paging_iter(self):
            return iter(endpoints)

    monkeypatch.setattr(stripe.WebhookEndpoint, "list", lambda **kw: Page())


def test_webhook_endpoint_healthy(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com/")
    _endpoints(monkeypatch, {"id": "we_1", "url": "https://app.example.com/api/v1/webhooks/stripe",
                             "status": "enabled", "enabled_events": sorted(rec.REQUIRED_WEBHOOK_EVENTS)})
    assert rec.check_webhook_endpoint() == []


def test_webhook_endpoint_wildcard_counts_as_every_event(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com")
    _endpoints(monkeypatch, {"id": "we_1", "url": "https://app.example.com/api/v1/webhooks/stripe",
                             "status": "enabled", "enabled_events": ["*"]})
    assert rec.check_webhook_endpoint() == []


def test_webhook_endpoint_on_the_wrong_host_is_reported(monkeypatch):
    """The 2026-09-28 drift: the endpoint still pointed at the marketing site."""
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com")
    _endpoints(monkeypatch, {"id": "we_1", "url": "https://example.com/api/v1/webhooks/stripe",
                             "status": "enabled", "enabled_events": sorted(rec.REQUIRED_WEBHOOK_EVENTS)})
    [problem] = rec.check_webhook_endpoint()
    assert "no webhook endpoint for https://app.example.com/api/v1/webhooks/stripe" in problem
    assert "https://example.com/api/v1/webhooks/stripe" in problem


def test_webhook_endpoint_missing_events_or_disabled_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.example.com")
    _endpoints(monkeypatch, {"id": "we_1", "url": "https://app.example.com/api/v1/webhooks/stripe",
                             "status": "disabled", "enabled_events": ["invoice.upcoming"]})
    problems = rec.check_webhook_endpoint()
    assert any("we_1 is disabled" in p for p in problems)
    assert any("customer.subscription.deleted" in p for p in problems)


def test_alert_only_when_something_needs_a_person(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(rec, "_send_operator_email", lambda subject, body, label: sent.append((subject, body)) or True)
    empty = {"checked": ["a"], "ended": [], "overdue": [], "errors": []}
    assert rec.send_reconcile_alert(empty, []) is False and sent == []

    busy = {"checked": ["a", "b"], "ended": [{"og": "a", "status": "canceled", "canceled_at": "2026-10-04"}],
            "overdue": [{"og": "b", "status": "past_due"}], "errors": []}
    assert rec.send_reconcile_alert(busy, ["no webhook endpoint for x"]) is True
    [(subject, body)] = sent
    assert "og=a" in body and "past_due" in body and "no webhook endpoint for x" in body


@pytest.mark.asyncio
async def test_webhook_deletion_records_stripes_end_date(client: AsyncClient, db_session, emails, monkeypatch):
    og = await _paid_group(db_session, "sub_hook")
    event = {"type": "customer.subscription.deleted",
             "data": {"object": {"id": "sub_hook", "customer": og.stripe_customer_id,
                                 "ended_at": int(OCT_4.timestamp())}}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    r = await client.post("/api/v1/webhooks/stripe", content=b"{}",
                          headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"})
    assert r.status_code == 200
    await db_session.refresh(og)
    assert _utc(og.canceled_at) == OCT_4 and emails == [og.id]


@pytest.mark.asyncio
async def test_webhook_deletion_of_an_older_subscription_keeps_the_current_one(
        client: AsyncClient, db_session, emails, monkeypatch):
    og = await _paid_group(db_session, "sub_current")
    event = {"type": "customer.subscription.deleted",
             "data": {"object": {"id": "sub_previous", "customer": og.stripe_customer_id}}}
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda p, s, k: event)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    r = await client.post("/api/v1/webhooks/stripe", content=b"{}",
                          headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"})
    assert r.status_code == 200
    await db_session.refresh(og)
    assert og.canceled_at is None and emails == []
