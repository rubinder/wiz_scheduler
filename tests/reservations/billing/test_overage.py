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


class _Page(list):
    def auto_paging_iter(self):
        return iter(list(self))


@pytest.fixture
def items(fake):
    """Invoice items and invoices on the fake Stripe. Items: create (honours
    idempotency keys the way Stripe does: a replayed key returns the first
    item, even one deleted since), retrieve, delete (refused for an item on
    an invoice past its draft), list(pending) and list(invoice). Invoices:
    create (as a draft), retrieve, list (the customer's, any status),
    delete (drafts only), finalize. Both refuse `subscription=` naming a
    canceled subscription. An item's `invoice` naming an invoice the fake
    does not hold (a renewal invoice) counts as finalized. Override with
    items["hooks"][name]; the un-hooked calls are items["real"][name]."""
    state: dict = {"live": {}, "created": [], "deleted": [], "keys": {}, "hooks": {}, "n": 0,
                   "invoices": [], "inv_keys": {}}

    def refuse_canceled(params):
        sub = fake["subs"].get(params.get("subscription"))
        if sub is not None and sub["status"] == "canceled":
            raise stripe.InvalidRequestError("subscription is canceled", "subscription")

    def create(params, options=None):
        if "create" in state["hooks"]:
            return state["hooks"]["create"](params, options)
        refuse_canceled(params)
        if params.get("invoice"):
            assert state["by_id"][params["invoice"]]["status"] == "draft"
        key = (options or {}).get("idempotency_key")
        if key in state["keys"]:
            return state["keys"][key]
        state["n"] += 1
        item = {"id": f"ii_{state['n']}", "invoice": params.get("invoice"), **params}
        state["live"][item["id"]] = item
        state["created"].append({"params": params, "key": key})
        state["keys"][key] = dict(item)
        return item

    def real_delete(item_id):
        state["deleted"].append(item_id)
        if item_id not in state["live"]:
            raise stripe.InvalidRequestError(f"No such invoiceitem: {item_id}", "id",
                                             code="resource_missing")
        on = state["live"][item_id].get("invoice")
        if on and state["by_id"].get(on, {"status": "open"})["status"] != "draft":
            state["deleted"].pop()
            raise stripe.InvalidRequestError(
                f"{item_id} is on finalized invoice {on} and cannot be deleted", "id")
        del state["live"][item_id]
        return {"id": item_id, "deleted": True}

    def delete(item_id, params=None, options=None):
        if "delete" in state["hooks"]:
            return state["hooks"]["delete"](item_id)
        return real_delete(item_id)

    def item_retrieve(item_id, params=None, options=None):
        if item_id not in state["live"]:
            raise stripe.InvalidRequestError(f"No such invoiceitem: {item_id}", "id",
                                             code="resource_missing")
        return state["live"][item_id]

    def item_list(params=None, options=None):
        if params.get("invoice"):
            return _Page(i for i in state["live"].values() if i.get("invoice") == params["invoice"])
        assert params["customer"] == CUSTOMER and params["pending"] is True
        return _Page(i for i in state["live"].values() if not i.get("invoice"))

    def invoice_list(params=None, options=None):
        if "invoice_list" in state["hooks"]:
            return state["hooks"]["invoice_list"](params)
        assert params["customer"] == CUSTOMER
        return _Page(i for i in state["by_id"].values() if i.get("customer") == CUSTOMER)

    def invoice_create(params, options=None):
        if "invoice_create" in state["hooks"]:
            return state["hooks"]["invoice_create"](params, options)
        refuse_canceled(params)
        key = (options or {}).get("idempotency_key")
        if key in state["inv_keys"]:
            return state["inv_keys"][key]
        inv = {"id": f"in_final_{len(state['invoices']) + 1}", "status": "draft", **params}
        state["invoices"].append({"params": params, "key": key})
        state["inv_keys"][key] = dict(inv)
        state["by_id"][inv["id"]] = inv
        return inv

    def invoice_delete(inv_id, params=None, options=None):
        if inv_id not in state["by_id"]:
            raise stripe.InvalidRequestError("No such invoice", "id", code="resource_missing")
        assert state["by_id"][inv_id]["status"] == "draft", "only a draft can be deleted"
        state["invoices_deleted"].append(inv_id)
        del state["by_id"][inv_id]
        for item in [i for i in state["live"].values() if i.get("invoice") == inv_id]:
            del state["live"][item["id"]]
        return {"id": inv_id, "deleted": True}

    def real_finalize(inv_id, params=None):
        inv = state["by_id"][inv_id]
        assert inv["status"] == "draft"
        assert any(i.get("invoice") == inv_id for i in state["live"].values()), "empty invoice"
        inv["status"] = "open"
        state["finalized"].append((inv_id, params))
        return inv

    def invoice_finalize(inv_id, params=None, options=None):
        if "finalize" in state["hooks"]:
            return state["hooks"]["finalize"](inv_id, params)
        return real_finalize(inv_id, params)

    base_retrieve = br._client().v1.invoices.retrieve

    def invoice_retrieve(inv_id, params=None, options=None):
        if inv_id in state["by_id"]:
            return state["by_id"][inv_id]
        if inv_id.startswith("in_final"):
            raise stripe.InvalidRequestError("No such invoice", "id", code="resource_missing")
        return base_retrieve(inv_id, params, options)

    state.update(by_id={}, invoices_deleted=[], finalized=[],
                 real={"delete": real_delete, "finalize": real_finalize})
    v1 = br._client().v1
    v1.invoice_items = SimpleNamespace(create=create, delete=delete, list=item_list,
                                       retrieve=item_retrieve)
    v1.invoices.list = invoice_list
    v1.invoices.create = invoice_create
    v1.invoices.delete = invoice_delete
    v1.invoices.retrieve = invoice_retrieve
    v1.invoices.finalize_invoice = invoice_finalize
    return state


