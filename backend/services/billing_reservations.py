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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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
    if event_type == "checkout.session.completed":
        if _field(obj, "mode") != "subscription":
            return False
        if _is_agent_metadata(_metadata(obj)):
            return True
    elif event_type.startswith("customer.subscription."):
        if _is_agent_metadata(_metadata(obj)):
            return True
    elif _is_agent_metadata(_invoice_subscription_metadata(obj)):
        return True
    return await _row_by_subscription(db, _subscription_id_of(event_type, obj)) is not None


async def _lock_group(db: AsyncSession, og_id: str) -> OwnershipGroup | None:
    """The group, row-locked: serializes the confirm route and the webhook
    writing the same group's subscription row."""
    return (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
    )).scalar_one_or_none()


async def _apply(
    db: AsyncSession, og_id: str, sub_id: str, *,
    status: str | None,
    period: tuple[datetime | None, datetime | None] | None = None,
    cancel_at_period_end: bool | None = None,
) -> ReservationSubscription | None:
    """Upsert the group's row for subscription `sub_id` (not committed).

    A row for another subscription is replaced only once that one is no
    longer live (answering); a live one is kept and the event ignored, so a
    late event for an old or duplicate subscription cannot clobber the
    current one. `status=None` leaves the status (a new row starts
    `incomplete`); a status outside STATUSES (say `paused`) is logged and
    left."""
    if status is not None and status not in STATUSES:
        logger.warning("agent subscription %s: unknown status %r left unchanged", sub_id, status)
        status = None
    row = await for_group(db, og_id)
    if row is None:
        row = ReservationSubscription(
            ownership_group_id=og_id, stripe_subscription_id=sub_id,
            status=status or "incomplete", cancel_at_period_end=False)
        db.add(row)
    elif row.stripe_subscription_id != sub_id:
        if row.stripe_subscription_id and row.status in ANSWERING_STATUSES:
            logger.warning(
                "agent subscription %s for og %s ignored: %s is still %s",
                sub_id, og_id, row.stripe_subscription_id, row.status)
            return None
        row.stripe_subscription_id = sub_id
        row.status = status or "incomplete"
        row.current_period_start = row.current_period_end = None
        row.cancel_at_period_end = False
    elif status is not None:
        row.status = status
    if period is not None:
        start, end = period
        if start is not None:
            row.current_period_start = start
        if end is not None:
            row.current_period_end = end
    if cancel_at_period_end is not None:
        row.cancel_at_period_end = bool(cancel_at_period_end)
    await db.flush()
    return row


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


def _retrieve_subscription(sub_id: str) -> Any:
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe.Subscription.retrieve(sub_id)


async def _sync_from_stripe(db: AsyncSession, og_id: str, sub_id: str) -> ReservationSubscription | None:
    """Upsert the row from the subscription as Stripe has it now. If Stripe
    cannot be reached, the row is recorded with its id only and the
    subscription events bring the rest."""
    try:
        sub = _retrieve_subscription(sub_id)
    except stripe.StripeError as e:
        logger.warning("agent subscription %s not retrieved: %s", sub_id, e)
        return await _apply(db, og_id, sub_id, status=None)
    return await _apply(db, og_id, sub_id, status=_field(sub, "status"), period=_period(sub),
                        cancel_at_period_end=_field(sub, "cancel_at_period_end"))


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
    reservation_subscriptions row, never the scheduling fields."""
    sub_id = _subscription_id_of(event_type, obj)
    customer = _as_id(_field(obj, "customer"))

    if event_type == "checkout.session.completed":
        og_id = await _group_for(db, sub_id, _metadata(obj), customer)
        if not og_id or not sub_id:
            return {"handled": False}
        og = await _lock_group(db, og_id)
        try:
            _verify_session(obj, og)
        except SessionInvalid as e:
            logger.warning("agent checkout %s ignored: %s", _field(obj, "id"), e)
            await db.rollback()
            return {"handled": False}
        row = await _sync_from_stripe(db, og_id, sub_id)
        await db.commit()
        return {"handled": True, "og_id": og_id, "status": row.status if row else None}

    if event_type.startswith("customer.subscription."):
        og_id = await _group_for(db, sub_id, _metadata(obj), customer)
        if not og_id or not sub_id:
            return {"handled": False}
        await _lock_group(db, og_id)
        if event_type == "customer.subscription.deleted":
            row = await for_group(db, og_id)
            # A deletion for an older subscription (the group has since
            # resubscribed) must not cancel the current one.
            if row is None or row.stripe_subscription_id not in (None, sub_id):
                await db.rollback()
                return {"handled": False, "og_id": og_id}
            row = await _apply(db, og_id, sub_id, status="canceled", period=_period(obj),
                               cancel_at_period_end=False)
        else:
            row = await _apply(db, og_id, sub_id, status=_field(obj, "status"),
                               period=_period(obj),
                               cancel_at_period_end=_field(obj, "cancel_at_period_end"))
        await db.commit()
        return {"handled": True, "og_id": og_id, "status": row.status if row else None}

    # invoice.*: only for a subscription we already hold a row for.
    row = await _row_by_subscription(db, sub_id)
    if row is None:
        return {"handled": False}
    og_id = row.ownership_group_id
    if event_type == "invoice.upcoming":
        return {"handled": True, "og_id": og_id,
                "overage": await reconcile_agent_overage(db, row)}
    await _lock_group(db, og_id)
    await db.refresh(row)
    if event_type == "invoice.payment_failed":
        # Stripe retries the card; the agent keeps answering while past_due
        # (Ruling T). An incomplete first payment stays incomplete: it never
        # answered, and past_due would let it.
        if row.status in ("active", "trialing"):
            row.status = "past_due"
    else:  # invoice.payment_succeeded
        await _sync_from_stripe(db, og_id, sub_id)
    await db.commit()
    return {"handled": True, "og_id": og_id, "status": row.status}


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


async def create_checkout(db: AsyncSession, og: OwnershipGroup, email: str) -> str:
    """A Checkout session (mode=subscription) for the agent; returns its URL.

    Uses the group's Stripe customer, creating one first if the group has
    none, so the agent invoices sit beside the scheduling ones and confirm
    can bind the session to the group."""
    _require_configured()
    row = await for_group(db, og.id)
    if row is not None and row.status in ANSWERING_STATUSES:
        raise AlreadySubscribed("already_subscribed")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        if not og.stripe_customer_id:
            customer = stripe.Customer.create(
                email=email, name=og.name, metadata={"og_id": og.id},
                idempotency_key=f"og-customer-{og.id}")
            og.stripe_customer_id = _as_id(customer)
            await db.commit()
        md = {"product": PRODUCT, "og_id": og.id}
        success_url, cancel_url = _return_urls()
        session = stripe.checkout.Session.create(
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
        raise StripeFailure(str(e)) from e
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
    row = await _sync_from_stripe(db, og_id, sub_id)
    if row is None:
        await db.rollback()
        raise AlreadySubscribed("already_subscribed")
    await db.commit()
    return row


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
