"""Stripe side of the AI host subscription (#259 step 5).

Every Stripe call for the agent subscription lives here (routers stay thin):
the Checkout session that starts it, the return-URL confirmation, the
status/usage summary, and the webhook handling of agent objects.

Webhook routing. `is_agent_event` decides whether an event's object belongs
to the agent subscription: its subscription id matches a
ReservationSubscription row, or the object (or its subscription) carries
metadata product == "reservation_agent". The webhook asks it before any
scheduling branch, so an agent invoice never sets the scheduling group's
autoreload_failed_at, and an agent subscription's deletion never cancels the
scheduling plan. Every other event falls through to the scheduling code
unchanged.

Anti-corruption: Stripe objects arrive as webhook dicts or SDK objects, and
the period fields moved from the subscription onto its items in API
2025-03-31; `_field` and `_period` read both, so nothing past this module
sees a Stripe shape.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import stripe
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.location import Location
from backend.models.ownership_group import OwnershipGroup
from backend.reservations.rules import get_rules
from backend.reservations.subscription import (
    ANSWERING_STATUSES,
    BILLABLE_STATUSES,
    PRODUCT,
    STATUSES,
    AgentUsage,
    ReservationSubscription,
    for_group,
    period_start_for,
    period_usage,
    spend_today,
    usage_period_start,
)
from backend.services.archive import active

logger = logging.getLogger("wizscheduler.billing_reservations")

# Where Checkout sends the admin back; the billing page reads
# agent_checkout and session_id and calls POST .../reservation-agent/confirm.
BILLING_PAGE_PATH = "/manager/billing"
_CENT = Decimal("0.01")

AGENT_EVENT_TYPES = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
    "invoice.upcoming",
    "invoice.voided",
    "invoice.marked_uncollectible",
})


TERMINAL_STATUSES = ("canceled", "incomplete_expired")
# Agent subscriptions a new Checkout cancels first: not answering, but
# still able to bill if they recover.
LEFTOVER_STATUSES = ("incomplete", "unpaid", "paused")
DUPLICATE_REFUND_KIND = "agent_duplicate_refund"


class AgentBillingError(Exception):
    """Base of the domain errors the billing router maps to HTTP."""


class NotConfigured(AgentBillingError):
    """RESERVATIONS_PRICE_ID or the Stripe key is unset (503)."""


class AlreadySubscribed(AgentBillingError):
    """The group already has a live agent subscription (409)."""


class SessionInvalid(AgentBillingError):
    """A Checkout session that is not this group's agent subscription (400)."""


class StripeFailure(AgentBillingError):
    """Stripe refused or could not be reached (502)."""


# ---------------------------------------------------------------------------
# Reading Stripe objects
# ---------------------------------------------------------------------------


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        return getattr(obj, name, None)
    except Exception:  # an SDK object can raise on a missing key
        return None


def _as_id(value: Any) -> str | None:
    """A reference that may be an id or an expanded object."""
    if value is None or isinstance(value, str):
        return value
    return _field(value, "id")


def _when(ts: Any) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc)


def _period(sub: Any) -> tuple[datetime | None, datetime | None]:
    """(current_period_start, current_period_end): top level on older API
    versions, on the subscription items since 2025-03-31."""
    start, end = _field(sub, "current_period_start"), _field(sub, "current_period_end")
    if start is None or end is None:
        items = _field(_field(sub, "items"), "data") or []
        if items:
            start = start if start is not None else _field(items[0], "current_period_start")
            end = end if end is not None else _field(items[0], "current_period_end")
    return _when(start), _when(end)


def _metadata(obj: Any) -> Any:
    return _field(obj, "metadata") or {}


def _is_agent_metadata(md: Any) -> bool:
    return _field(md, "product") == PRODUCT


def _invoice_subscription_id(invoice: Any) -> str | None:
    """The invoice's subscription: top level before API 2025-03-31, under
    parent.subscription_details since."""
    sub = _as_id(_field(invoice, "subscription"))
    if sub:
        return sub
    details = _field(_field(invoice, "parent"), "subscription_details")
    return _as_id(_field(details, "subscription"))


def _invoice_subscription_metadata(invoice: Any) -> Any:
    md = _field(_field(invoice, "subscription_details"), "metadata")
    if md:
        return md
    return _field(_field(_field(invoice, "parent"), "subscription_details"), "metadata") or {}


def _subscription_id_of(event_type: str, obj: Any) -> str | None:
    if event_type.startswith("customer.subscription."):
        return _as_id(_field(obj, "id"))
    if event_type.startswith("invoice."):
        # A standalone final invoice (finalize_agent_billing) names its
        # subscription in its own metadata.
        md = _metadata(obj)
        return _invoice_subscription_id(obj) or (
            _field(md, "subscription") if _is_agent_metadata(md) else None)
    if event_type == "checkout.session.completed":
        return _as_id(_field(obj, "subscription"))
    return None


async def _row_by_subscription(db: AsyncSession, sub_id: str | None) -> ReservationSubscription | None:
    if not sub_id:
        return None
    return (await db.execute(
        select(ReservationSubscription)
        .where(ReservationSubscription.stripe_subscription_id == sub_id)
    )).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Webhook routing
# ---------------------------------------------------------------------------


async def is_agent_event(db: AsyncSession, event_type: str, obj: Any) -> bool:
    """True when the event's object belongs to the agent subscription."""
    if event_type not in AGENT_EVENT_TYPES:
        return False
    if event_type == "checkout.session.completed" and _field(obj, "mode") != "subscription":
        return False
    if _is_agent_metadata(_object_metadata(event_type, obj)):
        return True
    return await _row_by_subscription(db, _subscription_id_of(event_type, obj)) is not None


def _object_metadata(event_type: str, obj: Any) -> Any:
    """Our metadata on the object: the session's or subscription's own, an
    invoice's subscription's."""
    if event_type.startswith("invoice."):
        if _is_agent_metadata(_metadata(obj)):
            return _metadata(obj)
        return _invoice_subscription_metadata(obj)
    return _metadata(obj)


