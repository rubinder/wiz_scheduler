"""Billing service for LLM usage, storage, and employee counts.

Pricing per ownership group per month:
- LLM: $2 free, then 130% of raw cost per usage
- Storage: 0.5 GB free, then $0.50/GB
- Employees: 250k free, then $0.20 per 250k block
"""

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, StorageSnapshot, TokenUsage
from backend.models.ownership_group import OwnershipGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Calculate the raw dollar cost for a set of tokens."""
    input_cost = (input_tokens / 1_000_000) * settings.LLM_INPUT_COST_PER_M
    output_cost = (output_tokens / 1_000_000) * settings.LLM_OUTPUT_COST_PER_M
    return round(input_cost + output_cost, 6)


async def get_monthly_usage(
    db: AsyncSession,
    ownership_group_id: str,
) -> TokenUsage | None:
    """Get the current month's token usage record for an ownership group."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(TokenUsage).where(
            TokenUsage.ownership_group_id == ownership_group_id,
            TokenUsage.year == now.year,
            TokenUsage.month == now.month,
        )
    )
    return result.scalar_one_or_none()


async def get_ownership_group_id(db: AsyncSession, company_id: str) -> str | None:
    """Resolve company_id to ownership_group_id."""
    result = await db.execute(
        select(Company.ownership_group_id).where(Company.id == company_id)
    )
    return result.scalar_one_or_none()


async def _get_company_ids_for_group(db: AsyncSession, og_id: str) -> list[str]:
    """Get all company IDs belonging to an ownership group."""
    result = await db.execute(
        select(Company.id).where(Company.ownership_group_id == og_id)
    )
    return [str(r) for r in result.scalars().all()]


# ---------------------------------------------------------------------------
# Stripe payment-method caching
# ---------------------------------------------------------------------------

async def cache_default_payment_method(
    db: AsyncSession,
    og: OwnershipGroup,
) -> str | None:
    """Fetch the subscription's default payment method and cache it on the OG.

    Returns the payment method ID, or None if the OG has no subscription.
    Idempotent: re-runs are safe and refresh the cached value.
    """
    if not og.stripe_subscription_id:
        return None

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
    pm_id = sub.default_payment_method
    if pm_id:
        og.default_payment_method_id = pm_id
        await db.flush()
    return pm_id


# ---------------------------------------------------------------------------
# LLM billing
# ---------------------------------------------------------------------------

async def check_and_record_usage(
    db: AsyncSession,
    company_id: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    """Record token usage and calculate billing.

    Returns a dict with:
        - cost_usd: raw cost of this usage
        - charged_usd: amount charged (0 if within free tier, cost*markup if over)
        - is_over_free_tier: whether the group has exceeded the free tier
        - monthly_cost_usd: total cost this month after this usage
        - monthly_charged_usd: total charged this month after this usage
        - free_remaining_usd: remaining free credits (0 if exhausted)
    """
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return {"cost_usd": 0, "charged_usd": 0, "is_over_free_tier": False,
                "monthly_cost_usd": 0, "monthly_charged_usd": 0, "free_remaining_usd": settings.LLM_FREE_TIER_USD}

    now = datetime.now(timezone.utc)
    usage = await get_monthly_usage(db, og_id)

    this_cost = calculate_cost(input_tokens, output_tokens)

    if usage:
        cost_before = usage.cost_usd
        cost_after = cost_before + this_cost

        if cost_before >= settings.LLM_FREE_TIER_USD:
            this_charge = round(this_cost * settings.LLM_OVERAGE_MARKUP, 6)
        elif cost_after > settings.LLM_FREE_TIER_USD:
            overage = cost_after - settings.LLM_FREE_TIER_USD
            this_charge = round(overage * settings.LLM_OVERAGE_MARKUP, 6)
        else:
            this_charge = 0.0

        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.total_tokens += input_tokens + output_tokens
        usage.cost_usd = cost_after
        usage.charged_usd += this_charge
        usage.updated_at = now
    else:
        if this_cost > settings.LLM_FREE_TIER_USD:
            overage = this_cost - settings.LLM_FREE_TIER_USD
            this_charge = round(overage * settings.LLM_OVERAGE_MARKUP, 6)
        else:
            this_charge = 0.0

        usage = TokenUsage(
            ownership_group_id=og_id,
            year=now.year,
            month=now.month,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=this_cost,
            charged_usd=this_charge,
        )
        db.add(usage)

    if this_charge > 0:
        from sqlalchemy import select
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
        )
        og = og_result.scalar_one()
        await auto_reload_if_needed(db, og, cost_usd=this_charge)

    await db.flush()

    free_remaining = max(0, settings.LLM_FREE_TIER_USD - usage.cost_usd)

    logger.info(
        "[BILLING] group=%s cost=%.4f charged=%.4f monthly_total=%.4f free_remaining=%.4f",
        og_id, this_cost, this_charge, usage.cost_usd, free_remaining,
    )

    return {
        "cost_usd": this_cost,
        "charged_usd": this_charge,
        "is_over_free_tier": usage.cost_usd > settings.LLM_FREE_TIER_USD,
        "monthly_cost_usd": usage.cost_usd,
        "monthly_charged_usd": usage.charged_usd,
        "free_remaining_usd": free_remaining,
    }