def _charge(period: str, amount: str, item: str, status: str, sub=AGENT_SUB) -> BillingCharge:
    return BillingCharge(ownership_group_id=OG_ID, kind=KIND, amount_usd=Decimal(amount),
                         stripe_object_id=item, stripe_subscription_id=sub, period=period,
                         status=status)


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
        "metadata": {"og_id": OG_ID, "period": PERIOD, "kind": KIND, "subscription": AGENT_SUB}}
    assert made["key"] == f"agent-overage-{OG_ID}-{PERIOD}-250"
    [charge] = await _charges(db_session)
    assert (charge.status, charge.period, Decimal(charge.amount_usd), charge.stripe_object_id,
            charge.stripe_subscription_id) == ("pending", PERIOD, Decimal("2.50"), "ii_1", AGENT_SUB)

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
    db_session.add(_charge(prev, "1.50", "ii_old", "succeeded"))
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
    db_session.add(_charge("2026-10-10", "1", "ii_other", "pending"))
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
    assert out["reconciled"] == []
    # The missed deletion still gets its final invoice.
    [final] = out["finalized"]
    assert final["final_invoice"] == "in_final_1" and final["amount_usd"] == 2.5
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


# ---------------------------------------------------------------------------
# Review fixes: anchor reset, final invoice, voided invoices, subscription
# scoping, replayed keys, orphans
# ---------------------------------------------------------------------------


def _set_period(fake, start: datetime, end: datetime, start_date: datetime | None = None) -> None:
    sub = fake["subs"][AGENT_SUB]
    sub["items"]["data"][0].update(current_period_start=_ts(start), current_period_end=_ts(end))
    if start_date is not None:
        sub["start_date"] = _ts(start_date)


async def test_an_anchor_reset_never_bills_usage_twice(og, db_session, fake, items, caplog):
    """Periods Jan 1 -> Feb 1 -> Mar 1, then the anchor is reset to Mar 10.
    Under the new anchor the Feb 1 and Mar 1 stamps are off-period. Feb 1's
    usage was billed (a charge exists for 2027-02-01): it stays put. Mar 1's
    was not: it moves (to the Feb 10 period, under the $5 included)."""
    utc = timezone.utc
    await _add_row(db_session)
    _set_period(fake, datetime(2027, 3, 10, tzinfo=utc), datetime(2027, 4, 10, tzinfo=utc),
                start_date=datetime(2027, 1, 1, tzinfo=utc))
    feb, mar = datetime(2027, 2, 1, tzinfo=utc), datetime(2027, 3, 1, tzinfo=utc)
    await _usage(db_session, "8.00", period_start=feb, created_at=datetime(2027, 2, 15, tzinfo=utc))
    db_session.add(_charge("2027-02-01", "3.00", "ii_feb", "succeeded"))
    await _usage(db_session, "2.00", period_start=mar, created_at=datetime(2027, 3, 5, tzinfo=utc))
    out = await _reconcile(db_session, og)
    assert out["swept"] == 1
    assert items["created"] == [] and "true_up" not in out
    db_session.expire_all()
    stamps = sorted((await db_session.execute(select(AgentUsage.period_start))).scalars().all())
    assert stamps == [feb, datetime(2027, 2, 10, tzinfo=utc)]
    assert sum(Decimal(c.amount_usd) for c in await _charges(db_session)) == Decimal("3.00")
    # The stale label is a warning while it is fresh, an info line later.
    import logging
    stale = [r for r in caplog.records if "stale label" in r.getMessage()]
    assert [r.levelno for r in stale] == [logging.WARNING]
    caplog.clear()
    _set_period(fake, datetime(2027, 4, 10, tzinfo=utc), datetime(2027, 5, 10, tzinfo=utc))
    await _reconcile(db_session, og)
    stale = [r for r in caplog.records if "stale label" in r.getMessage()]
    assert [r.levelno for r in stale] == [logging.INFO]


async def test_charges_of_another_subscription_never_count(og, db_session, fake, items):
    """Cancel and resubscribe the same day: both subscriptions' periods
    have the label 2026-11-10. The old one's paid charge does not settle
    the new one's overage."""
    later = P_START + timedelta(hours=2)
    db_session.add(_charge(PERIOD, "2.50", "ii_old", "succeeded", sub="sub_old"))
    await _usage(db_session, "7.50")  # the old subscription's period, billed above
    await _add_row(db_session)
    _set_period(fake, later, P_END + timedelta(hours=2))
    await _usage(db_session, "6.00", period_start=later)
    out = await _reconcile(db_session, og)
    assert (out["period"], out["action"], out["overage_usd"]) == (PERIOD, "created", 1.0)
    new = [c for c in await _charges(db_session) if c.stripe_subscription_id == AGENT_SUB]
    assert [(c.status, Decimal(c.amount_usd)) for c in new] == [("pending", Decimal("1.00"))]


