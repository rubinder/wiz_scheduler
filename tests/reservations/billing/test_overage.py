"""AI host overage reconcile (#259 step 5, Task 3): the period's billed usage
over the included $5.00 becomes one InvoiceItem on the agent subscription's
next invoice. Stripe is the in-memory fake of test_subscription_billing,
extended with invoice items."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe
from sqlalchemy import select

from backend.config import settings
from backend.models.billing_charge import BillingCharge
from backend.reservations.subscription import AgentUsage
from backend.services import billing_reservations as br
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.billing.test_subscription_billing import (  # noqa: F401 - fixtures
    AGENT_SUB,
    CUSTOMER,
    OG_ID,
    P_END,
    P_START,
    _add_row,
    _down,
    _invoice,
    _post,
    _row,
    _settings,
    _subscription,
    _ts,
    fake,
    og,
)

pytestmark = pytest.mark.asyncio

PERIOD = "2026-11-10"
PREV_START = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
KIND = "invoice_item_reservation_agent"


@pytest.fixture
def items(fake):
    """Invoice items on the fake Stripe: create (honours idempotency keys
    the way Stripe does: a replayed key returns the first item) and delete.
    Override with items["hooks"]["create"|"delete"]."""
    state: dict = {"live": {}, "created": [], "deleted": [], "keys": {}, "hooks": {}, "n": 0}

    def create(params, options=None):
        if "create" in state["hooks"]:
            return state["hooks"]["create"](params, options)
        key = (options or {}).get("idempotency_key")
        if key in state["keys"]:
            return state["keys"][key]
        state["n"] += 1
        item = {"id": f"ii_{state['n']}", **params}
        state["live"][item["id"]] = item
        state["created"].append({"params": params, "key": key})
        state["keys"][key] = item
        return item

    def delete(item_id, params=None, options=None):
        if "delete" in state["hooks"]:
            return state["hooks"]["delete"](item_id)
        state["deleted"].append(item_id)
        if item_id not in state["live"]:
            raise stripe.InvalidRequestError(f"No such invoiceitem: {item_id}", "id",
                                             code="resource_missing")
        del state["live"][item_id]
        return {"id": item_id, "deleted": True}

    br._client().v1.invoice_items = SimpleNamespace(create=create, delete=delete)
    return state


async def _usage(db, billed: str, *, period_start=P_START, created_at=None) -> None:
    db.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id=OG_ID, location_id=LOCATION_ID,
                      cost_usd=Decimal("1"), billed_usd=Decimal(billed), period_start=period_start,
                      created_at=created_at or period_start + timedelta(days=1)))
    await db.commit()


async def _charges(db, status: str | None = None) -> list[BillingCharge]:
    db.expire_all()
    q = select(BillingCharge).where(BillingCharge.kind == KIND)
    if status:
        q = q.where(BillingCharge.status == status)
    return list((await db.execute(q.order_by(BillingCharge.created_at))).scalars().all())


async def _reconcile(db, og) -> dict:
    return await br.reconcile_agent_overage(db, og)


async def test_zero_overage_creates_nothing(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "5.00")  # exactly the included amount
    out = await _reconcile(db_session, og)
    assert (out["action"], out["overage_usd"], out["period"]) == ("skip_zero", 0.0, PERIOD)
    assert items["created"] == [] and await _charges(db_session) == []


async def test_billed_over_the_included_amount_becomes_one_invoice_item(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "4.00")
    await _usage(db_session, "3.50")
    out = await _reconcile(db_session, og)
    assert (out["action"], out["usage_usd"], out["overage_usd"]) == ("created", 7.5, 2.5)
    [made] = items["created"]
    assert made["params"] == {
        "customer": CUSTOMER, "subscription": AGENT_SUB, "amount": 250, "currency": "usd",
        "description": "AI host usage over $5.00 — 2026-11-10",
        "metadata": {"og_id": OG_ID, "period": PERIOD, "kind": KIND}}
    assert made["key"] == f"agent-overage-{OG_ID}-{PERIOD}-250"
    [charge] = await _charges(db_session)
    assert (charge.status, charge.period, Decimal(charge.amount_usd), charge.stripe_object_id) == (
        "pending", PERIOD, Decimal("2.50"), "ii_1")

    # Rerun: the same amount is a no-op, no Stripe item call at all.
    items["hooks"]["create"] = items["hooks"]["delete"] = lambda *a: pytest.fail("no item call")
    assert (await _reconcile(db_session, og))["action"] == "noop"
    assert len(await _charges(db_session)) == 1


async def test_grown_usage_replaces_the_item(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    await _usage(db_session, "0.51")
    out = await _reconcile(db_session, og)
    assert (out["action"], out["overage_usd"]) == ("updated", 3.01)
    assert items["deleted"] == ["ii_1"]
    assert list(items["live"]) == ["ii_2"] and items["live"]["ii_2"]["amount"] == 301
    assert items["created"][-1]["key"] == f"agent-overage-{OG_ID}-{PERIOD}-301"
    [charge] = await _charges(db_session)
    assert (charge.stripe_object_id, Decimal(charge.amount_usd), charge.status) == (
        "ii_2", Decimal("3.01"), "pending")


async def test_an_item_already_gone_from_stripe_is_replaced(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "6.00")
    await _reconcile(db_session, og)
    del items["live"]["ii_1"]  # deleted in the dashboard
    await _usage(db_session, "1.00")
    assert (await _reconcile(db_session, og))["action"] == "updated"
    [charge] = await _charges(db_session)
    assert (charge.stripe_object_id, Decimal(charge.amount_usd)) == ("ii_2", Decimal("2.00"))


async def test_overage_back_to_zero_deletes_the_item_and_row(og, db_session, fake, items, monkeypatch):
    await _add_row(db_session)
    await _usage(db_session, "6.00")
    await _reconcile(db_session, og)
    monkeypatch.setattr(settings, "RESERVATIONS_INCLUDED_USAGE_USD", 10.0)
    assert (await _reconcile(db_session, og))["action"] == "deleted"
    assert items["live"] == {} and await _charges(db_session) == []


async def test_a_stripe_error_is_logged_returned_and_changes_nothing(og, db_session, fake, items, caplog):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    fake["hooks"]["retrieve"] = _down
    out = await _reconcile(db_session, og)
    assert "error" in out and "APIConnectionError" in out["error"]
    assert "not reconciled" in caplog.text
    assert await _charges(db_session) == [] and items["created"] == []

    del fake["hooks"]["retrieve"]
    await _reconcile(db_session, og)
    await _usage(db_session, "1.00")
    items["hooks"]["create"] = lambda *a: pytest.fail("no create after a failed delete")
    items["hooks"]["delete"] = lambda item_id: (_ for _ in ()).throw(
        stripe.InvalidRequestError("finalized", "id", code="invoice_not_editable"))
    out = await _reconcile(db_session, og)
    assert "error" in out
    [charge] = await _charges(db_session)
    assert (charge.stripe_object_id, Decimal(charge.amount_usd)) == ("ii_1", Decimal("2.50"))


async def test_a_failed_create_after_the_delete_leaves_no_stale_row(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    await _usage(db_session, "1.00")
    items["hooks"]["create"] = _down
    out = await _reconcile(db_session, og)
    assert "error" in out and items["live"] == {}
    assert await _charges(db_session) == []  # nothing claims the deleted item
    del items["hooks"]["create"]
    assert (await _reconcile(db_session, og))["action"] == "created"
    [charge] = await _charges(db_session)
    assert Decimal(charge.amount_usd) == Decimal("3.50")


@pytest.mark.parametrize("status", ["canceled", "unpaid", "incomplete", "paused"])
async def test_only_an_answering_subscription_is_billed(og, db_session, fake, items, status):
    await _add_row(db_session)
    await _usage(db_session, "9.00")
    fake["subs"][AGENT_SUB]["status"] = status
    out = await _reconcile(db_session, og)
    assert out["skipped"] == f"subscription_status={status}"
    assert items["created"] == []


async def test_past_due_is_still_billed(og, db_session, fake, items):
    await _add_row(db_session, "past_due")
    await _usage(db_session, "9.00")
    fake["subs"][AGENT_SUB]["status"] = "past_due"
    assert (await _reconcile(db_session, og))["action"] == "created"


async def test_no_subscription_is_skipped(og, db_session, fake, items):
    assert (await _reconcile(db_session, og))["skipped"] == "no_subscription"


# ---------------------------------------------------------------------------
# Period alignment
# ---------------------------------------------------------------------------


async def test_misassigned_usage_is_restamped_into_the_stripe_period(og, db_session, fake, items):
    await _add_row(db_session)
    fake["subs"][AGENT_SUB]["start_date"] = _ts(PREV_START)
    # Metered against a 30-day step from a stale period: Nov 9, not Nov 10.
    await _usage(db_session, "4.00")
    await _usage(db_session, "3.00", period_start=P_START - timedelta(days=1),
                 created_at=P_START + timedelta(days=2))
    # A row from before the subscription began: left as it was.
    old_stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await _usage(db_session, "9.00", period_start=old_stamp, created_at=old_stamp)
    out = await _reconcile(db_session, og)
    assert out["swept"] == 1
    assert (out["usage_usd"], out["overage_usd"]) == (7.0, 2.0)
    db_session.expire_all()
    stamps = sorted((await db_session.execute(select(AgentUsage.period_start))).scalars().all())
    assert stamps == [old_stamp, P_START, P_START]


async def test_a_misassigned_row_goes_to_the_period_of_its_creation(og, db_session, fake, items):
    """A wrong stamp on a row made in the previous period moves there, not
    into the current one."""
    await _add_row(db_session)
    fake["subs"][AGENT_SUB]["start_date"] = _ts(PREV_START)
    await _usage(db_session, "8.00", period_start=PREV_START + timedelta(days=3),
                 created_at=PREV_START + timedelta(days=5))
    out = await _reconcile(db_session, og)
    assert out["swept"] == 1 and out["usage_usd"] == 0.0
    db_session.expire_all()
    assert (await db_session.execute(select(AgentUsage.period_start))).scalar_one() == PREV_START


async def test_the_previous_period_tail_is_trued_up_once(og, db_session, fake, items):
    await _add_row(db_session)
    fake["subs"][AGENT_SUB]["start_date"] = _ts(PREV_START)
    prev = "2026-10-10"
    # The previous period billed $1.50 of overage before it ended (paid) ...
    db_session.add(BillingCharge(ownership_group_id=OG_ID, kind=KIND, amount_usd=Decimal("1.50"),
                                 stripe_object_id="ii_old", period=prev, status="succeeded"))
    # ... but ran up $7.00 by its end.
    await _usage(db_session, "7.00", period_start=PREV_START)
    out = await _reconcile(db_session, og)
    assert out["true_up"] == {"period": prev, "action": "true_up", "amount_usd": 0.5}
    [made] = items["created"]
    assert made["params"]["amount"] == 50 and made["params"]["metadata"]["period"] == prev
    assert made["params"]["description"] == f"AI host usage over $5.00 — {prev}"
    assert made["key"] == f"agent-overage-{OG_ID}-{prev}-trueup-200"
    assert "true_up" not in await _reconcile(db_session, og)
    assert len(items["created"]) == 1
    owed = sum(Decimal(c.amount_usd) for c in await _charges(db_session) if c.period == prev)
    assert owed == Decimal("2.00")


async def test_no_true_up_before_the_subscription_began(og, db_session, fake, items):
    await _add_row(db_session)  # no start_date: the subscription began with this period
    await _usage(db_session, "9.00", period_start=PREV_START)
    assert "true_up" not in await _reconcile(db_session, og)
    assert items["created"] == []


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


async def test_invoice_upcoming_reconciles_the_agent_overage(
    client, og, db_session, monkeypatch, fake, items,
):
    await _add_row(db_session)
    await _usage(db_session, "7.50")

    async def scheduling(*a, **k):
        pytest.fail("scheduling overage must not run for an agent invoice")
    monkeypatch.setattr("backend.routers.webhooks.bill_monthly_overages_for_og", scheduling)
    r = await _post(client, monkeypatch, "invoice.upcoming", _invoice())
    overage = r.json()["reservation_agent"]["overage"]
    assert (overage["action"], overage["overage_usd"]) == ("created", 2.5)
    [charge] = await _charges(db_session, "pending")
    assert charge.period == PERIOD
    db_session.expire_all()
    from backend.models.ownership_group import OwnershipGroup
    group = await db_session.get(OwnershipGroup, OG_ID)
    assert group.autoreload_failed_at is None and group.stripe_subscription_id == "sub_sched"


@pytest.mark.parametrize("new_api", [True, False])
async def test_invoice_payment_succeeded_marks_the_overage_paid(
    client, og, db_session, monkeypatch, fake, items, new_api,
):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    # Another period's pending item not on this invoice stays pending.
    db_session.add(BillingCharge(ownership_group_id=OG_ID, kind=KIND, amount_usd=Decimal("1"),
                                 stripe_object_id="ii_other", period="2026-10-10", status="pending"))
    await db_session.commit()
    inv = _invoice(new_api=new_api)
    line = ({"parent": {"type": "invoice_item_details",
                        "invoice_item_details": {"invoice_item": "ii_1"}}}
            if new_api else {"invoice_item": "ii_1"})
    inv["lines"] = {"data": [{"parent": {"type": "subscription_item_details"}}, line]}
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", inv)
    assert r.json()["reservation_agent"]["overage_paid"] == 1
    by_item = {c.stripe_object_id: c.status for c in await _charges(db_session)}
    assert by_item == {"ii_1": "succeeded", "ii_other": "pending"}
    assert (await _row(db_session)).status == "active"
    # A new period starts afresh: the paid row is not the pending one.
    await _usage(db_session, "0.10")
    assert (await _reconcile(db_session, og))["action"] == "created"


# ---------------------------------------------------------------------------
# The daily job
# ---------------------------------------------------------------------------


async def test_daily_run_resyncs_and_reconciles(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    out = await br.reconcile_all_agent_subscriptions(db_session)
    assert out["synced"] == [{"og": OG_ID, "status": "active"}]
    assert [r["action"] for r in out["reconciled"]] == ["created"]
    assert out["errors"] == []


async def test_daily_run_catches_a_missed_deletion(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.reconcile_all_agent_subscriptions(db_session)
    assert out["synced"] == [{"og": OG_ID, "status": "canceled"}]
    assert out["reconciled"] == [] and items["created"] == []
    assert (await _row(db_session)).status == "canceled"
    # Ended subscriptions are not read again.
    fake["calls"].clear()
    await br.reconcile_all_agent_subscriptions(db_session)
    assert fake["calls"] == []


async def test_daily_run_reports_a_group_stripe_cannot_read(og, db_session, fake, items):
    await _add_row(db_session)
    fake["hooks"]["retrieve"] = _down
    out = await br.reconcile_all_agent_subscriptions(db_session)
    assert out["synced"] == [] and len(out["errors"]) == 1
    assert out["errors"][0]["og"] == OG_ID
    assert (await _row(db_session)).status == "active"


async def test_daily_script_alerts_the_operator_on_errors(monkeypatch):
    from contextlib import asynccontextmanager

    import backend.database
    import backend.services.operator_alerts
    from backend.scripts import run_agent_overage_reconcile as script

    @asynccontextmanager
    async def session():
        yield object()

    async def run(db):
        return {"synced": [], "reconciled": [], "errors": [{"og": OG_ID, "error": "down"}]}
    sent = []
    monkeypatch.setattr(backend.database, "async_session_factory", session)
    monkeypatch.setattr(br, "reconcile_all_agent_subscriptions", run)
    monkeypatch.setattr(backend.services.operator_alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append((subject, body)) or True)
    await script.main()
    assert len(sent) == 1 and OG_ID in sent[0][1]
