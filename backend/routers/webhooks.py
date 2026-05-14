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

    return {"received": True, "type": event_type}