async def test_a_replayed_key_of_a_deleted_orphan_is_retried(og, db_session, fake, items):
    """A run created ii_1 and died before its commit: no row records it.
    The next run deletes the orphan, and the same key replays ii_1; the
    item is created again under key -r1."""
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    key = f"agent-overage-{OG_ID}-{PERIOD}-250"
    br._client().v1.invoice_items.create(
        {"customer": CUSTOMER, "subscription": AGENT_SUB, "amount": 250, "currency": "usd",
         "metadata": {"kind": KIND, "period": PERIOD, "subscription": AGENT_SUB}},
        {"idempotency_key": key})
    # Pending items that are not this subscription's period: left alone.
    items["live"]["ii_x"] = {"id": "ii_x", "subscription": AGENT_SUB,
                             "metadata": {"kind": KIND, "period": "2026-10-10",
                                          "subscription": AGENT_SUB}}
    items["live"]["ii_s"] = {"id": "ii_s", "subscription": "sub_sched",
                             "metadata": {"kind": "invoice_item_storage"}}
    out = await _reconcile(db_session, og)
    assert out["action"] == "created"
    assert items["deleted"] == ["ii_1"]
    assert [c["key"] for c in items["created"]] == [key, f"{key}-r1"]
    assert sorted(items["live"]) == ["ii_2", "ii_s", "ii_x"]
    [charge] = await _charges(db_session)
    assert charge.stripe_object_id == "ii_2"


@pytest.mark.parametrize("event_type,outcome", [("invoice.voided", "voided"),
                                                ("invoice.marked_uncollectible", "marked uncollectible")])
async def test_a_voided_or_uncollectible_invoice_fails_its_charges_and_alerts(
    client, og, db_session, monkeypatch, fake, items, event_type, outcome,
):
    sent = []
    monkeypatch.setattr("backend.services.operator_alerts.send_agent_invoice_unpaid_alert",
                        lambda *a: sent.append(a) or True)
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    inv = _invoice()
    inv["lines"] = {"data": [{"invoice_item": "ii_1"}]}
    items["live"]["ii_1"]["invoice"] = "in_1"  # on the invoice, no longer pending
    r = await _post(client, monkeypatch, event_type, inv)
    body = r.json()["reservation_agent"]
    assert body["handled"] is True and body["overage_failed"] == 1
    [charge] = await _charges(db_session)
    assert (charge.status, charge.error_message) == ("failed", f"invoice in_1 {outcome}")
    assert sent == [(OG_ID, "in_1", outcome, 2.5, 1)]
    # Never re-billed automatically.
    out = await _reconcile(db_session, og)
    assert out["action"] == "skip_zero" and len(items["created"]) == 1
    from backend.models.ownership_group import OwnershipGroup
    db_session.expire_all()
    assert (await db_session.get(OwnershipGroup, OG_ID)).autoreload_failed_at is None


async def test_a_scheduling_invoice_voided_is_not_routed(client, og, db_session, monkeypatch, fake):
    await _add_row(db_session)
    r = await _post(client, monkeypatch, "invoice.voided", _invoice("sub_sched"))
    assert "reservation_agent" not in r.json()


async def test_a_standalone_final_invoice_is_routed_by_its_metadata(
    client, og, db_session, monkeypatch, fake, items,
):
    await _add_row(db_session)
    db_session.add(_charge(PERIOD, "2.50", "ii_9", "pending"))
    await db_session.commit()
    inv = {"id": "in_final_1", "object": "invoice", "customer": CUSTOMER,
           "metadata": {"product": "reservation_agent", "og_id": OG_ID, "subscription": AGENT_SUB},
           "lines": {"data": [{"parent": {"invoice_item_details": {"invoice_item": "ii_9"}}}]}}
    r = await _post(client, monkeypatch, "invoice.payment_succeeded", inv)
    assert r.json()["reservation_agent"]["overage_paid"] == 1


# ---------------------------------------------------------------------------
# Final invoice (Ruling V): standalone, every period, finalized explicitly
# ---------------------------------------------------------------------------


PREV = "2026-10-10"


def _final_params() -> dict:
    return {"customer": CUSTOMER, "pending_invoice_items_behavior": "exclude",
            "auto_advance": False, "collection_method": "charge_automatically",
            "description": f"Final AI host usage — {PERIOD}",
            "metadata": {"product": "reservation_agent", "og_id": OG_ID,
                         "subscription": AGENT_SUB}}


async def _cancel_and_post(client, monkeypatch, fake) -> dict:
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    return r.json()["reservation_agent"]


async def _prev_true_up_pending(db, items) -> None:
    """A previous-period true-up made after the renewal invoice: pending on
    the subscription, waiting for an invoice that will never come."""
    items["live"]["ii_prev"] = {"id": "ii_prev", "subscription": AGENT_SUB, "amount": 40,
                                "metadata": {"kind": KIND, "period": PREV,
                                             "subscription": AGENT_SUB}}
    db.add(_charge(PREV, "0.40", "ii_prev", "pending"))
    await db.commit()


