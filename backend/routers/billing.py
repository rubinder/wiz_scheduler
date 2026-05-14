import stripe
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import User
from backend.models.billing_charge import BillingCharge
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import (
    AutoReloadBlocked,
    AutoReloadError,
    auto_reload_if_needed,
    get_full_billing_summary,
    get_ownership_group_id,
    record_storage_snapshots,
)

router = APIRouter(prefix="/billing", tags=["billing"])


class CreateCheckoutSessionRequest(BaseModel):
    email: str


class CreateCheckoutSessionResponse(BaseModel):
    session_id: str
    url: str


@router.post("/create-checkout-session", response_model=CreateCheckoutSessionResponse)
async def create_checkout_session(body: CreateCheckoutSessionRequest) -> CreateCheckoutSessionResponse:
    """Create a Stripe Checkout session for new account registration.

    This endpoint is unauthenticated — it's called before the user has an account.
    """
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            customer_email=body.email,
            line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
            success_url=settings.STRIPE_SUCCESS_URL,
            cancel_url=settings.STRIPE_CANCEL_URL,
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return CreateCheckoutSessionResponse(session_id=session.id, url=session.url)


@router.get("/usage")
async def get_usage(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full billing summary for the current ownership group.

    Includes LLM/storage/employee usage, pending invoice items, and the
    cancellation lifecycle state (is_read_only, canceled_at, scheduled_deletion_at).
    """
    og_id = await get_ownership_group_id(db, current_user.company_id)
    if not og_id:
        return {
            "base": {"monthly_usd": settings.BASE_MONTHLY_USD},
            "llm": {"input_tokens": 0, "output_tokens": 0, "raw_cost_usd": 0, "charged_usd": 0,
                     "free_tier_usd": settings.LLM_FREE_TIER_USD, "free_remaining_usd": settings.LLM_FREE_TIER_USD,
                     "is_over_free_tier": False, "overage_markup": settings.LLM_OVERAGE_MARKUP},
            "storage": {"used_gb": 0, "free_gb": settings.STORAGE_FREE_GB, "billable_gb": 0,
                        "cost_per_gb": settings.STORAGE_COST_PER_GB, "charged_usd": 0},
            "employees": {"count": 0, "free_tier": settings.EMPLOYEE_FREE_TIER, "billable": 0,
                          "block_size": settings.EMPLOYEE_BLOCK_SIZE, "cost_per_block": settings.EMPLOYEE_COST_PER_BLOCK, "charged_usd": 0},
            "schedules": {"count": 0, "free_tier": settings.SCHEDULE_FREE_TIER, "billable": 0,
                          "block_size": settings.SCHEDULE_BLOCK_SIZE, "cost_per_block": settings.SCHEDULE_COST_PER_BLOCK, "charged_usd": 0},
            "total_monthly_charge_usd": settings.BASE_MONTHLY_USD,
            "pending_invoice_items": [],
            "is_read_only": False,
            "canceled_at": None,
            "scheduled_deletion_at": None,
        }

    summary = await get_full_billing_summary(db, og_id)
    rows = (await db.execute(
        select(BillingCharge)
        .where(
            BillingCharge.ownership_group_id == og_id,
            BillingCharge.kind.in_(("invoice_item_storage", "invoice_item_employees")),
            BillingCharge.status == "pending",
        )
        .order_by(BillingCharge.created_at.desc())
    )).scalars()
    summary["pending_invoice_items"] = [
        {"kind": r.kind, "amount_usd": float(r.amount_usd), "period": r.period}
        for r in rows
    ]

    og = await db.get(OwnershipGroup, og_id)
    if og and og.canceled_at is not None:
        summary["is_read_only"] = True
        summary["canceled_at"] = og.canceled_at.isoformat()
        summary["scheduled_deletion_at"] = (
            og.canceled_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)
        ).isoformat()
    else:
        summary["is_read_only"] = False
        summary["canceled_at"] = None
        summary["scheduled_deletion_at"] = None

    return summary


@router.post("/storage-snapshots")
async def trigger_storage_snapshots(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record today's storage usage for all ownership groups.

    Idempotent — groups that already have a snapshot today are skipped.
    Also runs automatically once per day via a background task.
    """
    return await record_storage_snapshots(db)


# ---------------------------------------------------------------------------
# Auto-reload endpoints (Tasks 9–12)
# ---------------------------------------------------------------------------


class AutoReloadStatus(BaseModel):
    enabled: bool
    threshold_usd: float
    amount_usd: float
    current_balance_usd: float
    failed_at: str | None


class AutoReloadUpdate(BaseModel):
    enabled: bool | None = None
    threshold_usd: float | None = Field(default=None, ge=0.5)
    amount_usd: float | None = Field(default=None, ge=0.5)


class BillingChargeRow(BaseModel):
    id: str
    kind: str
    amount_usd: float
    stripe_object_id: str | None
    period: str | None
    status: str
    error_message: str | None
    created_at: str


class BillingChargesResponse(BaseModel):
    charges: list[BillingChargeRow]


class PortalLinkResponse(BaseModel):
    url: str


async def _load_og(db: AsyncSession, current_user: User) -> OwnershipGroup:
    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=404, detail="No ownership group found")
    result = await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))
    og = result.scalar_one_or_none()
    if not og:
        raise HTTPException(status_code=404, detail="Ownership group not found")
    return og


