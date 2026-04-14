import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import User
from backend.services.billing import get_full_billing_summary, get_ownership_group_id, record_storage_snapshots

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


class PurchaseCreditsRequest(BaseModel):
    amount_usd: float  # Dollar amount of AI credits to purchase


class PurchaseCreditsResponse(BaseModel):
    session_id: str
    url: str


@router.post("/purchase-credits", response_model=PurchaseCreditsResponse)
async def purchase_credits(
    body: PurchaseCreditsRequest,
    current_user: User = Depends(require_manager),
) -> PurchaseCreditsResponse:
    """Create a Stripe Checkout session to purchase AI credits."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    if body.amount_usd < 1.0:
        raise HTTPException(status_code=400, detail="Minimum purchase is $1.00")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=current_user.email,
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": int(body.amount_usd * 100),  # cents
                    "product_data": {
                        "name": f"AI Credits – ${body.amount_usd:.2f}",
                        "description": "AI scheduling credits for WizScheduler",
                    },
                },
                "quantity": 1,
            }],
            metadata={
                "type": "ai_credits",
                "amount_usd": str(body.amount_usd),
                "company_id": str(current_user.company_id),
            },
            success_url=settings.STRIPE_SUCCESS_URL.replace(
                "/register", "/manager/schedule"
            ).replace("session_id=", "credits_session_id="),
            cancel_url=settings.STRIPE_CANCEL_URL.replace("/register", "/manager/schedule"),
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return PurchaseCreditsResponse(session_id=session.id, url=session.url)


@router.post("/confirm-credits")
async def confirm_credits(
    body: dict,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify a completed Stripe credit purchase and add credits to the ownership group."""
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError:
        raise HTTPException(status_code=400, detail="Invalid session")

    if session.payment_status != "paid":
        raise HTTPException(status_code=400, detail="Payment not completed")

    metadata = session.metadata or {}
    if metadata.get("type") != "ai_credits":
        raise HTTPException(status_code=400, detail="Invalid session type")

    amount_usd = float(metadata.get("amount_usd", 0))
    if amount_usd <= 0:
        raise HTTPException(status_code=400, detail="Invalid credit amount")

    from backend.services.billing import add_purchased_credits, get_ownership_group_id

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    if not og_id:
        raise HTTPException(status_code=400, detail="No ownership group found")

    new_balance = await add_purchased_credits(db, og_id, amount_usd)
    await db.commit()

    return {"credits_added_usd": amount_usd, "new_balance_usd": new_balance}


@router.get("/usage")
async def get_usage(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the full billing summary for the current ownership group.

    Includes LLM usage, storage, and employee count charges.
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
        }

    return await get_full_billing_summary(db, og_id)


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