async def test_cancellation_bills_every_period_on_one_final_invoice(
    client, og, db_session, monkeypatch, fake, items,
):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)          # $2.50 pending on ii_1 (subscription-attached)
    await _prev_true_up_pending(db_session, items)
    await _usage(db_session, "1.00")          # the tail after the last run
    final = (await _cancel_and_post(client, monkeypatch, fake))["final"]
    assert (final["final_invoice"], final["amount_usd"], final["action"]) == ("in_final_1", 3.9, "invoiced")
    [inv] = items["invoices"]
    assert inv["params"] == _final_params()
    assert inv["key"] == f"agent-final-{AGENT_SUB}-390"
    made = {c["params"]["metadata"]["period"]: c for c in items["created"][1:]}
    assert made[PREV]["params"] == {
        "customer": CUSTOMER, "amount": 40, "currency": "usd", "invoice": "in_final_1",
        "description": f"AI host usage over $5.00 — {PREV}",
        "metadata": {"og_id": OG_ID, "period": PREV, "kind": KIND, "subscription": AGENT_SUB}}
    assert made[PERIOD]["params"]["amount"] == 350
    assert made[PERIOD]["key"] == f"agent-final-item-in_final_1-{PERIOD}-350"
    # Finalized only once its items were on it.
    assert items["finalized"] == [("in_final_1", {"auto_advance": True})]
    # Then the subscription-attached items went.
    assert sorted(items["deleted"]) == ["ii_1", "ii_prev"]
    assert {i["invoice"] for i in items["live"].values()} == {"in_final_1"}
    rows = {(c.period, c.status): (Decimal(c.amount_usd), c.error_message)
            for c in await _charges(db_session)}
    assert rows[(PERIOD, "pending")] == (Decimal("3.50"), "final invoice in_final_1")
    assert rows[(PREV, "pending")] == (Decimal("0.40"), "final invoice in_final_1")
    assert {k[1] for k in rows} == {"pending", "superseded"}
    assert len(await _charges(db_session, "superseded")) == 2

    # Replays (Stripe redelivers for days): nothing more.
    for _ in range(2):
        final = (await _cancel_and_post(client, monkeypatch, fake))["final"]
        assert (final["action"], final["amount_usd"]) == ("noop", 0.0)
    assert len(items["invoices"]) == 1 and len(items["created"]) == 3
    assert (await br.reconcile_all_agent_subscriptions(db_session))["synced"] == []


async def test_more_usage_after_a_finalized_final_bills_only_the_difference(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    await _usage(db_session, "0.50")  # a conversation that was still running
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["amount_usd"]) == ("in_final_2", 0.5)
    assert items["invoices_deleted"] == []
    assert items["invoices"][-1]["key"] == f"agent-final-{AGENT_SUB}-50"
    assert sum(Decimal(c.amount_usd) for c in await _charges(db_session, "pending")) == Decimal("3.00")


async def _leftover_draft(db, items, cents: int) -> None:
    """A recorded final whose invoice is still a draft (finalize never ran)."""
    items["by_id"]["in_left"] = {"id": "in_left", "status": "draft"}
    items["live"]["ii_left"] = {"id": "ii_left", "invoice": "in_left", "amount": cents,
                                "metadata": {"kind": KIND, "period": PERIOD,
                                             "subscription": AGENT_SUB}}
    charge = _charge(PERIOD, str(Decimal(cents) / 100), "ii_left", "pending")
    charge.error_message = "final invoice in_left"
    db.add(charge)
    await db.commit()


async def test_a_grown_amount_adds_a_delta_item_to_a_leftover_draft(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "8.00")
    await _leftover_draft(db_session, items, 250)
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["amount_usd"]) == ("in_left", 0.5)
    assert items["invoices"] == []  # no new invoice
    [made] = items["created"]
    assert (made["params"]["invoice"], made["params"]["amount"]) == ("in_left", 50)
    assert items["finalized"] == [("in_left", {"auto_advance": True})]
    assert sum(Decimal(c.amount_usd) for c in await _charges(db_session, "pending")) == Decimal("3.00")


async def test_a_leftover_draft_with_nothing_to_bill_is_deleted(og, db_session, fake, items, monkeypatch):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _leftover_draft(db_session, items, 250)
    monkeypatch.setattr(settings, "RESERVATIONS_INCLUDED_USAGE_USD", 10.0)
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert out["action"] == "draft_deleted" and out["amount_usd"] == 0.0
    assert items["invoices_deleted"] == ["in_left"] and "ii_left" not in items["live"]
    [charge] = await _charges(db_session)
    assert charge.status == "superseded"


async def test_a_leftover_draft_with_the_right_amount_is_finalized(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _leftover_draft(db_session, items, 250)
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["final_invoice"], out["amount_usd"]) == ("finalized", "in_left", 0.0)
    assert items["finalized"] == [("in_left", {"auto_advance": True})] and items["created"] == []