# ---------------------------------------------------------------------------
# Storage billing
# ---------------------------------------------------------------------------

async def calculate_storage_gb(db: AsyncSession, og_id: str) -> float:
    """Estimate storage used by an ownership group in GB.

    Sums pg_total_relation_size for rows belonging to the group's companies
    across key tables. This is an approximation — exact per-row sizes aren't
    available without table scans, so we use row counts * avg row size estimates.
    """
    company_ids = await _get_company_ids_for_group(db, og_id)
    if not company_ids:
        return 0.0

    # Count rows across the main data tables for these companies
    tables_with_company_id = [
        "employees", "employee_roles", "employee_availability",
        "employee_affinities", "employee_companies", "employee_invites",
        "shift_schedules", "shifts", "shift_templates",
        "roles", "locations", "regions", "failure_logs",
        "user_consents", "employee_role_minutes",
    ]

    total_bytes = 0
    # Avg bytes per row estimates (conservative)
    avg_row_bytes = {
        "employees": 256,
        "employee_roles": 64,
        "employee_availability": 128,
        "employee_affinities": 96,
        "employee_companies": 48,
        "employee_invites": 256,
        "shift_schedules": 4096,       # raw_llm_output can be large
        "shifts": 192,
        "shift_templates": 2048,       # weekly_schedule JSON
        "roles": 128,
        "locations": 192,
        "regions": 96,
        "failure_logs": 512,
        "user_consents": 128,
        "employee_role_minutes": 64,
    }

    placeholders = ", ".join(f":cid_{i}" for i in range(len(company_ids)))
    params = {f"cid_{i}": cid for i, cid in enumerate(company_ids)}

    for table in tables_with_company_id:
        try:
            result = await db.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE company_id IN ({placeholders})"),
                params,
            )
            row_count = result.scalar() or 0
            total_bytes += row_count * avg_row_bytes.get(table, 128)
        except Exception:
            pass  # Table might not exist or have different schema

    # Also count users table (linked via company_id)
    try:
        result = await db.execute(
            text(f"SELECT COUNT(*) FROM users WHERE company_id IN ({placeholders})"),
            params,
        )
        row_count = result.scalar() or 0
        total_bytes += row_count * 192
    except Exception:
        pass

    return total_bytes / (1024 * 1024 * 1024)  # bytes to GB


def calculate_storage_charge(storage_gb: float) -> float:
    """Calculate monthly storage charge.

    First 0.5 GB free, then $0.50/GB for each additional GB.
    """
    billable_gb = max(0, storage_gb - settings.STORAGE_FREE_GB)
    return round(billable_gb * settings.STORAGE_COST_PER_GB, 4)