async def _lock_group(db: AsyncSession, og_id: str) -> OwnershipGroup | None:
    """The group, row-locked: serializes checkout, confirm and the webhook
    for one group's agent subscription."""
    return (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def _locked_row(db: AsyncSession, og_id: str) -> ReservationSubscription | None:
    """The group's row as committed now (call under the group lock): the
    session's identity map may hold a copy from before another writer's
    commit, which populate_existing overwrites."""
    return (await db.execute(
        select(ReservationSubscription)
        .where(ReservationSubscription.ownership_group_id == og_id)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


# Every Stripe call in this module goes through one StripeClient with a
# short timeout and one network retry, so a slow Stripe cannot hold the
# group row lock for the SDK's default 80 s x 3. Built lazily (the key is
# read at use time) and rebuilt if the key changes; nothing global in the
# stripe module is touched.
STRIPE_TIMEOUT_S = 15
_client_cache: dict[str, Any] = {}


def _client() -> Any:
    return stripe_client()


def stripe_client() -> Any:
    """The module's StripeClient for the current STRIPE_SECRET_KEY."""
    key = settings.STRIPE_SECRET_KEY
    client = _client_cache.get(key)
    if client is None:
        _client_cache.clear()
        client = _client_cache[key] = stripe.StripeClient(
            key, http_client=stripe.RequestsClient(timeout=STRIPE_TIMEOUT_S),
            max_network_retries=1)
    return client


def _status(raw: Any, sub_id: str) -> str | None:
    if raw is None:
        return None
    if raw not in STATUSES:
        logger.warning("agent subscription %s: unknown status %r left unchanged", sub_id, raw)
        return None
    return raw


async def _apply(db: AsyncSession, og_id: str, sub: Any) -> tuple[ReservationSubscription | None, bool]:
    """Upsert the group's row from `sub`, the subscription as Stripe has it
    now (not committed). Returns (row, duplicate).

    - The row's own subscription: status, period and cancel flag follow
      Stripe, except that a terminal status (canceled, incomplete_expired)
      is one-way for that subscription id.
    - Another subscription: replaces the row only when the row's is not
      live (active, trialing, past_due) and the incoming one is not
      terminal. An incoming live one while the row's is live is a
      duplicate: (None, True), for the backstop. Anything else is ignored.
    """
    sub_id = _as_id(_field(sub, "id"))
    status = _status(_field(sub, "status"), sub_id)
    row = await _locked_row(db, og_id)
    if row is None:
        row = ReservationSubscription(
            ownership_group_id=og_id, stripe_subscription_id=sub_id,
            status=status or "incomplete", cancel_at_period_end=False)
        db.add(row)
    elif row.stripe_subscription_id != sub_id:
        live = row.stripe_subscription_id and row.status in ANSWERING_STATUSES
        if live and status in ANSWERING_STATUSES:
            return None, True
        if live or status is None or status in TERMINAL_STATUSES:
            logger.info("agent subscription %s (%s) for og %s ignored; the row holds %s (%s)",
                        sub_id, status, og_id, row.stripe_subscription_id, row.status)
            return None, False
        row.stripe_subscription_id = sub_id
        row.status = status
        row.current_period_start = row.current_period_end = None
    elif row.status in TERMINAL_STATUSES:
        if status != row.status:
            logger.info("agent subscription %s is %s; a stale %s is ignored",
                        sub_id, row.status, status)
        return row, False
    elif status is not None:
        row.status = status
    start, end = _period(sub)
    if start is not None:
        row.current_period_start = start
    if end is not None:
        row.current_period_end = end
    cape = _field(sub, "cancel_at_period_end")
    if cape is not None:
        row.cancel_at_period_end = bool(cape)
    await db.flush()
    return row, False


async def _group_for(
    db: AsyncSession, sub_id: str | None, metadata: Any, customer: str | None,
) -> str | None:
    """The ownership group an agent object belongs to: its row's group, else
    the og_id we put in the metadata, accepted only when the group's Stripe
    customer is the object's customer."""
    row = await _row_by_subscription(db, sub_id)
    if row is not None:
        return row.ownership_group_id
    og_id = _field(metadata, "og_id")
    if not og_id:
        return None
    og = await db.get(OwnershipGroup, og_id)
    if og is None or not og.stripe_customer_id or og.stripe_customer_id != customer:
        logger.warning("agent object for og %s ignored: customer %s does not match", og_id, customer)
        return None
    return og_id


class SubscriptionMissing(AgentBillingError):
    """Stripe has no such subscription (resource_missing)."""


def _retrieve_subscription(sub_id: str) -> Any:
    try:
        return _client().v1.subscriptions.retrieve(sub_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) == "resource_missing":
            raise SubscriptionMissing(sub_id) from e
        raise


async def _sync_from_stripe(db: AsyncSession, og_id: str, sub_id: str) -> tuple[ReservationSubscription | None, dict | None]:
    """Re-read the subscription from Stripe (never trust an event payload:
    events arrive late and out of order) and upsert the row. A duplicate
    live subscription goes to the backstop. Caller holds the group lock and
    commits. Raises stripe.StripeError when Stripe cannot be read, so the
    webhook answers 5xx and Stripe redelivers, and SubscriptionMissing when
    Stripe has no such subscription.

    A duplicate whose earlier handling did not finish (its record is
    `failed`) goes back to the backstop on every later event, even once
    Stripe says it is canceled, until the refund is done too."""
    sub = _retrieve_subscription(sub_id)
    row, duplicate = await _apply(db, og_id, sub)
    if not duplicate:
        record = await _duplicate_record(db, og_id, sub_id)
        if record is None or record.status == "succeeded":
            return row, None
        current = await _locked_row(db, og_id)
        if current is not None and current.stripe_subscription_id == sub_id:
            # The account's own subscription now: never cancel or refund
            # it, whatever its status; only close the old record.
            return row, await _supersede_duplicate(db, og_id, sub_id, record)
    return row, await _resolve_duplicate(db, og_id, sub)


def _invoice_payment_intent(invoice: Any) -> str | None:
    """The invoice's PaymentIntent: top level before API 2025-03-31, under
    payments.data[].payment.payment_intent since."""
    pi = _as_id(_field(invoice, "payment_intent"))
    if pi:
        return pi
    for p in _field(_field(invoice, "payments"), "data") or []:
        pi = _as_id(_field(_field(p, "payment"), "payment_intent"))
        if pi:
            return pi
    return None


async def _duplicate_record(db: AsyncSession, og_id: str, sub_id: str | None) -> Any:
    from backend.models.billing_charge import BillingCharge

    if not sub_id:
        return None
    return (await db.execute(
        select(BillingCharge).where(
            BillingCharge.ownership_group_id == og_id,
            BillingCharge.kind == DUPLICATE_REFUND_KIND,
            BillingCharge.stripe_object_id == sub_id,
        ).limit(1).execution_options(populate_existing=True)
    )).scalar_one_or_none()


def _refund(payment_intent_id: str | None, sub_id: str) -> bool:
    """Fully refund a duplicate's payment through this module's client, once
    (idempotency key agent-dup-refund-<sub>). Same contract as
    billing.refund_payment_intent: True when refunded now or already, False
    when Stripe refused; never raises."""
    if not payment_intent_id:
        return False
    try:
        _client().v1.refunds.create(
            {"payment_intent": payment_intent_id},
            {"idempotency_key": f"agent-dup-refund-{sub_id}"})
        return True
    except stripe.StripeError as e:
        if getattr(e, "code", None) == "charge_already_refunded":
            return True
        logger.warning("agent duplicate %s: refund of %s failed: %s", sub_id, payment_intent_id, e)
        return False


# A duplicate record's error_message starts with its outcome, which a retry
# compares and keeps: "cancelled=yes|no refund=<state>", then any errors.
def _record_outcome(record: Any) -> tuple[bool, str] | None:
    head = (record.error_message or "").split(";", 1)[0].split()
    try:
        fields = dict(part.split("=", 1) for part in head)
        return fields["cancelled"] == "yes", fields["refund"]
    except (KeyError, ValueError):
        return None


async def _resolve_duplicate(db: AsyncSession, og_id: str, sub: Any) -> dict:
    """Backstop for a second live agent subscription on one group: cancel it
    in Stripe, refund its latest paid invoice, keep one
    agent_duplicate_refund BillingCharge per subscription with the outcome,
    and alert the operator with what actually happened.

    A `succeeded` record (cancelled, and refunded or nothing paid; or
    superseded) makes a replay a no-op. A `failed` one is retried on every
    later event: the cancel while Stripe still has it live, the refund
    (once, by its idempotency key) unless an earlier attempt already
    refunded or found nothing paid. If the group's row has meanwhile moved
    onto this subscription (the one it duplicated ended), it is the account's
    subscription now: nothing is cancelled or refunded, the record is closed
    as superseded. The operator is alerted the first time and whenever the
    outcome (status, cancelled, refund) changes. Never raises on a Stripe
    error."""
    from backend.models.billing_charge import BillingCharge
    from backend.services.operator_alerts import send_agent_duplicate_subscription_alert

    sub_id = _as_id(_field(sub, "id"))
    record = await _duplicate_record(db, og_id, sub_id)
    if record is not None and record.status == "succeeded":
        return {"duplicate": sub_id, "outcome": "already_handled"}
    previous = _record_outcome(record) if record is not None else None
    row = await _locked_row(db, og_id)
    kept = row.stripe_subscription_id if row else None
    if kept == sub_id:
        if record is None:
            return {"duplicate": sub_id, "outcome": "not_a_duplicate"}
        return await _supersede_duplicate(db, og_id, sub_id, record)

    errors: list[str] = []
    cancelled = _field(sub, "status") in TERMINAL_STATUSES
    if not cancelled:
        try:
            _client().v1.subscriptions.cancel(sub_id)
            cancelled = True
        except stripe.StripeError as e:
            errors.append(f"cancel failed: {e}")
            logger.warning("agent duplicate %s not cancelled: %s", sub_id, e)

    # refund: "refunded", "none" (nothing was paid), "failed" (Stripe
    # refused) or "unknown" (the invoice could not be read).
    amount = Decimal(record.amount_usd) if record is not None else Decimal(0)
    pi = None
    # Only a done refund is kept: "none" (nothing was paid then) is re-read,
    # the invoice may have been paid since.
    if previous is not None and previous[1] == "refunded":
        refund = previous[1]
    else:
        refund = "none"
        invoice_id = _as_id(_field(sub, "latest_invoice"))
        if invoice_id:
            try:
                invoice = _client().v1.invoices.retrieve(invoice_id, {"expand": ["payments"]})
            except stripe.StripeError as e:
                refund = "unknown"  # the amount found earlier, if any, is kept
                errors.append(f"invoice {invoice_id} not read: {e}")
                logger.warning("agent duplicate %s: invoice %s not read: %s",
                               sub_id, invoice_id, e)
            else:
                amount = Decimal(int(_field(invoice, "amount_paid") or 0)) / 100
                pi = _invoice_payment_intent(invoice)
                if amount > 0:
                    refund = "refunded" if _refund(pi, sub_id) else "failed"
                    if refund == "failed":
                        errors.append(f"refund of payment {pi or '?'} failed")

    done = cancelled and refund in ("refunded", "none")
    status = "succeeded" if done else "failed"
    changed = previous is None or (record.status, *previous) != (status, cancelled, refund)
    if record is None:
        record = BillingCharge(ownership_group_id=og_id, kind=DUPLICATE_REFUND_KIND,
                               stripe_object_id=sub_id)
        db.add(record)
    record.amount_usd = amount
    record.status = status
    record.error_message = "; ".join(
        [f"cancelled={'yes' if cancelled else 'no'} refund={refund}"] + errors)[:1000]
    await db.flush()
    if changed:
        send_agent_duplicate_subscription_alert(
            og_id, sub_id, kept, pi, float(amount), cancelled=cancelled, refund=refund,
            errors=errors)
    return {"duplicate": sub_id, "cancelled": cancelled, "refund": refund,
            "outcome": "handled" if done else "needs_attention"}


async def _supersede_duplicate(db: AsyncSession, og_id: str, sub_id: str, record: Any) -> dict:
    """Close an unfinished duplicate record whose subscription the group's
    row now holds (the one it duplicated ended): nothing is cancelled or
    refunded, the operator is told once."""
    from backend.services.operator_alerts import send_agent_duplicate_subscription_alert

    cancelled, refund = _record_outcome(record) or (False, "none")
    record.status = "succeeded"
    record.error_message = (f"cancelled={'yes' if cancelled else 'no'} refund={refund}; "
                            "superseded: became the account's subscription, no action taken")
    await db.flush()
    send_agent_duplicate_subscription_alert(
        og_id, sub_id, sub_id, None, float(record.amount_usd), cancelled=cancelled,
        refund=refund, errors=[], superseded=True)
    return {"duplicate": sub_id, "outcome": "superseded"}


def _verify_session(session: Any, og: OwnershipGroup) -> str:
    """The session's subscription id, if it is this group's agent
    subscription Checkout; SessionInvalid otherwise."""
    customer = _as_id(_field(session, "customer"))
    if not og.stripe_customer_id or customer != og.stripe_customer_id:
        raise SessionInvalid("session does not belong to this account")
    if _field(session, "mode") != "subscription":
        raise SessionInvalid("not a subscription session")
    md = _metadata(session)
    if not _is_agent_metadata(md):
        raise SessionInvalid("not an AI host session")
    if _field(md, "og_id") not in (None, og.id):
        raise SessionInvalid("session does not belong to this account")
    sub_id = _as_id(_field(session, "subscription"))
    if not sub_id:
        raise SessionInvalid("checkout not completed")
    return sub_id


# ---------------------------------------------------------------------------
# Overage (Task 3): InvoiceItems on the agent subscription's next invoice
# ---------------------------------------------------------------------------
#
# Every overage charge row names its subscription (stripe_subscription_id)
# and its period label (the period start's UTC date); labels are compared
# per subscription, so a cancel and a resubscribe on one day never share
# one. Per (subscription, period) the amount billed is:
#   settled  = charges no longer pending: succeeded (paid) or failed
#              (voided / uncollectible; never re-billed automatically);
#   pending  = at most one pending item while the period is current,
#              reconciled to overage - settled;
#   true-up  = for the previous period, overage - every charge, only added.

OVERAGE_KIND = "invoice_item_reservation_agent"
# error_message of a pending charge whose item went onto the final invoice.
FINAL_MARK = "final invoice "
# ...and the suffix while that invoice is a draft the run could not finalize
# (the daily run looks for it).
DRAFT_SUFFIX = "; draft"
_MAX_KEY_RETRIES = 3


class OverageError(AgentBillingError):
    """The overage could not be reconciled for a reason other than a
    Stripe error (no period, an idempotent replay of a deleted item)."""


def _period_label(start: datetime) -> str:
    return start.astimezone(timezone.utc).date().isoformat()


def _cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _overage_description(period: str) -> str:
    included = Decimal(str(settings.RESERVATIONS_INCLUDED_USAGE_USD)).quantize(_CENT)
    return f"AI host usage over ${included} — {period}"


def _item_metadata(og_id: str, period: str, sub_id: str) -> dict[str, str]:
    return {"og_id": og_id, "period": period, "kind": OVERAGE_KIND, "subscription": sub_id}


def _item_subscription(item: Any) -> str | None:
    """The subscription an InvoiceItem belongs to: our metadata, else its
    subscription (top level before API 2025-03-31, under
    parent.subscription_details since)."""
    return (_field(_metadata(item), "subscription")
            or _as_id(_field(item, "subscription"))
            or _as_id(_field(_field(_field(item, "parent"), "subscription_details"),
                             "subscription")))


async def _overage_charges(
    db: AsyncSession, og_id: str, sub_id: str, period: str | None, statuses: tuple[str, ...],
) -> list:
    from backend.models.billing_charge import BillingCharge

    q = select(BillingCharge).where(
        BillingCharge.ownership_group_id == og_id,
        BillingCharge.kind == OVERAGE_KIND,
        BillingCharge.stripe_subscription_id == sub_id,
        BillingCharge.status.in_(statuses),
    )
    if period is not None:
        q = q.where(BillingCharge.period == period)
    return list((await db.execute(
        q.order_by(BillingCharge.created_at).execution_options(populate_existing=True)
    )).scalars().all())


def _total(charges: list) -> Decimal:
    return sum((Decimal(c.amount_usd) for c in charges), Decimal(0))


async def _billed_labels(db: AsyncSession, og_id: str) -> set[str]:
    """Period labels the group has any overage charge for, any status, any
    subscription."""
    from backend.models.billing_charge import BillingCharge

    return set((await db.execute(
        select(BillingCharge.period).distinct().where(
            BillingCharge.ownership_group_id == og_id,
            BillingCharge.kind == OVERAGE_KIND)
    )).scalars().all())


async def _sweep_usage_periods(
    db: AsyncSession, og_id: str, start: datetime, end: datetime | None, since: datetime,
) -> int:
    """Re-stamp the group's usage rows created since `since` (the
    subscription's start) whose period_start is not the start of any period
    of the subscription as Stripe has it now (start..end): a row metered
    against a stale or misaligned stored period. Each moves to the period
    containing its created_at.

    Never moves a row out of a period label that already has an overage
    charge (any status): that usage has been billed under its label, and
    moving it would bill it again (an anchor reset makes every earlier
    label misaligned). Those rows keep their stale label for good (each
    run logs them with their count); their usage stays billed exactly
    once, under that label. Rows from before `since`
    belong to an earlier subscription and are not looked at. Returns the
    number moved."""
    stamps = (await db.execute(
        select(AgentUsage.period_start).distinct()
        .where(AgentUsage.ownership_group_id == og_id, AgentUsage.created_at >= since)
    )).scalars().all()
    bad = [p for p in stamps if period_start_for(start, end, p) != p]
    if not bad:
        return 0
    billed = await _billed_labels(db, og_id)
    kept = [p for p in bad if _period_label(p) in billed]
    if kept:
        left = (await db.execute(
            select(AgentUsage.period_start, func.count(), func.max(AgentUsage.created_at)).where(
                AgentUsage.ownership_group_id == og_id, AgentUsage.created_at >= since,
                AgentUsage.period_start.in_(kept)).group_by(AgentUsage.period_start)
        )).all()
        # Nothing is stored about them: rows made since the previous period
        # began are a fresh finding (a warning, the first runs after the
        # anchor moved); older ones were reported before (info).
        recent = period_start_for(start, end, start - timedelta(microseconds=1))
        for stamp, n, newest in left:
            logger.log(logging.WARNING if newest >= recent else logging.INFO,
                       "agent usage of og %s: %d row(s) stamped %s are off the "
                       "subscription's periods (an anchor reset) but already billed "
                       "under label %s; left with that stale label, never re-billed",
                       og_id, n, stamp.isoformat(), _period_label(stamp))
    movable = [p for p in bad if p not in kept]
    if not movable:
        return 0
    rows = (await db.execute(
        select(AgentUsage).where(AgentUsage.ownership_group_id == og_id,
                                 AgentUsage.created_at >= since,
                                 AgentUsage.period_start.in_(movable))
    )).scalars().all()
    for usage in rows:
        moved = period_start_for(start, end, usage.created_at)
        logger.warning("agent usage %s (og %s) re-stamped from period %s to %s",
                       usage.id, og_id, usage.period_start.isoformat(), moved.isoformat())
        usage.period_start = moved
    await db.flush()
    return len(rows)


def _delete_invoice_item(item_id: str | None, deleted: set[str]) -> None:
    """Delete one pending InvoiceItem; one Stripe already lacks is gone
    already. Any other error (it is on a finalized invoice, Stripe is down)
    propagates: the caller must not bill a replacement next to it."""
    if not item_id:
        return
    try:
        _client().v1.invoice_items.delete(item_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) != "resource_missing":
            raise
    deleted.add(item_id)


def _delete_orphans(customer: str, sub_id: str, period: str, recorded: set[str],
                    deleted: set[str]) -> int:
    """Delete the customer's pending overage items for this subscription and
    period that no charge row records (a run that created one and died
    before its commit). Returns the count."""
    page = _client().v1.invoice_items.list({"customer": customer, "pending": True, "limit": 100})
    n = 0
    for item in page.auto_paging_iter():
        md = _metadata(item)
        item_id = _as_id(item)
        if (_field(md, "kind") != OVERAGE_KIND or _field(md, "period") != period
                or _item_subscription(item) != sub_id or item_id in recorded
                or item_id in deleted):
            continue
        logger.warning("agent overage item %s (subscription %s, period %s) has no charge "
                       "row; deleting it", item_id, sub_id, period)
        _delete_invoice_item(item_id, deleted)
        n += 1
    return n


def _create_invoice_item(
    og_id: str, customer: str, sub_id: str, period: str, cents: int, key: str,
    deleted: set[str], invoice: str | None = None,
) -> str:
    """Create one overage InvoiceItem (on the subscription's next invoice,
    or on `invoice`). An idempotency key Stripe has seen returns the first
    item even if it was deleted since: when the id that comes back is one
    deleted in this run, retry with the key suffixed -r1, -r2, ..."""
    params: dict[str, Any] = {
        "customer": customer, "amount": cents, "currency": "usd",
        "description": _overage_description(period),
        "metadata": _item_metadata(og_id, period, sub_id),
    }
    if invoice:
        params["invoice"] = invoice
    else:
        params["subscription"] = sub_id
    for attempt in range(_MAX_KEY_RETRIES + 1):
        attempt_key = key if attempt == 0 else f"{key}-r{attempt}"
        item_id = _as_id(_client().v1.invoice_items.create(
            params, {"idempotency_key": attempt_key}))
        if item_id not in deleted:
            return item_id
        logger.warning("idempotency key %s replayed deleted item %s; retrying", attempt_key, item_id)
    raise OverageError(f"every key for {key} replays a deleted item")


def _new_charge(og_id: str, sub_id: str, period: str, amount: Decimal, item_id: str) -> Any:
    from backend.models.billing_charge import BillingCharge

    return BillingCharge(ownership_group_id=og_id, kind=OVERAGE_KIND, amount_usd=amount,
                         stripe_object_id=item_id, stripe_subscription_id=sub_id,
                         period=period, status="pending")


async def _overage_for(db: AsyncSession, og_id: str, period_start: datetime) -> tuple[Decimal, Decimal]:
    """(billed usage, overage) of one period: overage = usage over the
    included amount, never negative."""
    used = await period_usage(db, og_id, period_start)
    included = Decimal(str(settings.RESERVATIONS_INCLUDED_USAGE_USD))
    return used, max(Decimal(0), used - included).quantize(_CENT)


async def _reconcile_current(
    db: AsyncSession, og_id: str, customer: str, sub_id: str, period: str, overage: Decimal,
    deleted: set[str],
) -> dict:
    """Make the current period's one pending InvoiceItem (and its pending
    BillingCharge) match overage minus what is settled: noop when equal,
    else delete the old one and any orphan, then create the new; at zero
    only delete. Raises stripe.StripeError with the DB as consistent as
    Stripe left it (a deleted item's row is gone)."""
    settled_charges = await _overage_charges(db, og_id, sub_id, period, ("succeeded", "failed"))
    settled = _total(settled_charges)
    target = max(Decimal(0), overage - settled).quantize(_CENT)
    cents = _cents(target)
    pending = await _overage_charges(db, og_id, sub_id, period, ("pending",))
    if len(pending) == 1 and _cents(pending[0].amount_usd) == cents and cents > 0:
        return {"action": "noop", "amount_usd": float(target)}
    for charge in pending:  # normally at most one
        _delete_invoice_item(charge.stripe_object_id, deleted)
        await db.delete(charge)
        await db.flush()
    orphans = _delete_orphans(customer, sub_id, period,
                              {c.stripe_object_id for c in settled_charges}, deleted)
    if cents == 0:
        action = "deleted" if pending or orphans else "skip_zero"
        return {"action": action, "amount_usd": 0.0}
    # A failed create leaves the old row deleted (the caller commits on a
    # Stripe error), so nothing claims an item Stripe no longer has.
    item_id = _create_invoice_item(og_id, customer, sub_id, period, cents,
                                   f"agent-overage-{og_id}-{period}-{cents}", deleted)
    db.add(_new_charge(og_id, sub_id, period, target, item_id))
    await db.flush()
    return {"action": "updated" if pending else "created", "amount_usd": float(target)}


async def _true_up_previous(
    db: AsyncSession, og_id: str, customer: str, sub_id: str, prev_start: datetime,
    deleted: set[str],
) -> dict | None:
    """Bill what the previous period ran up after its last reconcile (the
    hours between the last run and the renewal, a conversation still
    running across the boundary): its overage minus every charge for it,
    whatever the status (a voided one is not re-billed). Only ever adds:
    the previous period's items are on an invoice by now."""
    period = _period_label(prev_start)
    _, overage = await _overage_for(db, og_id, prev_start)
    charges = await _overage_charges(db, og_id, sub_id, period, ("pending", "succeeded", "failed"))
    billed = _total(charges)
    delta = (overage - billed).quantize(_CENT)
    if delta <= 0:
        if delta < 0:
            logger.warning("agent overage og %s period %s: %s billed over %s owed",
                           og_id, period, billed, overage)
        return None
    _delete_orphans(customer, sub_id, period, {c.stripe_object_id for c in charges}, deleted)
    item_id = _create_invoice_item(og_id, customer, sub_id, period, _cents(delta),
                                   f"agent-overage-{og_id}-{period}-trueup-{_cents(overage)}",
                                   deleted)
    db.add(_new_charge(og_id, sub_id, period, delta, item_id))
    await db.flush()
    return {"period": period, "action": "true_up", "amount_usd": float(delta)}


async def _reconcile_subscription(db: AsyncSession, og_id: str, sub: Any, deleted: set[str]) -> dict:
    """Sweep, current period and previous-period true-up for `sub`, the
    subscription as Stripe has it now. No status gate. Caller holds the
    group lock and commits."""
    sub_id = _as_id(_field(sub, "id"))
    start, end = _period(sub)
    customer = _as_id(_field(sub, "customer"))
    if start is None or not customer:
        raise OverageError("subscription has no current period or customer")
    since = _when(_field(sub, "start_date")) or start
    summary: dict = {"swept": await _sweep_usage_periods(db, og_id, start, end, since)}
    period = _period_label(start)
    used, overage = await _overage_for(db, og_id, start)
    summary.update(period=period, usage_usd=float(used), overage_usd=float(overage))
    summary.update(await _reconcile_current(db, og_id, customer, sub_id, period, overage, deleted))
    prev_start = period_start_for(start, end, start - timedelta(microseconds=1))
    if prev_start >= since:
        true_up = await _true_up_previous(db, og_id, customer, sub_id, prev_start, deleted)
        if true_up:
            summary["true_up"] = true_up
    return summary


def _og_id_of(og: OwnershipGroup) -> str:
    # The key without a load: `og` may be expired by the caller's commit.
    identity = sa_inspect(og).identity
    return identity[0] if identity else og.id


async def reconcile_agent_overage(
    db: AsyncSession, og: OwnershipGroup, now: datetime | None = None,
) -> dict:
    """Bill the agent subscription's current-period usage over the included
    amount as one InvoiceItem on its upcoming invoice (decision 37), the
    pattern of billing.bill_monthly_overages_for_og.

    Under the group lock: re-read the subscription from Stripe (skip unless
    BILLABLE_STATUSES); re-stamp misassigned usage rows
    (_sweep_usage_periods); make the current period's pending item match
    its overage (_reconcile_current); and true up the previous period
    (_true_up_previous). Period = the current period's start date (UTC,
    YYYY-MM-DD). Commits. Never raises on a Stripe error: logs it and
    returns {"error": ...}; whatever Stripe had already done is recorded."""
    del now  # Stripe's current period decides; kept for the job's signature
    og_id = _og_id_of(og)
    summary: dict = {"og_id": og_id}
    await _lock_group(db, og_id)
    row = await _locked_row(db, og_id)
    if row is None or not row.stripe_subscription_id:
        await db.rollback()
        return {**summary, "skipped": "no_subscription"}
    sub_id = row.stripe_subscription_id
    summary["subscription"] = sub_id
    try:
        sub = _retrieve_subscription(sub_id)
        status = _field(sub, "status")
        if status not in BILLABLE_STATUSES:
            await db.rollback()
            return {**summary, "skipped": f"subscription_status={status}"}
        summary.update(await _reconcile_subscription(db, og_id, sub, set()))
    except (stripe.StripeError, AgentBillingError) as e:
        logger.warning("agent overage og %s (subscription %s) not reconciled: %s", og_id, sub_id, e)
        await db.commit()
        return {**summary, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    await db.commit()
    return summary


# Final period (Ruling V) -----------------------------------------------------
#
# A canceled subscription gets no further invoice, and Stripe may refuse an
# item or invoice that names it. Its unbilled overage goes on a standalone
# final invoice that never names the subscription.
#
# Stripe is the record of what a final invoice billed, not our rows: every
# run first lists the customer's invoices carrying our metadata for this
# subscription and the items on them. One past its draft (open, paid,
# uncollectible, void) is billed: an item on it with no charge row (the run
# that finalized it died before its commit) is adopted as a row, and its
# amount is never billed again. A draft is unfinished work and is brought
# to what is owed, then finalized.
#
# What is owed, one item per period:
#   - the last period: overage - settled - what final invoices past their
#     draft hold - regular items already on an invoice (Ruling X);
#   - every other period: the regular pending items not yet on any invoice
#     (a true-up made after the renewal invoice), less what final invoices
#     past their draft hold for it.
#
# Ruling X: a pending charge row only means no payment webhook yet. Its
# InvoiceItem is retrieved first: with `invoice` set it is billed there
# already and is left alone (row pending, for that invoice's webhook). Only
# an item on no invoice moves: its replacement goes on the final draft, the
# draft is checked against what is owed, then the old item is deleted, and
# only then is the draft finalized. A delete refused because the item was
# invoiced meanwhile starts the pass again, which takes the replacement
# back off the draft.
#
# Nothing is recorded until the invoice is finalized. A finalize error is
# settled by reading the invoice's real status.

_FINAL_PASSES = 4
_PAST_DRAFT_UNPAID = {"void": "voided", "uncollectible": "marked uncollectible"}


class _Replan(Exception):
    """Stripe turned out different from what the pass assumed: plan again."""


class _FinalRun:
    """What one finalize call has made in Stripe, for its failure path."""

    def __init__(self) -> None:
        self.created_items: list[str] = []
        self.new_invoice: str | None = None
        self.deleted: set[str] = set()
        self.extra: set[str] = set()   # invoice ids to read besides the listing
        # True once the draft holds what is owed: from then on a failure
        # leaves it for the next run instead of deleting it (the old items
        # may be gone already, the draft is where the amounts live).
        self.keep = False
        self.finalized = False
        self.invoice: str | None = None            # the draft kept / finalized
        self.held: list[tuple[str, str, int]] = []  # its items once checked
        self.nothing_owed = False
        self.movable_items: tuple[str, ...] = ()    # old items not yet moved
        # The cancelled subscription's card (its default_payment_method), read
        # once by finalize_agent_billing; the final invoice is charged to it.
        self.sub_payment_method: str | None = None


def _final_invoice_of(charge: Any) -> str | None:
    msg = charge.error_message or ""
    return msg[len(FINAL_MARK):].split(";", 1)[0].strip() if msg.startswith(FINAL_MARK) else None


def _supersede(charge: Any, why: str) -> None:
    charge.status = "superseded"
    charge.error_message = why[:1000]


def _invoice_status(inv_id: str) -> str | None:
    """A final invoice's status; None when Stripe no longer has it (a
    deleted draft)."""
    try:
        inv = _client().v1.invoices.retrieve(inv_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) == "resource_missing":
            return None
        raise
    return None if _field(inv, "deleted") else _field(inv, "status")


def _final_invoices(customer: str, sub_id: str, extra: set[str]) -> dict[str, str]:
    """{invoice id: status} of every final invoice Stripe holds for the
    subscription: the customer's standalone invoices carrying our metadata,
    plus those of `extra` (ids our rows or a replayed key name) that exist."""
    found: dict[str, str] = {}
    page = _client().v1.invoices.list({"customer": customer, "limit": 100})
    for inv in page.auto_paging_iter():
        md = _metadata(inv)
        inv_id = _as_id(inv)
        if (inv_id and _is_agent_metadata(md) and _field(md, "subscription") == sub_id
                and not _invoice_subscription_id(inv)):
            found[inv_id] = _field(inv, "status")
    for inv_id in sorted(extra - set(found)):
        status = _invoice_status(inv_id)
        if status is not None:
            found[inv_id] = status
    return found


def _invoice_items_on(inv_id: str) -> list:
    page = _client().v1.invoice_items.list({"invoice": inv_id, "limit": 100})
    return list(page.auto_paging_iter())


def _final_items(inv_id: str) -> list[tuple[str, str, int]]:
    """(item id, period, cents) of our overage items on one invoice."""
    out = []
    for item in _invoice_items_on(inv_id):
        md = _metadata(item)
        if _field(md, "kind") == OVERAGE_KIND and _as_id(item):
            out.append((_as_id(item), _field(md, "period"), int(_field(item, "amount") or 0)))
    return out


def _item_state(item_id: str | None) -> tuple[str | None, int | None] | None:
    """(invoice id or None, cents) of an InvoiceItem as Stripe has it now;
    None when Stripe no longer has the item."""
    if not item_id:
        return None
    try:
        item = _client().v1.invoice_items.retrieve(item_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) == "resource_missing":
            return None
        raise
    if _field(item, "deleted"):
        return None
    amount = _field(item, "amount")
    return _as_id(_field(item, "invoice")), (int(amount) if amount is not None else None)


def _final_payment_method(customer: str, run: _FinalRun) -> str | None:
    """The card the final invoice is charged to. A standalone invoice does
    not inherit the subscription's default_payment_method, so a customer
    whose card sits only on the AI host subscription would never be
    charged: the cancelled subscription's card, else the customer's
    invoice_settings default, else None (Stripe's own fallbacks)."""
    if run.sub_payment_method:
        return run.sub_payment_method
    cust = _client().v1.customers.retrieve(customer)
    return _as_id(_field(_field(cust, "invoice_settings"), "default_payment_method"))


def _create_final_invoice(customer: str, sub_id: str, og_id: str, period: str, cents: int,
                          known: dict[str, str], run: _FinalRun) -> str:
    """A new draft final invoice (auto_advance False: finalized by the
    caller once its items are on it). Key agent-final-<sub>-<cents>. The
    key can replay an earlier invoice, so what comes back is read again:
      - deleted, or a final already counted in `known` (an earlier one of
        the same total): the next key, -r1, -r2, ...;
      - a draft with nothing on it: used;
      - anything else is a final invoice of this subscription the listing
        did not show (finalized, or holding items): never a second invoice
        next to it; it is added to the run and the pass starts again."""
    params = {
        "customer": customer,
        "pending_invoice_items_behavior": "exclude",
        "auto_advance": False,
        "collection_method": "charge_automatically",
        "description": f"Final AI host usage — {period}",
        "metadata": {"product": PRODUCT, "og_id": og_id, "subscription": sub_id},
    }
    payment_method = _final_payment_method(customer, run)
    if payment_method:
        params["default_payment_method"] = payment_method
    key = f"agent-final-{sub_id}-{cents}"
    for attempt in range(_MAX_KEY_RETRIES + 1):
        attempt_key = key if attempt == 0 else f"{key}-r{attempt}"
        inv_id = _as_id(_client().v1.invoices.create(params, {"idempotency_key": attempt_key}))
        status = _invoice_status(inv_id)
        if status is None or inv_id in known:
            logger.warning("final invoice key %s replayed %s (%s); retrying", attempt_key,
                           inv_id, "deleted" if status is None else f"already counted, {status}")
            continue
        if status == "draft" and not _invoice_items_on(inv_id):
            return inv_id
        run.extra.add(inv_id)
        raise _Replan(f"key {attempt_key} replayed final invoice {inv_id} ({status}) "
                      f"that the listing did not show")
    raise OverageError(f"every key for {key} replays an invoice that cannot be used")


def _undo(created_items: list[str], invoice_id: str | None) -> list[str]:
    """Best-effort removal of what a failed finalize made in Stripe."""
    problems = []
    for item_id in created_items:
        try:
            _delete_invoice_item(item_id, set())
        except stripe.StripeError as e:
            problems.append(f"item {item_id} not removed: {e}")
    if invoice_id:
        try:
            _client().v1.invoices.delete(invoice_id)
        except stripe.StripeError as e:
            if getattr(e, "code", None) != "resource_missing":
                problems.append(f"draft {invoice_id} not deleted: {e}")
    return problems


def _discard_draft(inv_id: str, run: _FinalRun) -> None:
    """Delete a draft final invoice, its items first (so none is ever left
    pending on the customer). Raises on a Stripe error."""
    if run.invoice == inv_id:
        run.keep, run.invoice, run.held = False, None, []
    for item in _invoice_items_on(inv_id):
        _delete_invoice_item(_as_id(item), run.deleted)
    try:
        _client().v1.invoices.delete(inv_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) != "resource_missing":
            raise


def _delete_moved(charges: list) -> None:
    """Delete the subscription-attached items whose replacements are on the
    final draft. One Stripe refuses to delete is read again: on an invoice
    now, it is billed there (plan again); otherwise the error stands and
    the draft is not finalized next to an item that still exists."""
    for charge in charges:
        try:
            _delete_invoice_item(charge.stripe_object_id, set())
        except stripe.StripeError as e:
            state = _item_state(charge.stripe_object_id)
            if state is not None and state[0]:
                raise _Replan(f"item {charge.stripe_object_id} went onto invoice "
                              f"{state[0]} meanwhile") from e
            raise


def _final_charge(og_id: str, sub_id: str, period: str, cents: int, item_id: str,
                  invoice_id: str) -> Any:
    charge = _new_charge(og_id, sub_id, period, Decimal(cents) / 100, item_id)
    charge.error_message = f"{FINAL_MARK}{invoice_id}"
    return charge


async def _final_pass(
    db: AsyncSession, og_id: str, sub_id: str, customer: str, period: str, overage: Decimal,
    run: _FinalRun,
) -> dict:
    """One attempt at the final invoice from Stripe's state as it is now
    (see the notes above). Returns the summary fields; flushes, the caller
    commits. Raises _Replan when Stripe changed under it."""
    from collections import defaultdict

    await db.flush()
    rows = await _overage_charges(db, og_id, sub_id, None,
                                  ("pending", "succeeded", "failed", "superseded"))
    by_item = {c.stripe_object_id: c for c in rows if c.stripe_object_id}
    run.extra |= {inv for c in rows if c.status == "pending" and (inv := _final_invoice_of(c))}
    finals = _final_invoices(customer, sub_id, run.extra)

    # Final invoices past their draft: billed. Adopt what no row records.
    final_billed: dict[str, Decimal] = defaultdict(Decimal)
    final_item_ids: set[str] = set()
    listed: set[str] = set()
    adopted = 0
    for inv_id in sorted(finals):
        status = finals[inv_id]
        if status == "draft":
            continue
        for item_id, p, cents in _final_items(inv_id):
            final_billed[p] += Decimal(cents) / 100
            final_item_ids.add(item_id)
            listed.add(item_id)
            charge = by_item.get(item_id)
            if charge is None:
                logger.warning("final AI host invoice %s (%s) of og %s holds item %s that no "
                               "charge row records; adopting it as billed", inv_id, status,
                               og_id, item_id)
                charge = by_item[item_id] = _final_charge(og_id, sub_id, p, cents, item_id, inv_id)
                db.add(charge)
                adopted += 1
            elif charge.status == "pending" and (charge.error_message or "").endswith(DRAFT_SUFFIX):
                # Recorded as a kept draft; it was finalized after all.
                charge.error_message = f"{FINAL_MARK}{inv_id}"
                adopted += 1
            if charge.status == "pending":  # a webhook that was missed, or came first
                if status == "paid":
                    charge.status = "succeeded"
                elif status in _PAST_DRAFT_UNPAID:
                    charge.status = "failed"
                    charge.error_message = f"invoice {inv_id} {_PAST_DRAFT_UNPAID[status]}"

    # Drafts: one is kept and brought to what is owed; any other is deleted.
    drafts = sorted(i for i, s in finals.items() if s == "draft")
    draft_id: str | None = None
    if drafts:
        draft_id = run.new_invoice if run.new_invoice in drafts else drafts[0]
        for other in drafts:
            if other != draft_id:
                logger.warning("final AI host invoice og %s: second draft %s deleted", og_id, other)
                _discard_draft(other, run)
    on_draft: dict[str, list[tuple[str, int]]] = defaultdict(list)
    if draft_id:
        for item_id, p, cents in _final_items(draft_id):
            on_draft[p].append((item_id, cents))
            listed.add(item_id)
    for charge in rows:  # a recorded final item Stripe no longer has there
        inv_id = _final_invoice_of(charge)
        if charge.status != "pending" or inv_id is None:
            continue
        if inv_id not in finals or (inv_id != draft_id and finals[inv_id] == "draft"):
            _supersede(charge, f"final draft {inv_id} no longer exists")
        elif charge.stripe_object_id not in listed:
            _supersede(charge, f"item no longer on final invoice {inv_id}")

    # Ruling X: regular pending charges, by what Stripe says of their item.
    movable: list = []
    invoiced: dict[str, Decimal] = defaultdict(Decimal)
    for charge in rows:
        if (charge.status != "pending" or _final_invoice_of(charge) is not None
                or charge.stripe_object_id in final_item_ids):
            continue
        state = _item_state(charge.stripe_object_id)
        if state is not None and state[0]:
            cents = state[1] if state[1] is not None else _cents(charge.amount_usd)
            invoiced[charge.period] += Decimal(cents) / 100
            logger.info("agent overage item %s (og %s, period %s) is on invoice %s already; "
                        "left for that invoice", charge.stripe_object_id, og_id, charge.period,
                        state[0])
        else:
            movable.append(charge)

    settled = sum((Decimal(c.amount_usd) for c in rows
                   if c.period == period and c.status in ("succeeded", "failed")
                   and c.stripe_object_id not in final_item_ids), Decimal(0))
    wanted: dict[str, int] = {period: _cents(max(
        Decimal(0), overage - settled - final_billed[period] - invoiced[period]))}
    moving: dict[str, Decimal] = defaultdict(Decimal)
    for charge in movable:
        if charge.period != period:
            moving[charge.period] += Decimal(charge.amount_usd)
    for p, amount in moving.items():
        wanted[p] = _cents(max(Decimal(0), amount - final_billed[p]))
    wanted = {p: c for p, c in wanted.items() if c > 0}

    run.movable_items = tuple(c.stripe_object_id for c in movable if c.stripe_object_id)
    run.nothing_owed = not wanted
    out: dict = {"final_invoice": None, "amount_usd": 0.0, "retired": movable}
    if adopted:
        out["adopted"] = adopted
    if not wanted:
        if draft_id:
            _discard_draft(draft_id, run)
            for item_id in [i for held in on_draft.values() for i, _ in held]:
                if item_id in by_item and by_item[item_id].status == "pending":
                    _supersede(by_item[item_id],
                               f"final draft {draft_id} deleted: nothing left to bill")
            out["action"] = "draft_deleted"
        else:
            out["action"] = "adopted" if adopted else "noop"
        _delete_moved(movable)
        out["why"] = "subscription ended; on no further invoice"
        await db.flush()
        return out

    invoice_id = draft_id
    if invoice_id is None:
        invoice_id = _create_final_invoice(customer, sub_id, og_id, period,
                                           sum(wanted.values()), finals, run)
        run.new_invoice = invoice_id
    for p in sorted(set(wanted) | set(on_draft)):
        want = wanted.get(p, 0)
        have = sum(c for _, c in on_draft.get(p, []))
        if have > want:  # the draft holds more than is owed: take it off
            logger.warning("final AI host invoice og %s period %s: draft %s holds %d cents, "
                           "%d owed; replacing its item", og_id, p, invoice_id, have, want)
            for item_id, _ in on_draft[p]:
                _delete_invoice_item(item_id, run.deleted)
                if item_id in by_item and by_item[item_id].status == "pending":
                    _supersede(by_item[item_id], f"removed from final draft {invoice_id}")
            have = 0
        if want > have:
            run.created_items.append(_create_invoice_item(
                og_id, customer, sub_id, p, want - have,
                f"agent-final-item-{invoice_id}-{p}-{want - have}", run.deleted,
                invoice=invoice_id))

    # The draft must hold exactly what is owed before anything irreversible.
    held = _final_items(invoice_id)
    actual: dict[str, int] = defaultdict(int)
    for _, p, cents in held:
        actual[p] += cents
    if {p: c for p, c in actual.items() if c} != wanted:
        # A replayed item key, or a change from outside: start from a new draft.
        _discard_draft(invoice_id, run)
        if run.new_invoice == invoice_id:
            run.new_invoice = None
        raise _Replan(f"draft {invoice_id} held {dict(actual)}, owed {wanted}")
    run.keep, run.invoice, run.held = True, invoice_id, held
    _delete_moved(movable)

    try:
        _client().v1.invoices.finalize_invoice(invoice_id, {"auto_advance": True})
    except stripe.StripeError as e:
        # The call may have gone through: the invoice's status decides.
        status = _invoice_status(invoice_id)
        if status in (None, "draft"):
            raise
        logger.warning("finalizing final AI host invoice %s raised %s but it is %s; "
                       "recording it as billed", invoice_id, type(e).__name__, status)
    run.finalized = True
    made = 0
    for item_id, p, cents in held:
        if item_id in run.created_items:
            made += cents
        charge = by_item.get(item_id)
        if charge is None or charge.status == "superseded":
            db.add(_final_charge(og_id, sub_id, p, cents, item_id, invoice_id))
        elif charge.status == "pending":
            charge.error_message = f"{FINAL_MARK}{invoice_id}"
    out.update(final_invoice=invoice_id, action="invoiced" if made else "finalized",
               amount_usd=float(Decimal(made) / 100),
               why=f"replaced by final invoice {invoice_id}")
    await db.flush()
    return out


async def _mark_kept_draft(db: AsyncSession, og_id: str, sub_id: str, run: _FinalRun) -> list[str]:
    """After a failed run that left its draft (or an invoice it finalized
    but could not record): record the invoice's items as pending charges
    marked as a draft, so the daily run finds the group again
    (_unfinished_finals). The next pass corrects them from Stripe. Never
    raises; returns what went wrong."""
    from backend.models.billing_charge import BillingCharge

    try:
        await _lock_group(db, og_id)
        ids = [item_id for item_id, _, _ in run.held]
        have = set((await db.execute(
            select(BillingCharge.stripe_object_id).where(
                BillingCharge.ownership_group_id == og_id, BillingCharge.kind == OVERAGE_KIND,
                BillingCharge.stripe_object_id.in_(ids), BillingCharge.status != "superseded")
        )).scalars().all()) if ids else set()
        for item_id, p, cents in run.held:
            if item_id not in have:
                charge = _final_charge(og_id, sub_id, p, cents, item_id, run.invoice or "")
                charge.error_message += DRAFT_SUFFIX
                db.add(charge)
        await db.commit()
        return []
    except Exception as e:  # noqa: BLE001
        logger.exception("final AI host invoice og %s: draft %s not recorded", og_id, run.invoice)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return [f"draft {run.invoice} not recorded for the daily retry: {type(e).__name__}"]


async def _unfinished_finals(db: AsyncSession) -> list[tuple[str, str]]:
    """(group, subscription) of canceled agent subscriptions with final
    billing still to do: a pending overage charge that is not on a
    finalized final invoice (a regular item never moved, or a kept draft)."""
    from backend.models.billing_charge import BillingCharge

    return [(og_id, sub_id) for og_id, sub_id in (await db.execute(
        select(ReservationSubscription.ownership_group_id,
               ReservationSubscription.stripe_subscription_id)
        .where(ReservationSubscription.status == "canceled",
               ReservationSubscription.stripe_subscription_id.is_not(None),
               select(BillingCharge.id).where(
                   BillingCharge.ownership_group_id == ReservationSubscription.ownership_group_id,
                   BillingCharge.stripe_subscription_id == ReservationSubscription.stripe_subscription_id,
                   BillingCharge.kind == OVERAGE_KIND, BillingCharge.status == "pending",
                   BillingCharge.error_message.is_(None)
                   | BillingCharge.error_message.not_like(f"{FINAL_MARK}%")
                   | BillingCharge.error_message.like(f"%{DRAFT_SUFFIX}")).exists())
        .order_by(ReservationSubscription.ownership_group_id)
    )).all()]


async def finalize_agent_billing(db: AsyncSession, og_id: str, sub_id: str) -> dict:
    """The agent subscription ended (canceled): bill what it never will
    (see the notes above) on one standalone final invoice. Under the group
    lock; commits; never raises: a failure is logged, returned and sent to
    the operator. Replay safe, also after a crash at any point: every run
    starts from the final invoices Stripe holds. `amount_usd` is what this
    call put on a final invoice."""
    from backend.services.operator_alerts import send_agent_final_invoice_alert

    summary: dict = {"og_id": og_id, "subscription": sub_id, "final_invoice": None,
                     "amount_usd": 0.0}
    period = None
    run = _FinalRun()
    try:
        await _lock_group(db, og_id)
        sub = _retrieve_subscription(sub_id)
        if _field(sub, "status") != "canceled":
            await db.rollback()
            return {**summary, "skipped": f"subscription_status={_field(sub, 'status')}"}
        start, end = _period(sub)
        customer = _as_id(_field(sub, "customer"))
        if start is None or not customer:
            raise OverageError("subscription has no current period or customer")
        run.sub_payment_method = _as_id(_field(sub, "default_payment_method"))
        since = _when(_field(sub, "start_date")) or start
        summary["swept"] = await _sweep_usage_periods(db, og_id, start, end, since)
        period = _period_label(start)
        used, overage = await _overage_for(db, og_id, start)
        summary.update(period=period, usage_usd=float(used), overage_usd=float(overage))

        result: dict | None = None
        for _ in range(_FINAL_PASSES):
            try:
                result = await _final_pass(db, og_id, sub_id, customer, period, overage, run)
                break
            except _Replan as e:
                logger.warning("final AI host invoice og %s: %s; planning again", og_id, e)
        if result is None:
            raise OverageError("Stripe kept changing under the final invoice")
        why = result.pop("why")
        for charge in result.pop("retired"):
            _supersede(charge, why)
        await db.flush()
        await db.commit()
        summary.update(result)
        return summary
    except Exception as e:  # noqa: BLE001 - never raises into the webhook or the daily run
        log = logger.warning if isinstance(e, (stripe.StripeError, AgentBillingError)) \
            else logger.exception
        log("final AI host invoice for og %s (subscription %s) failed: %s", og_id, sub_id, e)
        problems: list[str] = []
        try:
            await db.rollback()  # nothing is recorded unless the commit went through
        except Exception as rollback_error:  # noqa: BLE001
            problems.append(f"rollback failed: {rollback_error}")
        case = "not_billed"
        if run.finalized:
            case = "finalized_unrecorded"
            problems.append("the invoice was finalized in Stripe but not recorded; "
                            "the next run adopts it from Stripe")
        elif run.keep:
            case = "draft_kept"
            problems.append("the draft holding what is owed was left in Stripe for the next run")
        elif run.nothing_owed:
            case = "nothing_owed"
        else:
            problems += _undo(run.created_items, run.new_invoice)
        if run.keep and run.invoice:
            problems += await _mark_kept_draft(db, og_id, sub_id, run)
        detail = "; ".join([f"{type(e).__name__}: {str(e)[:300]}"] + problems)
        send_agent_final_invoice_alert(og_id, sub_id, period, detail, case=case,
                                       invoice_id=run.invoice, item_ids=run.movable_items)
        return {**summary, "amount_usd": 0.0, "final_invoice": None,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}


# Invoice outcomes ------------------------------------------------------------


def _invoice_item_ids(invoice: Any) -> list[str]:
    """The InvoiceItem ids on an invoice's lines: line.invoice_item before
    API 2025-03-31, line.parent.invoice_item_details.invoice_item since."""
    ids = []
    for line in _field(_field(invoice, "lines"), "data") or []:
        item = _as_id(_field(line, "invoice_item")) or _as_id(_field(
            _field(_field(line, "parent"), "invoice_item_details"), "invoice_item"))
        if item:
            ids.append(item)
    return ids


async def _pending_on_invoice(db: AsyncSession, og_id: str, invoice: Any) -> list:
    from backend.models.billing_charge import BillingCharge

    ids = _invoice_item_ids(invoice)
    if not ids:
        return []
    return list((await db.execute(
        select(BillingCharge).where(
            BillingCharge.ownership_group_id == og_id,
            BillingCharge.kind == OVERAGE_KIND,
            BillingCharge.status == "pending",
            BillingCharge.stripe_object_id.in_(ids),
        )
    )).scalars().all())


async def _mark_overage_paid(db: AsyncSession, og_id: str, invoice: Any) -> int:
    """A paid agent invoice: its overage items' pending BillingCharges
    become succeeded."""
    charges = await _pending_on_invoice(db, og_id, invoice)
    for charge in charges:
        charge.status = "succeeded"
    await db.flush()
    return len(charges)


UNPAID_OUTCOMES = {"invoice.voided": "voided",
                   "invoice.marked_uncollectible": "marked uncollectible"}


async def _mark_overage_unpaid(db: AsyncSession, og_id: str, invoice: Any, outcome: str) -> list:
    """Ruling W: a voided or uncollectible agent invoice: its overage items'
    pending charges become failed. Nothing is re-billed automatically (the
    reconcile counts failed charges as settled); the caller alerts."""
    charges = await _pending_on_invoice(db, og_id, invoice)
    for charge in charges:
        charge.status = "failed"
        charge.error_message = f"invoice {_as_id(_field(invoice, 'id'))} {outcome}"
    await db.flush()
    return charges


async def reconcile_all_agent_subscriptions(db: AsyncSession, now: datetime | None = None) -> dict:
    """The daily job (backend/scripts/run_agent_overage_reconcile.py): for
    every group whose agent row still has a subscription that is not ended,
    re-sync the row from Stripe; one that has ended meanwhile (a missed
    customer.subscription.deleted) gets its final invoice
    (finalize_agent_billing); each still billable is reconciled. Before
    that, ended subscriptions with unfinished final billing are retried
    (_unfinished_finals). One group's failure never stops the others."""
    from backend.tenant import tenant_scope_disabled

    now = now or datetime.now(timezone.utc)
    out: dict[str, list] = {"synced": [], "reconciled": [], "finalized": [], "errors": []}
    with tenant_scope_disabled():
        # Ended subscriptions whose final billing did not complete (a kept
        # draft, an item never moved): finalize is re-runnable and a no-op
        # once nothing remains.
        for og_id, sub_id in await _unfinished_finals(db):
            try:
                result = await finalize_agent_billing(db, og_id, sub_id)
                (out["errors"] if "error" in result else out["finalized"]).append(result)
            except Exception as e:  # noqa: BLE001 - one group never stops the run
                logger.exception("agent final billing retry failed for og %s", og_id)
                await db.rollback()
                out["errors"].append({"og": og_id, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        groups = (await db.execute(
            select(ReservationSubscription.ownership_group_id,
                   ReservationSubscription.stripe_subscription_id)
            .where(ReservationSubscription.stripe_subscription_id.is_not(None),
                   ReservationSubscription.status.not_in(TERMINAL_STATUSES))
            .order_by(ReservationSubscription.ownership_group_id)
        )).all()
        for og_id, sub_id in groups:
            try:
                await _lock_group(db, og_id)
                try:
                    row, duplicate = await _sync_from_stripe(db, og_id, sub_id)
                except SubscriptionMissing:
                    await db.rollback()
                    out["errors"].append({"og": og_id, "error": f"Stripe has no subscription {sub_id}"})
                    continue
                await db.commit()
                status = row.status if row else None
                out["synced"].append({"og": og_id, "status": status,
                                      **({"duplicate": duplicate} if duplicate else {})})
                if row is None:
                    continue
                if status == "canceled" and row.stripe_subscription_id == sub_id:
                    result = await finalize_agent_billing(db, og_id, sub_id)
                    (out["errors"] if "error" in result else out["finalized"]).append(result)
                    continue
                if status not in BILLABLE_STATUSES:
                    continue
                og = await db.get(OwnershipGroup, og_id)
                result = await reconcile_agent_overage(db, og, now)
                (out["errors"] if "error" in result else out["reconciled"]).append(result)
            except Exception as e:  # noqa: BLE001 - one group never stops the run
                logger.exception("agent billing daily run failed for og %s", og_id)
                await db.rollback()
                out["errors"].append({"og": og_id, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


async def handle_agent_event(db: AsyncSession, event_type: str, obj: Any) -> dict:
    """Apply one agent-subscription webhook event. Touches only the
    reservation_subscriptions row, its overage charges (and, for a
    duplicate, its refund record), never the scheduling fields. Every state
    change re-reads the subscription from Stripe; a Stripe read error
    propagates (5xx, Stripe redelivers)."""
    from backend.services.operator_alerts import send_agent_invoice_unpaid_alert

    sub_id = _subscription_id_of(event_type, obj)
    customer = _as_id(_field(obj, "customer"))
    og_id = await _group_for(db, sub_id, _object_metadata(event_type, obj), customer)
    if not og_id or not sub_id:
        return {"handled": False}

    if event_type == "invoice.upcoming":
        row = await _row_by_subscription(db, sub_id)
        og = await db.get(OwnershipGroup, og_id)
        if row is None or og is None:
            return {"handled": False, "og_id": og_id}
        return {"handled": True, "og_id": og_id,
                "overage": await reconcile_agent_overage(db, og)}

    og = await _lock_group(db, og_id)
    if event_type == "checkout.session.completed":
        try:
            _verify_session(obj, og)
        except SessionInvalid as e:
            logger.warning("agent checkout %s ignored: %s", _field(obj, "id"), e)
            await db.rollback()
            return {"handled": False}
    try:
        row, duplicate = await _sync_from_stripe(db, og_id, sub_id)
    except SubscriptionMissing:
        logger.warning("agent %s for subscription %s: Stripe has no such subscription",
                       event_type, sub_id)
        await db.rollback()
        return {"handled": False, "og_id": og_id}
    out: dict = {"handled": True, "og_id": og_id, "status": row.status if row else None}
    unpaid: list = []
    if event_type == "invoice.payment_succeeded":
        paid = await _mark_overage_paid(db, og_id, obj)
        if paid:
            out["overage_paid"] = paid
    elif event_type in UNPAID_OUTCOMES:
        unpaid = await _mark_overage_unpaid(db, og_id, obj, UNPAID_OUTCOMES[event_type])
        out["overage_failed"] = len(unpaid)
    await db.commit()
    if unpaid:
        send_agent_invoice_unpaid_alert(og_id, _as_id(_field(obj, "id")) or "?",
                                        UNPAID_OUTCOMES[event_type], float(_total(unpaid)),
                                        len(unpaid))
    if duplicate:
        out["duplicate"] = duplicate
    if (event_type == "customer.subscription.deleted" and row is not None
            and row.stripe_subscription_id == sub_id and row.status == "canceled"):
        out["final"] = await finalize_agent_billing(db, og_id, sub_id)
    return out


# ---------------------------------------------------------------------------
# Routes' services
# ---------------------------------------------------------------------------


def _require_configured() -> None:
    if not settings.RESERVATIONS_PRICE_ID or not settings.STRIPE_SECRET_KEY:
        raise NotConfigured("not_configured")


def _return_urls() -> tuple[str, str]:
    base = settings.FRONTEND_URL.rstrip("/") + BILLING_PAGE_PATH
    return (f"{base}?agent_checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            f"{base}?agent_checkout=cancel")


async def _clear_the_way(db: AsyncSession, og: OwnershipGroup) -> None:
    """Before a new agent Checkout (group locked): adopt a live agent
    subscription Stripe has but the row lacks (AlreadySubscribed), cancel
    leftover incomplete, unpaid or paused ones, and expire open agent sessions, so at most one session
    can be paid."""
    customer = og.stripe_customer_id
    client = _client()
    subs = client.v1.subscriptions.list({"customer": customer, "status": "all", "limit": 100})
    adopted = False
    for sub in subs.auto_paging_iter():
        if not _is_agent_metadata(_metadata(sub)):
            continue
        status = _field(sub, "status")
        if status in ANSWERING_STATUSES:
            try:
                row, _ = await _sync_from_stripe(db, og.id, _as_id(_field(sub, "id")))
            except SubscriptionMissing:
                continue  # gone since the list: nothing to clear
            adopted = adopted or row is not None
        elif status in LEFTOVER_STATUSES:
            client.v1.subscriptions.cancel(_as_id(_field(sub, "id")))
    if adopted:
        await db.commit()
        raise AlreadySubscribed("already_subscribed")
    sessions = client.v1.checkout.sessions.list(
        {"customer": customer, "status": "open", "limit": 100})
    for s in sessions.auto_paging_iter():
        if _is_agent_metadata(_metadata(s)):
            client.v1.checkout.sessions.expire(_as_id(_field(s, "id")))


async def create_checkout(db: AsyncSession, og: OwnershipGroup, email: str) -> str:
    """A Checkout session (mode=subscription) for the agent; returns its URL.

    Uses the group's Stripe customer, creating one first if the group has
    none, so the agent invoices sit beside the scheduling ones and confirm
    can bind the session to the group. Runs under the group lock."""
    _require_configured()
    og = await _lock_group(db, og.id)
    row = await _locked_row(db, og.id)
    if row is not None and row.status in ANSWERING_STATUSES:
        await db.rollback()
        raise AlreadySubscribed("already_subscribed")
    try:
        if not og.stripe_customer_id:
            customer = _client().v1.customers.create(
                {"email": email, "name": og.name, "metadata": {"og_id": og.id}},
                {"idempotency_key": f"og-customer-{og.id}"})
            og.stripe_customer_id = _as_id(customer)
            await db.flush()
        else:
            await _clear_the_way(db, og)
        md = {"product": PRODUCT, "og_id": og.id}
        success_url, cancel_url = _return_urls()
        session = _client().v1.checkout.sessions.create({
            "mode": "subscription",
            "customer": og.stripe_customer_id,
            "line_items": [{"price": settings.RESERVATIONS_PRICE_ID, "quantity": 1}],
            "subscription_data": {"metadata": md},
            # No client_reference_id: the scheduling confirm-upgrade binds a
            # session to the group by it. Confirm here binds by customer and
            # metadata (_verify_session).
            "metadata": md,
            "success_url": success_url,
            "cancel_url": cancel_url,
        })
    except stripe.StripeError as e:
        await db.commit()  # keep a customer created above; release the lock
        raise StripeFailure(str(e)) from e
    await db.commit()
    return _field(session, "url")


async def confirm_checkout(db: AsyncSession, og: OwnershipGroup, session_id: str) -> ReservationSubscription:
    """Verify the returning Checkout session and record the subscription.
    Idempotent with the checkout.session.completed webhook."""
    if not settings.STRIPE_SECRET_KEY:
        raise NotConfigured("not_configured")
    try:
        session = _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as e:
        raise SessionInvalid("invalid session") from e
    sub_id = _verify_session(session, og)
    og_id = og.id
    await _lock_group(db, og_id)
    try:
        row, duplicate = await _sync_from_stripe(db, og_id, sub_id)
    except SubscriptionMissing as e:
        await db.rollback()
        raise SessionInvalid("subscription not found") from e
    except stripe.StripeError as e:
        await db.rollback()
        raise StripeFailure(str(e)) from e
    await db.commit()
    if row is None:
        raise AlreadySubscribed("already_subscribed")
    return row


async def cancel_for_deleted_group(db: AsyncSession, og_id: str) -> bool:
    """Cancel the group's agent subscription in Stripe before its data is
    deleted (day 90). Best effort: logs and returns False on any failure,
    never raises, so it never blocks the deletion."""
    try:
        row = await for_group(db, og_id)
        if row is None or not row.stripe_subscription_id or row.status in TERMINAL_STATUSES:
            return False
        _client().v1.subscriptions.cancel(row.stripe_subscription_id)
        return True
    except Exception:
        logger.exception("agent subscription not cancelled for deleted og %s", og_id)
        return False


def _money(value: Decimal) -> float:
    return float(Decimal(value).quantize(_CENT))


async def status_summary(
    db: AsyncSession, og_id: str, company_id: str, now: datetime,
) -> dict:
    """Subscription state, this period's usage and each location's spend
    today against its cap (the company's active locations)."""
    row = await for_group(db, og_id)
    included = Decimal(str(settings.RESERVATIONS_INCLUDED_USAGE_USD))
    period_start = usage_period_start(row, now)
    billed = await period_usage(db, og_id, period_start)
    locations = (await db.execute(
        select(Location).where(Location.company_id == company_id, active(Location))
        .order_by(Location.name)
    )).scalars().all()
    per_location = []
    for loc in locations:
        rules, _ = await get_rules(db, company_id, loc.id)
        spent = await spend_today(db, loc.id, now, loc.timezone)
        cap = Decimal(str(rules.daily_spend_cap_usd))
        per_location.append({
            "location_id": loc.id, "name": loc.name,
            "spend_today_usd": _money(spent), "daily_cap_usd": _money(cap),
            "cap_reached": spent >= cap,
        })
    return {
        "configured": bool(settings.RESERVATIONS_PRICE_ID),
        "price_usd_monthly": _money(Decimal(str(settings.RESERVATIONS_PRICE_MONTHLY_USD))),
        "usage_markup": float(settings.RESERVATIONS_USAGE_MARKUP),
        "status": row.status if row else None,
        "answering": bool(row and row.status in ANSWERING_STATUSES),
        "current_period_start": row.current_period_start.isoformat() if row and row.current_period_start else None,
        "current_period_end": row.current_period_end.isoformat() if row and row.current_period_end else None,
        "cancel_at_period_end": bool(row and row.cancel_at_period_end),
        "usage": {
            "period_start": period_start.isoformat(),
            "billed_usd": _money(billed),
            "included_usd": _money(included),
            "overage_usd": _money(max(Decimal(0), billed - included)),
        },
        "locations": per_location,
    }