async def test_a_failed_final_deletes_its_draft_records_nothing_and_alerts(
    og, db_session, fake, items, monkeypatch,
):
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)
    # The second item fails: the first is removed with the draft.
    calls = []

    def second_fails(params, options):
        calls.append(params)
        if len(calls) == 2:
            raise stripe.APIConnectionError("down")
        del items["hooks"]["create"]
        try:
            return br._client().v1.invoice_items.create(params, options)
        finally:
            items["hooks"]["create"] = second_fails
    items["hooks"]["create"] = second_fails
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "APIConnectionError" in out["error"]
    assert (out["amount_usd"], out["final_invoice"]) == (0.0, None)
    assert items["invoices_deleted"] == ["in_final_1"]
    assert sorted(items["live"]) == ["ii_1", "ii_prev"]       # untouched
    [body] = sent
    assert "Nothing was moved or billed" in body and "do not create new ones" in body
    assert "invoice the existing pending AI host usage items <code>ii_1</code>, <code>ii_prev</code>" in body
    assert "do NOT" not in body and "do nothing" not in body
    assert {c.stripe_object_id: c.status for c in await _charges(db_session)} == {
        "ii_1": "pending", "ii_prev": "pending"}
    # A retry starts clean: the key replays the deleted draft, so -r1.
    items["hooks"].clear()
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["amount_usd"]) == ("in_final_2", 2.9)
    assert [i["key"] for i in items["invoices"]] == [f"agent-final-{AGENT_SUB}-290",
                                                     f"agent-final-{AGENT_SUB}-290-r1"]


def _alerts(monkeypatch) -> list:
    """The bodies of the operator emails the final-invoice alert sends."""
    sent: list = []
    monkeypatch.setattr("backend.services.operator_alerts._send_operator_email",
                        lambda subject, body, label: sent.append(body) or True)
    return sent


DRAFT_ROW = "final invoice in_final_1; draft"


async def _two_pending_then_cancel(db, og, fake, items, tail: str | None = None) -> None:
    """$2.50 pending on ii_1 (current period) and $0.40 on ii_prev (the
    previous period's true-up), both subscription-attached; then canceled."""
    await _add_row(db)
    await _usage(db, "7.50")
    await _reconcile(db, og)
    await _prev_true_up_pending(db, items)
    if tail:
        await _usage(db, tail)
    fake["subs"][AGENT_SUB]["status"] = "canceled"


def _on_invoice(items, inv_id: str) -> dict:
    return {i["metadata"]["period"]: i["amount"] for i in items["live"].values()
            if i.get("invoice") == inv_id}


async def test_a_finalize_that_really_failed_leaves_the_draft_for_the_next_run(
    og, db_session, fake, items, monkeypatch,
):
    """The old items are deleted before the finalize (Ruling X), so the
    draft is where the amounts live: it stays, nothing is recorded, and the
    next run finalizes that same draft. Never a second invoice."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)
    items["hooks"]["finalize"] = _down
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "APIConnectionError" in out["error"]
    assert (out["amount_usd"], out["final_invoice"]) == (0.0, None)
    assert items["invoices_deleted"] == [] and items["finalized"] == []
    assert items["by_id"]["in_final_1"]["status"] == "draft"
    assert _on_invoice(items, "in_final_1") == {PERIOD: 250, PREV: 40}
    [body] = sent
    assert ("Draft <code>in_final_1</code> holds the amounts — do NOT invoice by hand; the "
            "next run finalizes it (or finalize it in Stripe)") in body
    assert "invoice the existing" not in body
    # The old rows are untouched; the draft's items are recorded as a draft.
    assert {c.stripe_object_id: (c.status, c.error_message) for c in await _charges(db_session)} == {
        "ii_1": ("pending", None), "ii_prev": ("pending", None),
        "ii_2": ("pending", DRAFT_ROW), "ii_3": ("pending", DRAFT_ROW)}

    # The next daily run finalizes that draft (the row is canceled by then:
    # the webhook that called finalize had synced it).
    (await _row(db_session)).status = "canceled"
    await db_session.commit()
    items["hooks"].clear()
    daily = await br.reconcile_all_agent_subscriptions(db_session)
    assert daily["errors"] == [] and daily["synced"] == []
    [out] = daily["finalized"]
    assert (out["action"], out["final_invoice"]) == ("finalized", "in_final_1")
    assert len(items["invoices"]) == 1 and len(items["created"]) == 3
    assert items["finalized"] == [("in_final_1", {"auto_advance": True})]
    rows = await _charges(db_session)
    assert sorted((c.period, c.status, Decimal(c.amount_usd)) for c in rows
                  if c.error_message == "final invoice in_final_1") == [
        (PREV, "pending", Decimal("0.40")), (PERIOD, "pending", Decimal("2.50"))]
    assert {c.status for c in rows if c.stripe_object_id in ("ii_1", "ii_prev")} == {"superseded"}
    # Done: the daily run leaves it alone from now on.
    fake["calls"].clear()
    assert (await br.reconcile_all_agent_subscriptions(db_session))["finalized"] == []
    assert fake["calls"] == [] and len(sent) == 1


async def test_a_kept_draft_with_no_earlier_charge_is_finalized_by_the_daily_run(
    og, db_session, fake, items, monkeypatch,
):
    """No reconcile ever ran, so the only trace of the kept draft is the
    row the failed run recorded for it."""
    _alerts(monkeypatch)
    await _add_row(db_session, status="canceled")
    await _usage(db_session, "7.50")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    items["hooks"]["finalize"] = _down
    assert "error" in await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    [row] = await _charges(db_session)
    assert (row.status, row.error_message) == ("pending", DRAFT_ROW)
    items["hooks"].clear()
    [out] = (await br.reconcile_all_agent_subscriptions(db_session))["finalized"]
    assert (out["action"], out["final_invoice"]) == ("finalized", "in_final_1")
    assert items["finalized"] == [("in_final_1", {"auto_advance": True})] and len(items["invoices"]) == 1
    [row] = await _charges(db_session)
    assert (row.status, row.error_message, Decimal(row.amount_usd)) == (
        "pending", "final invoice in_final_1", Decimal("2.50"))


async def test_the_daily_run_retries_a_final_that_never_moved_anything(
    og, db_session, fake, items, monkeypatch,
):
    _alerts(monkeypatch)
    await _add_row(db_session, status="canceled")
    await _usage(db_session, "7.50")
    await db_session.commit()
    db_session.add(_charge(PERIOD, "2.50", "ii_old", "pending"))
    await db_session.commit()
    items["live"]["ii_old"] = {"id": "ii_old", "subscription": AGENT_SUB, "amount": 250,
                               "metadata": {"kind": KIND, "period": PERIOD,
                                            "subscription": AGENT_SUB}}
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    [out] = (await br.reconcile_all_agent_subscriptions(db_session))["finalized"]
    assert (out["final_invoice"], out["amount_usd"]) == ("in_final_1", 2.5)
    assert "ii_old" not in items["live"]


async def test_a_cleanup_failure_with_nothing_owed_never_tells_the_operator_to_invoice(
    og, db_session, fake, items, monkeypatch,
):
    """Every amount is on the finalized final invoice already (adopted from
    Stripe); only the old item cannot be removed."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)
    real_commit = db_session.commit

    async def lost():
        monkeypatch.setattr(db_session, "commit", real_commit)
        raise RuntimeError("connection lost")
    monkeypatch.setattr(db_session, "commit", lost)
    await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    sent.clear()
    items["live"]["ii_1"] = {"id": "ii_1", "amount": 250, "subscription": AGENT_SUB,
                             "metadata": {"kind": KIND, "period": PERIOD,
                                          "subscription": AGENT_SUB}}  # its delete never landed
    items["hooks"]["delete"] = lambda item_id: (_ for _ in ()).throw(stripe.APIConnectionError("down"))
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" in out and len(items["invoices"]) == 1
    [body] = sent
    assert "Nothing is left to bill — do NOT invoice by hand" in body and "<code>ii_1</code>" in body
    assert "invoice the existing" not in body


