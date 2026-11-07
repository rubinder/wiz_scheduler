"""Keep the app's view of each subscription in step with Stripe.

Webhooks are the primary signal: customer.subscription.deleted records the
cancellation the moment Stripe ends a subscription. Webhooks can be missed,
though. From 2026-09-28 to 2026-11-07 every delivery went to the marketing
site (the endpoint still pointed at wizscheduler.com), and the endpoint was
never subscribed to customer.subscription.deleted at all, so a subscription
Stripe cancelled on 2026-10-04 after failed payments stayed "paid" here.

This module is the daily safety net (backend/scripts/run_stripe_reconcile.py,
terraform/stripe_reconcile_schedule.tf):

- every ownership group the app treats as paid is checked against Stripe,
  and one Stripe has ended is cancelled through record_subscription_ended,
  the same path the webhook takes;
- a subscription that is past due or unpaid is reported to the operator,
  never acted on: Stripe is still retrying and decides when it ends;
- the webhook endpoint itself is checked (URL, enabled, required events),
  so a drift like the one above is reported the next day.

Stripe stays authoritative. Nothing here calls Stripe on a request path.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import send_subscription_ended_email
from backend.services.operator_alerts import _send_operator_email
from backend.tenant import tenant_scope_disabled

logger = logging.getLogger("wizscheduler.stripe_reconcile")

# Every event backend/routers/webhooks.py handles.
REQUIRED_WEBHOOK_EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.deleted",
    "customer.subscription.updated",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
    "invoice.upcoming",
    "payment_method.attached",
})
# Stripe will never bill these again.
ENDED_STATUSES = frozenset({"canceled", "incomplete_expired"})
# Stripe is still retrying; the subscription may yet recover or end.
OVERDUE_STATUSES = frozenset({"past_due", "unpaid"})


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        return obj[name]
    except (KeyError, TypeError):
        return getattr(obj, name, None)


def _when(ts: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc) if ts else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def subscription_ended_at(subscription: Any) -> datetime | None:
    """When Stripe ended the subscription, from ended_at, else canceled_at."""
    return _when(_field(subscription, "ended_at")) or _when(_field(subscription, "canceled_at"))


def webhook_url() -> str:
    return settings.FRONTEND_URL.rstrip("/") + "/api/v1/webhooks/stripe"


async def record_subscription_ended(
    db: AsyncSession, og: OwnershipGroup, ended_at: datetime | None = None
) -> bool:
    """Mark the group's subscription cancelled and send the ended email, once.

    canceled_at is when Stripe ended the subscription (the grace period and
    the day-90 deletion count from it), or now when Stripe gave no time.
    Returns False when the group was already cancelled.
    """
    if og.canceled_at is not None:
        return False
    og.canceled_at = ended_at or datetime.now(timezone.utc)
    og.notified_subscription_ended_at = og.canceled_at
    await db.commit()
    await send_subscription_ended_email(og)
    return True


async def reconcile_subscriptions(db: AsyncSession) -> dict[str, list]:
    """Check every paid group against Stripe. Never raises for one group."""
    summary: dict[str, list] = {"checked": [], "ended": [], "overdue": [], "errors": []}
    with tenant_scope_disabled():
        groups = (await db.execute(
            select(OwnershipGroup).where(
                OwnershipGroup.stripe_subscription_id.is_not(None),
                OwnershipGroup.canceled_at.is_(None),
            ).order_by(OwnershipGroup.id)
        )).scalars().all()
        for og in groups:
            summary["checked"].append(og.id)
            try:
                sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
            except stripe.StripeError as e:
                # A missing subscription is not proof it ended (test data, a
                # wrong key): report it, never cancel on it.
                summary["errors"].append({"og": og.id, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                continue
            status = _field(sub, "status")
            if status in ENDED_STATUSES:
                if await record_subscription_ended(db, og, subscription_ended_at(sub)):
                    summary["ended"].append({"og": og.id, "status": status,
                                             "canceled_at": og.canceled_at.isoformat()})
            elif status in OVERDUE_STATUSES:
                summary["overdue"].append({"og": og.id, "status": status})
    return summary


def check_webhook_endpoint() -> list[str]:
    """Problems with the Stripe webhook endpoint; empty when it is healthy."""
    want = webhook_url()
    try:
        endpoints = [e for e in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter()]
    except stripe.StripeError as e:
        return [f"could not list webhook endpoints: {type(e).__name__}: {str(e)[:200]}"]
    matching = [e for e in endpoints if _field(e, "url") == want]
    if not matching:
        urls = ", ".join(sorted(str(_field(e, "url")) for e in endpoints)) or "none"
        return [f"no webhook endpoint for {want} (Stripe has: {urls})"]
    problems: list[str] = []
    for e in matching:
        if _field(e, "status") != "enabled":
            problems.append(f"webhook endpoint {_field(e, 'id')} is {_field(e, 'status')}")
        events = set(_field(e, "enabled_events") or [])
        missing = sorted(REQUIRED_WEBHOOK_EVENTS - events) if "*" not in events else []
        if missing:
            problems.append(f"webhook endpoint {_field(e, 'id')} is not subscribed to {', '.join(missing)}")
    return problems


def send_reconcile_alert(summary: dict[str, list], endpoint_problems: list[str]) -> bool:
    """One operator email when anything needs a person. Always logs. Never raises."""
    lines: list[str] = []
    for item in summary.get("ended", []):
        lines.append(f"Recorded a cancellation the webhook missed: og={item['og']} "
                     f"(Stripe status {item['status']}, canceled_at {item['canceled_at']}).")
    for item in summary.get("overdue", []):
        lines.append(f"Subscription {item['status']}: og={item['og']}. Stripe is retrying; "
                     f"the customer may need to update their card.")
    for item in summary.get("errors", []):
        lines.append(f"Could not check og={item['og']}: {item['error']}")
    lines += endpoint_problems
    if not lines:
        return False
    for line in lines:
        logger.warning("[OPERATOR] stripe_reconcile %s", line)
    body = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return _send_operator_email(
        "[WizScheduler] Stripe reconciliation needs a look",
        f"<p>The daily Stripe check found:</p><ul>{body}</ul>",
        "stripe_reconcile",
    )


async def run_stripe_reconcile(db: AsyncSession) -> dict[str, Any]:
    stripe.api_key = settings.STRIPE_SECRET_KEY
    summary: dict[str, Any] = await reconcile_subscriptions(db)
    summary["endpoint_problems"] = check_webhook_endpoint()
    summary["alerted"] = send_reconcile_alert(summary, summary["endpoint_problems"])
    return summary
