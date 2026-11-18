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
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterator

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.location import Location
from backend.models.ownership_group import OwnershipGroup
from backend.reservations.rules import get_rules
from backend.reservations.subscription import (
    ANSWERING_STATUSES,
    PRODUCT,
    STATUSES,
    ReservationSubscription,
    for_group,
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
        return _invoice_subscription_id(obj)
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


# Stripe calls made while the group row is locked: a short timeout and one
# network retry, so a slow Stripe cannot hold the lock for the SDK's
# default 80 s x 3. The SDK reads stripe.default_http_client per request;
# it is swapped only for one synchronous call (no await inside), so no
# other coroutine on the loop sees it.
LOCKED_STRIPE_TIMEOUT_S = 15
_locked_http_client: Any = None


@contextmanager
def _short_stripe() -> Iterator[None]:
    global _locked_http_client
    if _locked_http_client is None:
        try:
            _locked_http_client = stripe.new_default_http_client(timeout=LOCKED_STRIPE_TIMEOUT_S)
        except TypeError:  # a client without a timeout option
            _locked_http_client = False
    previous = stripe.default_http_client
    if _locked_http_client:
        stripe.default_http_client = _locked_http_client
    try:
        yield
    finally:
        stripe.default_http_client = previous


def _locked_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """One Stripe call under the group lock (short timeout, one retry)."""
    stripe.api_key = settings.STRIPE_SECRET_KEY
    with _short_stripe():
        return fn(*args, max_network_retries=1, **kwargs)


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
        return _locked_call(stripe.Subscription.retrieve, sub_id)
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
    if duplicate or await _duplicate_record(db, og_id, sub_id) is not None:
        return None, await _resolve_duplicate(db, og_id, sub)
    return row, None


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


async def _resolve_duplicate(db: AsyncSession, og_id: str, sub: Any) -> dict:
    """Backstop for a second live agent subscription on one group: cancel it
    in Stripe, refund its latest paid invoice, keep one
    agent_duplicate_refund BillingCharge per subscription with the outcome,
    and alert the operator with what actually happened.

    A `succeeded` record (cancelled, and refunded or nothing paid) makes a
    replay a no-op. A `failed` one is retried on every later event: the
    cancel if Stripe still has the subscription live, the refund (Stripe
    idempotency key agent-dup-refund-<sub>, so it refunds once) if it was
    not done. The operator is alerted the first time and whenever the
    outcome changes. Never raises on a Stripe error."""
    from backend.models.billing_charge import BillingCharge
    from backend.services.billing import refund_payment_intent
    from backend.services.operator_alerts import send_agent_duplicate_subscription_alert

    sub_id = _as_id(_field(sub, "id"))
    record = await _duplicate_record(db, og_id, sub_id)
    if record is not None and record.status == "succeeded":
        return {"duplicate": sub_id, "outcome": "already_handled"}

    errors: list[str] = []
    cancelled = _field(sub, "status") in TERMINAL_STATUSES
    if not cancelled:
        try:
            _locked_call(stripe.Subscription.cancel, sub_id)
            cancelled = True
        except stripe.StripeError as e:
            errors.append(f"cancel failed: {e}")
            logger.warning("agent duplicate %s not cancelled: %s", sub_id, e)

    # refund: "refunded", "none" (nothing was paid), "failed" (Stripe
    # refused) or "unknown" (the invoice could not be read).
    amount, pi, refund = Decimal(0), None, "none"
    invoice_id = _as_id(_field(sub, "latest_invoice"))
    if invoice_id:
        try:
            invoice = _locked_call(stripe.Invoice.retrieve, invoice_id, expand=["payments"])
        except stripe.StripeError as e:
            refund = "unknown"
            errors.append(f"invoice {invoice_id} not read: {e}")
            logger.warning("agent duplicate %s: invoice %s not read: %s", sub_id, invoice_id, e)
        else:
            amount = Decimal(int(_field(invoice, "amount_paid") or 0)) / 100
            pi = _invoice_payment_intent(invoice)
            if amount > 0:
                with _short_stripe():
                    ok = refund_payment_intent(pi, f"agent-dup-refund-{sub_id}")
                refund = "refunded" if ok else "failed"
                if not ok:
                    errors.append(f"refund of payment {pi or '?'} failed")

    done = cancelled and refund in ("refunded", "none")
    status = "succeeded" if done else "failed"
    message = "; ".join(errors)[:1000] or None
    changed = record is None or (record.status, record.error_message) != (status, message)
    if record is None:
        record = BillingCharge(ownership_group_id=og_id, kind=DUPLICATE_REFUND_KIND,
                               stripe_object_id=sub_id)
        db.add(record)
    record.amount_usd = amount
    record.status = status
    record.error_message = message
    await db.flush()
    if changed:
        row = await _locked_row(db, og_id)
        send_agent_duplicate_subscription_alert(
            og_id, sub_id, row.stripe_subscription_id if row else None, pi,
            float(amount), cancelled=cancelled, refund=refund, errors=errors)
    return {"duplicate": sub_id, "cancelled": cancelled, "refund": refund,
            "outcome": "handled" if done else "needs_attention"}


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


async def reconcile_agent_overage(db: AsyncSession, row: ReservationSubscription) -> dict:
    """Bill the period's usage over the included amount as an InvoiceItem on
    the agent subscription's upcoming invoice. Task 3 implements it."""
    del db, row
    return {"skipped": "not_implemented"}


async def handle_agent_event(db: AsyncSession, event_type: str, obj: Any) -> dict:
    """Apply one agent-subscription webhook event. Touches only the
    reservation_subscriptions row (and, for a duplicate, its refund record),
    never the scheduling fields. Every state change re-reads the
    subscription from Stripe; a Stripe read error propagates (5xx, Stripe
    redelivers)."""
    sub_id = _subscription_id_of(event_type, obj)
    customer = _as_id(_field(obj, "customer"))
    og_id = await _group_for(db, sub_id, _object_metadata(event_type, obj), customer)
    if not og_id or not sub_id:
        return {"handled": False}

    if event_type == "invoice.upcoming":
        row = await _row_by_subscription(db, sub_id)
        if row is None:
            return {"handled": False, "og_id": og_id}
        return {"handled": True, "og_id": og_id,
                "overage": await reconcile_agent_overage(db, row)}

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
    await db.commit()
    out: dict = {"handled": True, "og_id": og_id, "status": row.status if row else None}
    if duplicate:
        out["duplicate"] = duplicate
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
    subs = _locked_call(stripe.Subscription.list, customer=customer, status="all", limit=100)
    adopted = False
    for sub in subs.auto_paging_iter():
        if not _is_agent_metadata(_metadata(sub)):
            continue
        status = _field(sub, "status")
        if status in ANSWERING_STATUSES:
            row, _ = await _sync_from_stripe(db, og.id, _as_id(_field(sub, "id")))
            adopted = adopted or row is not None
        elif status in LEFTOVER_STATUSES:
            _locked_call(stripe.Subscription.cancel, _as_id(_field(sub, "id")))
    if adopted:
        await db.commit()
        raise AlreadySubscribed("already_subscribed")
    sessions = _locked_call(stripe.checkout.Session.list, customer=customer,
                            status="open", limit=100)
    for s in sessions.auto_paging_iter():
        if _is_agent_metadata(_metadata(s)):
            _locked_call(stripe.checkout.Session.expire, _as_id(_field(s, "id")))


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
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        if not og.stripe_customer_id:
            customer = _locked_call(
                stripe.Customer.create, email=email, name=og.name, metadata={"og_id": og.id},
                idempotency_key=f"og-customer-{og.id}")
            og.stripe_customer_id = _as_id(customer)
            await db.flush()
        else:
            await _clear_the_way(db, og)
        md = {"product": PRODUCT, "og_id": og.id}
        success_url, cancel_url = _return_urls()
        session = _locked_call(
            stripe.checkout.Session.create,
            mode="subscription",
            customer=og.stripe_customer_id,
            line_items=[{"price": settings.RESERVATIONS_PRICE_ID, "quantity": 1}],
            subscription_data={"metadata": md},
            metadata=md,
            client_reference_id=og.id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
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
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.retrieve(session_id)
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
        _locked_call(stripe.Subscription.cancel, row.stripe_subscription_id)
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