# ---------------------------------------------------------------------------
# Never twice: Ruling X (an item already on an invoice) and crash replays
# ---------------------------------------------------------------------------


async def test_an_item_already_on_an_open_invoice_is_not_billed_again(
    og, db_session, fake, items, monkeypatch,
):
    """`pending` only means no payment webhook yet: ii_1 already sits on the
    open renewal invoice. It is left there; the final bills the rest."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items, tail="1.00")  # overage $3.50
    items["live"]["ii_1"]["invoice"] = "in_renewal"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" not in out and sent == []
    assert (out["final_invoice"], out["amount_usd"]) == ("in_final_1", 1.4)
    # $1.00 of the last period (3.50 - 2.50 on the renewal invoice), and the
    # previous period's item, which was on no invoice.
    assert _on_invoice(items, "in_final_1") == {PERIOD: 100, PREV: 40}
    assert items["invoices"][0]["key"] == f"agent-final-{AGENT_SUB}-140"
    assert items["live"]["ii_1"]["invoice"] == "in_renewal"      # not deleted, not moved
    assert items["deleted"] == ["ii_prev"]
    rows = {c.stripe_object_id: c for c in await _charges(db_session)}
    assert (rows["ii_1"].status, rows["ii_1"].error_message) == ("pending", None)
    assert rows["ii_prev"].status == "superseded"
    # The renewal invoice's webhook still settles it.
    assert await br._mark_overage_paid(db_session, OG_ID, {
        "id": "in_renewal", "lines": {"data": [{"invoice_item": "ii_1"}]}}) == 1
    await db_session.commit()
    # Replay: nothing more (the paid charge counts as settled).
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["amount_usd"]) == ("noop", 0.0) and len(items["invoices"]) == 1


async def test_every_item_already_invoiced_means_no_final_invoice(og, db_session, fake, items):
    await _two_pending_then_cancel(db_session, og, fake, items)
    items["live"]["ii_1"]["invoice"] = items["live"]["ii_prev"]["invoice"] = "in_renewal"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["final_invoice"], out["amount_usd"]) == ("noop", None, 0.0)
    assert items["invoices"] == [] and items["deleted"] == [] and len(items["created"]) == 1
    assert {c.status for c in await _charges(db_session)} == {"pending"}


async def test_an_item_invoiced_between_the_check_and_the_delete_comes_off_the_draft(
    og, db_session, fake, items, monkeypatch,
):
    """ii_1 is on no invoice when checked, so its replacement goes on the
    draft; then its delete is refused because the renewal invoice took it
    meanwhile. The replacement comes off the draft before it is finalized."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items, tail="1.00")

    def invoiced_meanwhile(item_id):
        if item_id == "ii_1":
            items["live"]["ii_1"]["invoice"] = "in_renewal"
        return items["real"]["delete"](item_id)
    items["hooks"]["delete"] = invoiced_meanwhile
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" not in out and sent == []
    assert out["final_invoice"] == "in_final_1" and len(items["invoices"]) == 1
    assert _on_invoice(items, "in_final_1") == {PERIOD: 100, PREV: 40}
    assert out["amount_usd"] == 1.4
    assert items["finalized"] == [("in_final_1", {"auto_advance": True})]
    assert items["live"]["ii_1"]["invoice"] == "in_renewal"
    rows = await _charges(db_session)
    assert {c.stripe_object_id: c.status for c in rows if c.stripe_object_id in ("ii_1", "ii_prev")} \
        == {"ii_1": "pending", "ii_prev": "superseded"}
    assert sum(Decimal(c.amount_usd) for c in rows
               if c.error_message == "final invoice in_final_1") == Decimal("1.40")