def _autoreload_status(og: OwnershipGroup) -> AutoReloadStatus:
    return AutoReloadStatus(
        enabled=og.autoreload_enabled,
        threshold_usd=float(og.autoreload_threshold_usd),
        amount_usd=float(og.autoreload_amount_usd),
        current_balance_usd=float(og.ai_credits_usd),
        failed_at=og.autoreload_failed_at.isoformat() if og.autoreload_failed_at else None,
    )


@router.get("/autoreload", response_model=AutoReloadStatus)
async def get_autoreload(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    return _autoreload_status(og)


@router.put("/autoreload", response_model=AutoReloadStatus)
async def update_autoreload(
    body: AutoReloadUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    if body.enabled is not None:
        og.autoreload_enabled = body.enabled
    if body.threshold_usd is not None:
        og.autoreload_threshold_usd = body.threshold_usd
    if body.amount_usd is not None:
        og.autoreload_amount_usd = body.amount_usd
    await db.commit()
    return _autoreload_status(og)


@router.post("/autoreload/retry", response_model=AutoReloadStatus)
async def retry_autoreload(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> AutoReloadStatus:
    og = await _load_og(db, current_user)
    if og.autoreload_failed_at is None:
        raise HTTPException(status_code=400, detail="Auto-reload is not in a failed state")

    og.autoreload_failed_at = None
    await db.flush()

    try:
        await auto_reload_if_needed(db, og, cost_usd=float(og.autoreload_threshold_usd))
    except AutoReloadError as e:
        await db.commit()
        raise HTTPException(status_code=402, detail=f"Retry failed: {e}")
    except AutoReloadBlocked:
        raise HTTPException(status_code=409, detail="Billing on hold")

    await db.commit()
    return _autoreload_status(og)


@router.get("/charges", response_model=BillingChargesResponse)
async def get_billing_charges(
    limit: int = 50,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> BillingChargesResponse:
    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        return BillingChargesResponse(charges=[])

    result = await db.execute(
        select(BillingCharge)
        .where(BillingCharge.ownership_group_id == og_id)
        .order_by(BillingCharge.created_at.desc())
        .limit(min(limit, 200))
    )
    rows = list(result.scalars())
    return BillingChargesResponse(charges=[
        BillingChargeRow(
            id=r.id,
            kind=r.kind,
            amount_usd=float(r.amount_usd),
            stripe_object_id=r.stripe_object_id,
            period=r.period,
            status=r.status,
            error_message=r.error_message,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ])


@router.get("/portal-link", response_model=PortalLinkResponse)
async def get_portal_link(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PortalLinkResponse:
    og = await _load_og(db, current_user)
    if not og.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer associated with this account")
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.billing_portal.Session.create(
            customer=og.stripe_customer_id,
            return_url=settings.STRIPE_BILLING_PORTAL_RETURN_URL,
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return PortalLinkResponse(url=session.url)


# ---------------------------------------------------------------------------
# Reactivation endpoints
# ---------------------------------------------------------------------------


class ReactivateCheckoutResponse(BaseModel):
    session_id: str
    url: str


class ConfirmReactivationRequest(BaseModel):
    session_id: str


@router.post("/reactivate-checkout", response_model=ReactivateCheckoutResponse)
async def reactivate_checkout(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ReactivateCheckoutResponse:
    """Create a Stripe Checkout session for reactivating a canceled subscription.

    Reuses the existing stripe_customer_id so saved payment methods + invoice
    history persist across the cancel/reactivate cycle. The customer completes
    Checkout and is redirected back; the frontend then calls
    /billing/confirm-reactivation with the session_id.
    """
    og = await _load_og(db, current_user)
    if og.canceled_at is None:
        raise HTTPException(status_code=400, detail="Subscription is not canceled")
    if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    success_url = settings.STRIPE_SUCCESS_URL.replace(
        "/register", "/manager/dashboard"
    ).replace("session_id=", "reactivate_session_id=")
    cancel_url = settings.STRIPE_CANCEL_URL.replace("/register", "/manager/dashboard")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            customer=og.stripe_customer_id,
            line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return ReactivateCheckoutResponse(session_id=session.id, url=session.url)


@router.post("/confirm-reactivation")
async def confirm_reactivation(
    body: ConfirmReactivationRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify the reactivation Checkout session and clear cancellation state."""
    og = await _load_og(db, current_user)
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        session = stripe.checkout.Session.retrieve(body.session_id)
    except stripe.StripeError:
        raise HTTPException(status_code=400, detail="Invalid session")

    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed")

    og.stripe_customer_id = session.customer
    og.stripe_subscription_id = session.subscription
    og.canceled_at = None
    og.notified_subscription_ended_at = None
    og.notified_deletion_reminder_at = None
    og.notified_data_deleted_at = None
    await db.commit()

    return {"reactivated": True, "subscription_id": og.stripe_subscription_id}
