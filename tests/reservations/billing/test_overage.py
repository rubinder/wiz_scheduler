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
    item, even one deleted since), delete, list(pending). Invoices: create
    (with `subscription` + include it attaches that subscription's pending
    items). Override with items["hooks"][name]."""
    state: dict = {"live": {}, "created": [], "deleted": [], "keys": {}, "hooks": {}, "n": 0,
                   "invoices": [], "inv_keys": {}}

    def create(params, options=None):
        if "create" in state["hooks"]:
            return state["hooks"]["create"](params, options)
        key = (options or {}).get("idempotency_key")
        if key in state["keys"]:
            return state["keys"][key]
        state["n"] += 1
        item = {"id": f"ii_{state['n']}", "invoice": params.get("invoice"), **params}
        state["live"][item["id"]] = item
        state["created"].append({"params": params, "key": key})
        state["keys"][key] = dict(item)
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

    def item_list(params=None, options=None):
        assert params["customer"] == CUSTOMER and params["pending"] is True
        return _Page(i for i in state["live"].values() if not i.get("invoice"))

    def invoice_create(params, options=None):
        if "invoice_create" in state["hooks"]:
            return state["hooks"]["invoice_create"](params, options)
        key = (options or {}).get("idempotency_key")
        if key in state["inv_keys"]:
            return state["inv_keys"][key]
        inv = {"id": f"in_final_{len(state['invoices']) + 1}", **params}
        state["invoices"].append({"params": params, "key": key})
        state["inv_keys"][key] = inv
        if params.get("pending_invoice_items_behavior") == "include":
            for item in state["live"].values():
                if not item.get("invoice") and item.get("subscription") == params.get("subscription"):
                    item["invoice"] = inv["id"]
        return inv

    v1 = br._client().v1
    v1.invoice_items = SimpleNamespace(create=create, delete=delete, list=item_list)
    v1.invoices.create = invoice_create
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


async def test_an_anchor_reset_never_bills_usage_twice(og, db_session, fake, items):
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


async def test_cancellation_bills_the_last_period_on_a_final_invoice(
    client, og, db_session, monkeypatch, fake, items,
):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)          # $2.50 pending on ii_1
    await _usage(db_session, "1.00")          # the tail after the last run
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    final = r.json()["reservation_agent"]["final"]
    assert (final["final_invoice"], final["standalone"], final["amount_usd"]) == ("in_final_1", False, 3.5)
    [inv] = items["invoices"]
    assert inv["params"] == {
        "customer": CUSTOMER, "subscription": AGENT_SUB, "pending_invoice_items_behavior": "include",
        "auto_advance": True, "collection_method": "charge_automatically",
        "description": f"Final AI host usage — {PERIOD}",
        "metadata": {"product": "reservation_agent", "og_id": OG_ID, "subscription": AGENT_SUB}}
    assert inv["key"] == f"agent-final-{AGENT_SUB}"
    assert {i["invoice"] for i in items["live"].values()} == {"in_final_1"}
    [charge] = await _charges(db_session)
    assert (Decimal(charge.amount_usd), charge.status, charge.error_message) == (
        Decimal("3.50"), "pending", "final invoice in_final_1")
    assert (await _row(db_session)).status == "canceled"

    # Replays (Stripe redelivers for days): no new item, no new invoice.
    for _ in range(2):
        r = await _post(client, monkeypatch, "customer.subscription.deleted",
                        _subscription(status="canceled"))
        assert r.json()["reservation_agent"]["final"]["final_invoice"] is None
    assert len(items["invoices"]) == 1 and len(items["created"]) == 2
    # The daily run does not look at it again either.
    assert (await br.reconcile_all_agent_subscriptions(db_session))["synced"] == []


async def test_no_final_invoice_without_overage(client, og, db_session, monkeypatch, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "4.00")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    r = await _post(client, monkeypatch, "customer.subscription.deleted",
                    _subscription(status="canceled"))
    assert r.json()["reservation_agent"]["final"]["final_invoice"] is None
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


async def test_final_invoice_falls_back_to_a_standalone_invoice(
    og, db_session, fake, items, monkeypatch,
):
    """If Stripe refuses `subscription` for the ended subscription, the
    invoice is made without it and only this subscription's overage items
    are put on it (re-created with invoice=, the originals deleted)."""
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    # A pending item of the scheduling subscription must not be swept in.
    items["live"]["ii_s"] = {"id": "ii_s", "subscription": "sub_sched", "metadata": {}}
    real = items["hooks"].pop("invoice_create", None)
    assert real is None

    def refuse_subscription(params, options=None):
        if "subscription" in params:
            raise stripe.InvalidRequestError("subscription is canceled", "subscription")
        del items["hooks"]["invoice_create"]
        return br._client().v1.invoices.create(params, options)
    items["hooks"]["invoice_create"] = refuse_subscription
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert (out["final_invoice"], out["standalone"], out["amount_usd"]) == ("in_final_1", True, 2.5)
    [inv] = items["invoices"]
    assert inv["params"]["pending_invoice_items_behavior"] == "exclude"
    assert "subscription" not in inv["params"]
    assert inv["key"] == f"agent-final-{AGENT_SUB}-standalone"
    assert "ii_1" not in items["live"]
    moved = items["live"]["ii_2"]
    assert (moved["invoice"], moved["amount"], moved["metadata"]["subscription"]) == (
        "in_final_1", 250, AGENT_SUB)
    assert items["live"]["ii_s"].get("invoice") is None
    [charge] = await _charges(db_session)
    assert (charge.stripe_object_id, charge.error_message) == ("ii_2", "final invoice in_final_1")


async def test_a_failed_final_invoice_alerts_and_never_raises(og, db_session, fake, items, monkeypatch):
    sent = []
    monkeypatch.setattr("backend.services.operator_alerts.send_agent_final_invoice_alert",
                        lambda *a: sent.append(a) or True)
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    items["hooks"]["invoice_create"] = _down
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert "APIConnectionError" in out["error"]
    assert len(sent) == 1 and sent[0][:3] == (OG_ID, AGENT_SUB, PERIOD)
    # The last period's item was still recorded; a retry invoices it.
    [charge] = await _charges(db_session, "pending")
    assert charge.error_message is None
    del items["hooks"]["invoice_create"]
    assert (await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB))["final_invoice"] == "in_final_1"


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


async def test_the_final_invoice_drops_an_unrecorded_item(og, db_session, fake, items):
    await _add_row(db_session)
    await _usage(db_session, "7.50")
    await _reconcile(db_session, og)
    items["live"]["ii_orphan"] = {"id": "ii_orphan", "subscription": AGENT_SUB, "amount": 999,
                                  "metadata": {"kind": KIND, "period": "2026-10-10",
                                               "subscription": AGENT_SUB}}
    fake["subs"][AGENT_SUB]["status"] = "canceled"
    out = await br.finalize_agent_billing(db_session, OG_ID, AGENT_SUB)
    assert out["amount_usd"] == 2.5 and "ii_orphan" not in items["live"]
    assert items["live"]["ii_1"]["invoice"] == "in_final_1"