async def test_a_commit_lost_after_the_finalize_adopts_the_invoice_on_the_rerun(
    og, db_session, fake, items, monkeypatch,
):
    """The invoice is finalized in Stripe, then the process dies before the
    DB commit. The rerun finds the open invoice in Stripe and records it;
    it never makes a second one."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)
    real_commit = db_session.commit

    async def lost():
        monkeypatch.setattr(db_session, "commit", real_commit)
        raise RuntimeError("connection lost")
    monkeypatch.setattr(db_session, "commit", lost)
    try:
        out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    except RuntimeError:        # (the code before the fix let it out)
        await db_session.rollback()
        out = {"error": "RuntimeError"}
    assert "RuntimeError" in out["error"]
    assert items["by_id"]["in_final_1"]["status"] == "open"
    assert items["invoices_deleted"] == [] and _on_invoice(items, "in_final_1") == {PERIOD: 250, PREV: 40}
    [body] = sent
    assert ("Invoice <code>in_final_1</code> is already open/paid in Stripe — do nothing; "
            "the next run records it") in body
    assert "invoice the existing" not in body and "finalizes it" not in body
    # The old rows are as they were; the invoice's items are marked for the daily run.
    assert {c.stripe_object_id: (c.status, c.error_message) for c in await _charges(db_session)} == {
        "ii_1": ("pending", None), "ii_prev": ("pending", None),
        "ii_2": ("pending", DRAFT_ROW), "ii_3": ("pending", DRAFT_ROW)}

    for action in ("adopted", "noop"):
        out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
        assert "error" not in out and (out["action"], out["amount_usd"]) == (action, 0.0)
        assert len(items["invoices"]) == 1, "a second final invoice"
        assert len(items["finalized"]) == 1 and len(items["created"]) == 3
    assert len(sent) == 1
    rows = await _charges(db_session)
    assert sorted((c.period, Decimal(c.amount_usd)) for c in rows if c.status == "pending") == [
        (PREV, Decimal("0.40")), (PERIOD, Decimal("2.50"))]
    assert {c.error_message for c in rows if c.status == "pending"} == {"final invoice in_final_1"}
    assert {c.stripe_object_id for c in rows if c.status == "superseded"} == {"ii_1", "ii_prev"}
    # Its payment webhook finds the adopted rows.
    paid = await br._mark_overage_paid(db_session, OG_ID, {"id": "in_final_1", "lines": {"data": [
        {"invoice_item": i["id"]} for i in items["live"].values()]}})
    assert paid == 2


async def test_a_finalize_error_on_an_invoice_that_is_open_is_recorded_as_billed(
    og, db_session, fake, items, monkeypatch,
):
    """finalize_invoice raises (a timeout) but Stripe did finalize: the
    invoice's real status decides, nothing is undone or billed again."""
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)

    def finalized_then_timeout(inv_id, params):
        items["real"]["finalize"](inv_id, params)
        raise stripe.APIConnectionError("timed out")
    items["hooks"]["finalize"] = finalized_then_timeout
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" not in out and sent == []
    assert (out["final_invoice"], out["action"], out["amount_usd"]) == ("in_final_1", "invoiced", 2.9)
    assert items["invoices_deleted"] == [] and _on_invoice(items, "in_final_1") == {PERIOD: 250, PREV: 40}
    rows = await _charges(db_session)
    assert sum(Decimal(c.amount_usd) for c in rows if c.status == "pending"
               and c.error_message == "final invoice in_final_1") == Decimal("2.90")
    assert {c.stripe_object_id for c in rows if c.status == "superseded"} == {"ii_1", "ii_prev"}
    items["hooks"].clear()
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["amount_usd"]) == ("noop", 0.0) and len(items["invoices"]) == 1


async def test_a_finalize_error_with_an_unreadable_invoice_touches_nothing_and_is_adopted_later(
    og, db_session, fake, items, monkeypatch,
):
    sent = _alerts(monkeypatch)
    await _two_pending_then_cancel(db_session, og, fake, items)
    v1 = br._client().v1
    real_retrieve = v1.invoices.retrieve

    def finalized_then_stripe_down(inv_id, params):
        items["real"]["finalize"](inv_id, params)
        v1.invoices.retrieve = _down
        raise stripe.APIConnectionError("timed out")
    items["hooks"]["finalize"] = finalized_then_stripe_down
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" in out and len(sent) == 1
    assert items["invoices_deleted"] == [] and _on_invoice(items, "in_final_1") == {PERIOD: 250, PREV: 40}
    v1.invoices.retrieve = real_retrieve
    items["hooks"].clear()
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["adopted"], out["amount_usd"]) == ("adopted", 2, 0.0)
    assert len(items["invoices"]) == 1 and len(items["finalized"]) == 1


