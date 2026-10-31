"""Stripe webhook receiver.

This endpoint is intentionally unauthenticated — Stripe verifies the request
via the `Stripe-Signature` header instead. The signing secret comes from
settings.STRIPE_WEBHOOK_SECRET (set per-environment via Secrets Manager).
"""
import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db
from backend.models.billing_charge import BillingCharge
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import (
    bill_monthly_overages_for_og,
    cache_default_payment_method,
    send_subscription_ended_email,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("wizscheduler.stripe_webhook")


@router.post("/stripe", status_code=200)
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    sig_header = request.headers.get("stripe-signature")
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    event_type = event["type"] if isinstance(event, dict) else event.type
    obj = event["data"]["object"] if isinstance(event, dict) else event.data.object
    logger.info("Stripe webhook received: %s", event_type)

    def _customer_id() -> str | None:
        return obj.get("customer") if isinstance(obj, dict) else obj.customer

    async def _find_og(customer_id: str | None) -> OwnershipGroup | None:
        if not customer_id:
            return None
        return (await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_customer_id == customer_id)
        )).scalar_one_or_none()

    if event_type == "invoice.upcoming":
        og = await _find_og(_customer_id())
        if og:
            await bill_monthly_overages_for_og(db, og)
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "invoice.payment_failed":
        og = await _find_og(_customer_id())
        if og and og.autoreload_failed_at is None:
            og.autoreload_failed_at = datetime.now(timezone.utc)
            await db.commit()
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "payment_method.attached":
        og = await _find_og(_customer_id())
        if og:
            await cache_default_payment_method(db, og)
            await db.commit()
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "invoice.payment_succeeded":
        # Invoice finalized + paid. Walk its line items and flip the
        # matching pending BillingCharge rows (our InvoiceItem-backed
        # entries) to status=succeeded for audit accuracy.
        og = await _find_og(_customer_id())
        if not og:
            return {"received": True, "type": event_type, "og_id": None}

        if isinstance(obj, dict):
            lines = (obj.get("lines") or {}).get("data") or []
        else:
            lines = getattr(getattr(obj, "lines", None), "data", None) or []

        updated = 0
        for line in lines:
            line_md = line.get("metadata") if isinstance(line, dict) else (getattr(line, "metadata", None) or {})
            line_md = dict(line_md) if line_md else {}
            if line_md.get("kind") not in ("invoice_item_storage", "invoice_item_employees"):
                continue
            ii_id = line.get("invoice_item") if isinstance(line, dict) else getattr(line, "invoice_item", None)
            if not ii_id:
                continue
            bc = (await db.execute(
                select(BillingCharge).where(
                    BillingCharge.ownership_group_id == og.id,
                    BillingCharge.stripe_object_id == ii_id,
                    BillingCharge.status == "pending",
                )
            )).scalar_one_or_none()
            if bc:
                bc.status = "succeeded"
                updated += 1

        if updated:
            await db.commit()
        return {"received": True, "type": event_type, "og_id": og.id, "updated": updated}

    if event_type == "customer.subscription.updated":
        og = await _find_og(_customer_id())
        cape = obj.get("cancel_at_period_end") if isinstance(obj, dict) else getattr(obj, "cancel_at_period_end", None)
        logger.info(
            "Subscription updated: og=%s cancel_at_period_end=%s",
            og.id if og else None,
            cape,
        )
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "customer.subscription.deleted":
        og = await _find_og(_customer_id())
        if og and og.canceled_at is None:
            og.canceled_at = datetime.now(timezone.utc)
            og.notified_subscription_ended_at = og.canceled_at
            await db.commit()
            await send_subscription_ended_email(og)
        return {"received": True, "type": event_type, "og_id": og.id if og else None}

    if event_type == "checkout.session.completed":
        # Only the paid payroll archive (#218) is handled here. Every other
        # Checkout session (signup, upgrade, reactivation) is confirmed by
        # its own return-URL endpoint and stays untouched.
        from backend.services.payroll_archive import (
            CHECKOUT_KIND,
            ArchiveSessionInvalid,
            _as_id,
            stripe_field,
            record_archive_purchase,
            verify_archive_session,
        )

        metadata = stripe_field(obj, "metadata") or {}
        if stripe_field(metadata, "kind") != CHECKOUT_KIND:
            return {"received": True, "type": event_type}
        og_id = stripe_field(metadata, "ownership_group_id") or stripe_field(obj, "client_reference_id")
        if not og_id or stripe_field(obj, "payment_status") != "paid":
            logger.warning(
                "payroll_archive webhook ignored: session=%s og=%s payment_status=%s",
                stripe_field(obj, "id"), og_id, stripe_field(obj, "payment_status"),
            )
            return {"received": True, "type": event_type, "og_id": og_id}
        og = await db.get(OwnershipGroup, og_id)
        if og is not None:
            try:
                verify_archive_session(obj, og)
            except ArchiveSessionInvalid as e:
                # Paid, tagged as ours, but not verifiable against the group
                # (say a customer mismatch). Money was taken; a person must
                # look. Not refunded automatically: it may be genuine.
                from backend.services.operator_alerts import send_payroll_archive_review_alert

                logger.warning(
                    "payroll_archive webhook rejected: session=%s og=%s reason=%s",
                    stripe_field(obj, "id"), og_id, e,
                )
                send_payroll_archive_review_alert(
                    og_id, stripe_field(obj, "id") or "?",
                    _as_id(stripe_field(obj, "payment_intent")), str(e),
                )
                return {"received": True, "type": event_type, "og_id": og_id}
        # A group already deleted is refunded inside record_archive_purchase.
        outcome = await record_archive_purchase(db, og_id, obj)
        return {"received": True, "type": event_type, "og_id": og_id, "payroll_archive": outcome}

    return {"received": True, "type": event_type}