async def record_storage_snapshots(db: AsyncSession) -> dict:
    """Measure and persist today's storage usage for every ownership group.

    Skips groups that already have a snapshot for today (idempotent).
    Returns a summary dict: {"recorded": int, "skipped": int, "errors": int}.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    og_result = await db.execute(select(OwnershipGroup.id))
    og_ids = list(og_result.scalars().all())

    recorded = 0
    skipped = 0
    errors = 0

    for og_id in og_ids:
        # Check if snapshot already exists for today
        existing = await db.execute(
            select(StorageSnapshot.id).where(
                StorageSnapshot.ownership_group_id == og_id,
                StorageSnapshot.snapshot_date == today,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        try:
            storage_gb = await calculate_storage_gb(db, og_id)
            charged_usd = calculate_storage_charge(storage_gb)
            snapshot = StorageSnapshot(
                ownership_group_id=og_id,
                snapshot_date=today,
                measured_at=now,
                storage_gb=round(storage_gb, 6),
                charged_usd=round(charged_usd, 4),
            )
            db.add(snapshot)
            await db.flush()
            recorded += 1
        except Exception as e:
            logger.error("Failed to record storage snapshot for OG %s: %s", og_id, e)
            errors += 1

    await db.commit()
    summary = {"recorded": recorded, "skipped": skipped, "errors": errors}
    logger.info("Storage snapshots: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# Employee billing
# ---------------------------------------------------------------------------

async def count_employees_for_group(db: AsyncSession, og_id: str) -> int:
    """Count total employees across all companies in an ownership group."""
    company_ids = await _get_company_ids_for_group(db, og_id)
    if not company_ids:
        return 0

    result = await db.execute(
        select(func.count(Employee.id)).where(
            Employee.company_id.in_(company_ids)
        )
    )
    return result.scalar() or 0


def calculate_employee_charge(employee_count: int) -> float:
    """Calculate monthly employee charge.

    First 250k free, then $0.20 per 250k block (rounded up).
    """
    billable = max(0, employee_count - settings.EMPLOYEE_FREE_TIER)
    if billable == 0:
        return 0.0
    blocks = math.ceil(billable / settings.EMPLOYEE_BLOCK_SIZE)
    return round(blocks * settings.EMPLOYEE_COST_PER_BLOCK, 4)


# ---------------------------------------------------------------------------
# Schedule generation billing
# ---------------------------------------------------------------------------

async def count_schedules_this_month(db: AsyncSession, og_id: str) -> int:
    """Count schedules generated this month across all companies in a group."""
    from backend.models import ShiftSchedule

    company_ids = await _get_company_ids_for_group(db, og_id)
    if not company_ids:
        return 0

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.count(ShiftSchedule.id)).where(
            ShiftSchedule.company_id.in_(company_ids),
            ShiftSchedule.created_at >= month_start,
        )
    )
    return result.scalar() or 0


def calculate_schedule_charge(schedule_count: int) -> float:
    """Calculate monthly schedule generation charge.

    First 50 free, then $0.10 per 100 schedules (rounded up to next block).
    """
    billable = max(0, schedule_count - settings.SCHEDULE_FREE_TIER)
    if billable == 0:
        return 0.0
    blocks = math.ceil(billable / settings.SCHEDULE_BLOCK_SIZE)
    return round(blocks * settings.SCHEDULE_COST_PER_BLOCK, 4)


# ---------------------------------------------------------------------------
# Schedule quota check
# ---------------------------------------------------------------------------


async def check_schedule_quota(
    db: AsyncSession,
    company_id: str,
) -> dict:
    """Check whether the ownership group can generate more schedules.

    Returns:
        - can_generate: True if within free tier or has purchased credits
        - schedules_used: number of schedules generated this month
        - schedules_free_tier: free tier limit
        - is_over_free_tier: whether the free tier is exhausted
        - purchased_credits_usd: purchased credit balance (shared with AI)
        - next_block_cost_usd: cost for the next block of schedules
    """
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return {
            "can_generate": True,
            "schedules_used": 0,
            "schedules_free_tier": settings.SCHEDULE_FREE_TIER,
            "is_over_free_tier": False,
            "purchased_credits_usd": 0.0,
            "next_block_cost_usd": settings.SCHEDULE_COST_PER_BLOCK,
        }

    # Load OG to check autoreload state — blocks generation when a prior charge failed.
    og_full = (await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))).scalar_one_or_none()
    if og_full and og_full.autoreload_failed_at is not None:
        return {
            "can_generate": False,
            "schedules_used": 0,
            "schedules_free_tier": settings.SCHEDULE_FREE_TIER,
            "is_over_free_tier": True,
            "purchased_credits_usd": float(og_full.ai_credits_usd),
            "next_block_cost_usd": settings.SCHEDULE_COST_PER_BLOCK,
            "autoreload_failed": True,
        }

    schedule_count = await count_schedules_this_month(db, og_id)

    # Demo company gets a higher free tier
    company_result = await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )
    company_slug = company_result.scalar_one_or_none()
    free_tier = 250 if company_slug == "acme-corp" else settings.SCHEDULE_FREE_TIER

    is_over = schedule_count >= free_tier

    purchased_credits = float(og_full.ai_credits_usd) if og_full else 0.0

    can_generate = not is_over or purchased_credits > 0

    return {
        "can_generate": can_generate,
        "schedules_used": schedule_count,
        "schedules_free_tier": free_tier,
        "is_over_free_tier": is_over,
        "purchased_credits_usd": round(purchased_credits, 4),
        "next_block_cost_usd": settings.SCHEDULE_COST_PER_BLOCK,
    }


async def deduct_credits_for_schedule_overage(
    db: AsyncSession,
    company_id: str,
) -> None:
    """Deduct purchased credits for schedule overage after generation.

    Called after a schedule is created when the group is over the free tier.
    Charges one schedule's share of the block cost.
    """
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return

    schedule_count = await count_schedules_this_month(db, og_id)
    if schedule_count <= settings.SCHEDULE_FREE_TIER:
        return

    # Per-schedule cost = block cost / block size
    per_schedule_cost = settings.SCHEDULE_COST_PER_BLOCK / settings.SCHEDULE_BLOCK_SIZE

    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id).with_for_update()
    )
    og = og_result.scalar_one_or_none()
    if not og:
        return

    await auto_reload_if_needed(db, og, cost_usd=per_schedule_cost)
    og.ai_credits_usd = round(float(og.ai_credits_usd) - per_schedule_cost, 4)
    await db.flush()


# ---------------------------------------------------------------------------
# AI credit checks & purchases
# ---------------------------------------------------------------------------


async def check_ai_credits(
    db: AsyncSession,
    company_id: str,
) -> dict:
    """Check whether the ownership group can run AI generation.

    Returns:
        - can_generate: True if within free tier or has purchased credits
        - free_remaining_usd: remaining free tier credits
        - purchased_credits_usd: purchased credit balance
        - is_over_free_tier: whether the free tier is exhausted
    """
    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return {
            "can_generate": True,
            "free_remaining_usd": settings.LLM_FREE_TIER_USD,
            "purchased_credits_usd": 0.0,
            "is_over_free_tier": False,
            "monthly_cost_usd": 0.0,
        }

    # Load OG to check autoreload state — blocks generation when a prior charge failed.
    og_full = (await db.execute(select(OwnershipGroup).where(OwnershipGroup.id == og_id))).scalar_one_or_none()
    if og_full and og_full.autoreload_failed_at is not None:
        return {
            "can_generate": False,
            "free_remaining_usd": 0.0,
            "purchased_credits_usd": float(og_full.ai_credits_usd),
            "is_over_free_tier": True,
            "monthly_cost_usd": 0.0,
            "autoreload_failed": True,
        }

    usage = await get_monthly_usage(db, og_id)
    monthly_cost = usage.cost_usd if usage else 0.0
    free_remaining = max(0.0, settings.LLM_FREE_TIER_USD - monthly_cost)
    is_over = monthly_cost >= settings.LLM_FREE_TIER_USD

    purchased_credits = float(og_full.ai_credits_usd) if og_full else 0.0

    can_generate = not is_over or purchased_credits > 0

    return {
        "can_generate": can_generate,
        "free_remaining_usd": round(free_remaining, 4),
        "purchased_credits_usd": round(purchased_credits, 4),
        "is_over_free_tier": is_over,
        "monthly_cost_usd": round(monthly_cost, 4),
    }


async def deduct_credits_for_overage(
    db: AsyncSession,
    company_id: str,
    charged_usd: float,
) -> None:
    """Deduct purchased AI credits for any overage charge.

    Called after check_and_record_usage when the group is over the free tier.
    """
    if charged_usd <= 0:
        return

    og_id = await get_ownership_group_id(db, company_id)
    if not og_id:
        return

    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == og_id)
    )
    og = og_result.scalar_one_or_none()
    if not og:
        return

    og.ai_credits_usd = max(0.0, og.ai_credits_usd - charged_usd)
    await db.flush()


async def add_purchased_credits(
    db: AsyncSession,
    ownership_group_id: str,
    amount_usd: float,
) -> float:
    """Add purchased AI credits to an ownership group. Returns new balance."""
    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == ownership_group_id)
    )
    og = og_result.scalar_one_or_none()
    if not og:
        return 0.0

    og.ai_credits_usd = round(og.ai_credits_usd + amount_usd, 4)
    await db.flush()
    return og.ai_credits_usd


# ---------------------------------------------------------------------------
# Full billing summary
# ---------------------------------------------------------------------------

async def get_full_billing_summary(db: AsyncSession, og_id: str) -> dict:
    """Compute the full billing summary for an ownership group."""
    # LLM usage
    usage = await get_monthly_usage(db, og_id)
    llm_cost = usage.cost_usd if usage else 0.0
    llm_charged = usage.charged_usd if usage else 0.0
    llm_free_remaining = max(0, settings.LLM_FREE_TIER_USD - llm_cost)

    # Storage
    storage_gb = await calculate_storage_gb(db, og_id)
    storage_charge = calculate_storage_charge(storage_gb)

    # Employees
    employee_count = await count_employees_for_group(db, og_id)
    employee_charge = calculate_employee_charge(employee_count)

    # Schedules
    schedule_count = await count_schedules_this_month(db, og_id)
    schedule_charge = calculate_schedule_charge(schedule_count)

    # Base subscription
    base_charge = settings.BASE_MONTHLY_USD

    total_monthly_charge = round(
        base_charge + llm_charged + storage_charge + employee_charge + schedule_charge, 4
    )

    return {
        "base": {
            "monthly_usd": base_charge,
        },
        "llm": {
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "raw_cost_usd": round(llm_cost, 4),
            "charged_usd": round(llm_charged, 4),
            "free_tier_usd": settings.LLM_FREE_TIER_USD,
            "free_remaining_usd": round(llm_free_remaining, 4),
            "is_over_free_tier": llm_cost > settings.LLM_FREE_TIER_USD,
            "overage_markup": settings.LLM_OVERAGE_MARKUP,
        },
        "storage": {
            "used_gb": round(storage_gb, 4),
            "free_gb": settings.STORAGE_FREE_GB,
            "billable_gb": round(max(0, storage_gb - settings.STORAGE_FREE_GB), 4),
            "cost_per_gb": settings.STORAGE_COST_PER_GB,
            "charged_usd": storage_charge,
        },
        "employees": {
            "count": employee_count,
            "free_tier": settings.EMPLOYEE_FREE_TIER,
            "billable": max(0, employee_count - settings.EMPLOYEE_FREE_TIER),
            "block_size": settings.EMPLOYEE_BLOCK_SIZE,
            "cost_per_block": settings.EMPLOYEE_COST_PER_BLOCK,
            "charged_usd": employee_charge,
        },
        "schedules": {
            "count": schedule_count,
            "free_tier": settings.SCHEDULE_FREE_TIER,
            "billable": max(0, schedule_count - settings.SCHEDULE_FREE_TIER),
            "block_size": settings.SCHEDULE_BLOCK_SIZE,
            "cost_per_block": settings.SCHEDULE_COST_PER_BLOCK,
            "charged_usd": schedule_charge,
        },
        "total_monthly_charge_usd": total_monthly_charge,
    }


# ---------------------------------------------------------------------------
# Auto-reload (real-time billing buffer for AI + schedules)
# ---------------------------------------------------------------------------

class AutoReloadError(Exception):
    """Charge attempt against the saved card failed (declined, SCA, etc.)."""


class AutoReloadDisabled(Exception):
    """OG has autoreload_enabled=False and balance is insufficient."""


class AutoReloadBlocked(Exception):
    """OG has a sticky autoreload_failed_at and must be manually retried."""


async def auto_reload_if_needed(
    db: AsyncSession,
    og: OwnershipGroup,
    cost_usd: float,
) -> None:
    """Charge the customer's saved card if needed to keep balance above threshold.

    Called by debit paths (AI overage, schedule overage) BEFORE the debit is applied.
    Raises if the customer is in a non-chargeable state — callers should propagate
    the error as HTTP 402 to block the operation.

    Side effects:
        - On success: increments og.ai_credits_usd by autoreload_amount_usd,
          writes a 'succeeded' BillingCharge row.
        - On Stripe failure: sets og.autoreload_failed_at = now(),
          writes a 'failed' BillingCharge row, then raises AutoReloadError.
    """
    from datetime import datetime, timezone
    import stripe
    from backend.config import settings
    from backend.models.billing_charge import BillingCharge

    if og.autoreload_failed_at is not None:
        raise AutoReloadBlocked(
            f"Billing on hold since {og.autoreload_failed_at.isoformat()}; "
            "retry payment in the Billing UI."
        )

    threshold = float(og.autoreload_threshold_usd)
    if float(og.ai_credits_usd) - cost_usd >= threshold:
        return  # balance after debit will still be above threshold

    if not og.autoreload_enabled:
        raise AutoReloadDisabled(
            "Auto-reload is disabled and the included AI/schedule quota is exhausted."
        )

    if not og.stripe_customer_id or not og.default_payment_method_id:
        raise AutoReloadError(
            "Customer has no payment method on file. Add a card to enable auto-reload."
        )

    stripe.api_key = settings.STRIPE_SECRET_KEY
    reload_amount_usd = float(og.autoreload_amount_usd)

    try:
        intent = stripe.PaymentIntent.create(
            customer=og.stripe_customer_id,
            amount=int(reload_amount_usd * 100),
            currency="usd",
            payment_method=og.default_payment_method_id,
            off_session=True,
            confirm=True,
            metadata={"og_id": og.id, "kind": "autoreload"},
        )
    except stripe.StripeError as e:
        og.autoreload_failed_at = datetime.now(timezone.utc)
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind="autoreload",
            amount_usd=reload_amount_usd,
            stripe_object_id=None,
            status="failed",
            error_message=str(e),
        ))
        await db.flush()
        raise AutoReloadError(str(e))

    if intent.status != "succeeded":
        og.autoreload_failed_at = datetime.now(timezone.utc)
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind="autoreload",
            amount_usd=reload_amount_usd,
            stripe_object_id=intent.id,
            status="failed",
            error_message=f"PaymentIntent status={intent.status}",
        ))
        await db.flush()
        raise AutoReloadError(f"PaymentIntent status: {intent.status}")

    og.ai_credits_usd = round(float(og.ai_credits_usd) + reload_amount_usd, 4)
    db.add(BillingCharge(
        ownership_group_id=og.id,
        kind="autoreload",
        amount_usd=reload_amount_usd,
        stripe_object_id=intent.id,
        status="succeeded",
    ))
    await db.flush()