async def test_a_replayed_key_never_makes_a_second_invoice_next_to_an_unrecorded_one(
    og, db_session, fake, items,
):
    """Even if the listing does not show it: the key replays the open,
    unrecorded final invoice; it is adopted, not followed by an -r1 one."""
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    real_commit = db_session.commit

    async def lost():
        db_session.commit = real_commit
        raise RuntimeError("connection lost")
    db_session.commit = lost
    try:
        await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    except RuntimeError:
        await db_session.rollback()
    assert items["by_id"]["in_final_1"]["status"] == "open"
    assert [c.error_message for c in await _charges(db_session)] == [DRAFT_ROW]
    items["hooks"]["invoice_list"] = lambda params: _Page([])
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "error" not in out and (out["action"], out["amount_usd"]) == ("adopted", 0.0)
    assert [i["key"] for i in items["invoices"]] == [f"agent-final-{AGENT_SUB}-250"]
    [charge] = await _charges(db_session)
    assert (charge.status, Decimal(charge.amount_usd), charge.error_message) == (
        "pending", Decimal("2.50"), "final invoice in_final_1")


async def test_a_leftover_draft_holding_more_than_owed_is_reduced(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")           # $2.50 owed
    await _leftover_draft(db_session, items, 400)
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["amount_usd"]) == ("in_left", 2.5)
    assert _on_invoice(items, "in_left") == {PERIOD: 250} and "ii_left" not in items["live"]
    assert items["finalized"] == [("in_left", {"auto_advance": True})]
    rows = await _charges(db_session)
    assert [Decimal(c.amount_usd) for c in rows if c.status == "pending"] == [Decimal("2.50")]
    assert [c.stripe_object_id for c in rows if c.status == "superseded"] == ["ii_left"]


async def test_an_old_item_that_cannot_be_deleted_stops_the_final_invoice(
    og, db_session, fake, items, monkeypatch,
):
    """An old item that is on no invoice and cannot be deleted could still
    be billed: the draft holding its replacement is not finalized next to
    it. The next run finishes the same draft."""
    sent = _alerts(monkeypatch)
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    items["hooks"]["delete"] = lambda item_id: (_ for _ in ()).throw(stripe.APIConnectionError("down"))
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "APIConnectionError" in out["error"] and out["amount_usd"] == 0.0
    assert items["finalized"] == [] and len(sent) == 1
    assert "Draft <code>in_final_1</code> holds the amounts — do NOT invoice by hand" in sent[0]
    assert {c.stripe_object_id: (c.status, c.error_message) for c in await _charges(db_session)} == {
        "ii_1": ("pending", None), "ii_2": ("pending", DRAFT_ROW)}
    del items["hooks"]["delete"]
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["action"]) == ("in_final_1", "finalized")
    assert len(items["invoices"]) == 1 and items["deleted"] == ["ii_1"]
    assert _on_invoice(items, "in_final_1") == {PERIOD: 250}
    # Not billed again on a replay.
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["action"], out["amount_usd"]) == ("noop", 0.0)


async def test_the_final_invoice_never_names_the_canceled_subscription(og, db_session, fake, items):
    """The fake refuses subscription= for a canceled subscription, as
    Stripe may; the final path never sends it."""
    await _add_row(db_session)
    await _usage(db_session, "9.00")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert out["amount_usd"] == 4.0 and "error" not in out
    assert all("subscription" not in c["params"] for c in items["created"])
    assert all("subscription" not in i["params"] for i in items["invoices"])


async def test_no_final_invoice_without_overage(client, og, db_session, monkeypatch, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "4.00")
    final = (await _cancel_and_post(client, monkeypatch, fake))["final"]
    assert (final["final_invoice"], final["amount_usd"]) == (None, 0.0)
    assert items["invoices"] == [] and items["created"] == []


async def test_a_deleted_older_subscription_gets_no_final_invoice(
    client, og, db_session, monkeypatch, fake, items,
):
    await _add_row(db_session)
    await _usage(db_session, "9.00")
    fake["subs"]["sub_old"] = _subscription("sub_old", "canceled")
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription("sub_old", "canceled"))
    assert "final" not in r.json()["reservation_agent"]
    assert items["invoices"] == [] and items["created"] == []


async def test_superseded_charges_are_not_listed(client, manager_token, og, db_session):
    db_session.add(_charge(PERIOD, "2.50", "ii_1", "superseded"))
    db_session.add(_charge(PERIOD, "3.50", "ii_2", "pending"))
    await db_session.commit()
    r = await client.get("/api/v1/billing/charges",
                         headers={"Authorization": f"Bearer {manager_token}"})
    assert r.status_code == 200, r.text
    assert [c["stripe_object_id"] for c in r.json()["charges"]] == ["ii_2"]
