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
from backend.models import Company, Employee, StorageSnapshot, TokenUsage, TokenUsageDaily
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


async def get_og_anthropic_spend_24h(
    db: AsyncSession,
    ownership_group_id: str,
) -> float:
    """Sum raw Anthropic cost (USD) charged to this OG over the last 24h.

    Reads the per-day aggregate (token_usage_daily). With 24h windows
    that may straddle midnight UTC, today's row contributes its full
    cost_usd and yesterday's row contributes a proportional share
    derived from how much of yesterday lies inside the 24h window.

    The proportional split is intentionally cheap (no per-call timestamps
    exist) — it's a defensive ceiling, not a tax calculation.
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    today = now.date()
    yesterday = today - timedelta(days=1)
    rows = (await db.execute(
        select(TokenUsageDaily).where(
            TokenUsageDaily.ownership_group_id == ownership_group_id,
            TokenUsageDaily.usage_date.in_((today, yesterday)),
        )
    )).scalars().all()

    today_cost = 0.0
    yesterday_cost = 0.0
    for row in rows:
        if row.usage_date == today:
            today_cost = row.cost_usd
        else:
            yesterday_cost = row.cost_usd

    # Fraction of yesterday inside the 24h window: hours elapsed today /
    # 24. At 03:00 UTC, that's 3/24 = 0.125 — yesterday contributes 87.5%
    # of its total to "last 24h".
    seconds_into_today = (
        now - now.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    yesterday_fraction = max(0.0, 1.0 - (seconds_into_today / 86400.0))

    return round(today_cost + yesterday_cost * yesterday_fraction, 6)


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

    # Mirror into the per-day aggregate so the daily circuit breaker
    # (#43) has a rolling-24h figure to query. The monthly aggregate
    # above is the source of truth for billing; daily exists purely for
    # the cap query.
    today = now.date()
    daily = (await db.execute(
        select(TokenUsageDaily).where(
            TokenUsageDaily.ownership_group_id == og_id,
            TokenUsageDaily.usage_date == today,
        )
    )).scalar_one_or_none()
    if daily is None:
        daily = TokenUsageDaily(
            ownership_group_id=og_id,
            usage_date=today,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=this_cost,
        )
        db.add(daily)
    else:
        daily.input_tokens += input_tokens
        daily.output_tokens += output_tokens
        daily.total_tokens += input_tokens + output_tokens
        daily.cost_usd += this_cost
        daily.updated_at = now

    if this_charge > 0:
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


async def compute_monthly_overage(
    db: AsyncSession,
    og_id: str,
    kind: str,
    period: str,
) -> float:
    """Compute the dollar overage charge for a given kind+period.

    `kind` must be 'invoice_item_storage' or 'invoice_item_employees'.
    `period` is 'YYYY-MM'. Storage uses the most recent snapshot within
    that calendar month; employees uses the current point-in-time count
    (employee billing is anniversary-period based, not historical).
    """
    from datetime import date

    if kind == "invoice_item_storage":
        year, month = int(period[:4]), int(period[5:7])
        next_month = 1 if month == 12 else month + 1
        next_year = year + 1 if month == 12 else year
        result = await db.execute(
            select(StorageSnapshot)
            .where(
                StorageSnapshot.ownership_group_id == og_id,
                StorageSnapshot.snapshot_date >= date(year, month, 1),
                StorageSnapshot.snapshot_date < date(next_year, next_month, 1),
            )
            .order_by(StorageSnapshot.snapshot_date.desc())
            .limit(1)
        )
        snap = result.scalar_one_or_none()
        if not snap:
            return 0.0
        return calculate_storage_charge(float(snap.storage_gb))

    if kind == "invoice_item_employees":
        count = await count_employees_for_group(db, og_id)
        return calculate_employee_charge(count)

    raise ValueError(f"Unknown overage kind: {kind!r}")


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


# ---------------------------------------------------------------------------
# Monthly InvoiceItem reconciliation (PR 2)
# ---------------------------------------------------------------------------


async def bill_monthly_overages_for_og(
    db: AsyncSession,
    og: OwnershipGroup,
    period: str | None = None,
) -> dict:
    """Reconcile storage + employee overage InvoiceItems for one OG.

    For each kind in ('invoice_item_storage', 'invoice_item_employees'):
        - Compute the current charge from live usage data.
        - Look up the existing pending BillingCharge for (og, period, kind).
        - Create, update (delete + recreate), or delete the Stripe InvoiceItem
          and matching BillingCharge row so the final state matches the
          computed amount.

    Subscriptions not in {active, trialing} are skipped — Stripe will not
    bill anyway, and we don't want to dirty the audit log.

    `period` defaults to the calendar month of the subscription's current
    period_start (i.e. the period the upcoming invoice covers).
    """
    import stripe
    from backend.config import settings
    from backend.models.billing_charge import BillingCharge

    if not og.stripe_subscription_id or not og.stripe_customer_id:
        return {"og_id": og.id, "skipped": "no_subscription"}

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        sub = stripe.Subscription.retrieve(og.stripe_subscription_id)
    except stripe.StripeError as e:
        return {"og_id": og.id, "error": str(e)}

    if sub.status not in ("active", "trialing"):
        return {"og_id": og.id, "skipped": f"subscription_status={sub.status}"}

    if period is None:
        from datetime import datetime as _dt, timezone as _tz
        start = _dt.fromtimestamp(int(sub.current_period_start), _tz.utc)
        period = f"{start.year:04d}-{start.month:02d}"

    summary: dict = {"og_id": og.id, "period": period, "actions": []}

    for kind in ("invoice_item_storage", "invoice_item_employees"):
        new_charge = await compute_monthly_overage(db, og.id, kind, period)
        new_amount_cents = int(round(new_charge * 100))

        existing = (await db.execute(
            select(BillingCharge).where(
                BillingCharge.ownership_group_id == og.id,
                BillingCharge.kind == kind,
                BillingCharge.period == period,
                BillingCharge.status == "pending",
            )
        )).scalar_one_or_none()

        if existing and int(round(float(existing.amount_usd) * 100)) == new_amount_cents and new_amount_cents > 0:
            summary["actions"].append({"kind": kind, "action": "noop"})
            continue

        if existing:
            try:
                stripe.InvoiceItem.delete(existing.stripe_object_id)
            except stripe.StripeError:
                pass  # invoice item already finalized or gone; proceed
            await db.delete(existing)
            await db.flush()

        if new_amount_cents == 0:
            summary["actions"].append({"kind": kind, "action": "deleted" if existing else "skip_zero"})
            continue

        item = stripe.InvoiceItem.create(
            customer=og.stripe_customer_id,
            subscription=og.stripe_subscription_id,
            amount=new_amount_cents,
            currency="usd",
            description=f"{kind.replace('invoice_item_', '').title()} overage — {period}",
            metadata={"og_id": og.id, "period": period, "kind": kind},
        )
        db.add(BillingCharge(
            ownership_group_id=og.id,
            kind=kind,
            amount_usd=new_charge,
            stripe_object_id=item.id,
            period=period,
            status="pending",
        ))
        await db.flush()
        summary["actions"].append({
            "kind": kind,
            "action": "created" if not existing else "updated",
            "amount_usd": new_charge,
        })

    await db.commit()
    return summary


async def bill_monthly_overages_all(db: AsyncSession) -> dict:
    """Run bill_monthly_overages_for_og across every OG with a Stripe subscription.

    Designed to be invoked weekly by a background loop and on-demand by the
    `invoice.upcoming` Stripe webhook handler (defense in depth).
    """
    import logging
    log = logging.getLogger("wizscheduler.monthly_billing")

    result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.stripe_subscription_id.is_not(None))
    )
    ogs = list(result.scalars())

    results: list[dict] = []
    for og in ogs:
        try:
            summary = await bill_monthly_overages_for_og(db, og)
            results.append(summary)
        except Exception as e:  # noqa: BLE001
            log.error("monthly billing failed for og=%s: %s", og.id, e)
            results.append({"og_id": og.id, "error": str(e)})

    return {"total_ogs": len(ogs), "results": results}


async def send_subscription_ended_email(og: OwnershipGroup) -> None:
    """Send the 'your subscription has ended' email via Resend.

    Idempotency is the caller's responsibility (the webhook handler checks
    notified_subscription_ended_at before invoking). Non-fatal — exceptions
    are logged and swallowed so a Resend outage doesn't drop the webhook.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping subscription-ended email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    from sqlalchemy import select
    from backend.models import Company, User
    from backend.database import async_session_factory

    async with async_session_factory() as db:
        company_ids = (await db.execute(
            select(Company.id).where(Company.ownership_group_id == og.id)
        )).scalars().all()
        if not company_ids:
            return
        manager_emails = (await db.execute(
            select(User.email).where(
                User.company_id.in_(company_ids),
                User.user_role == "manager",
            )
        )).scalars().all()
        manager_emails = [e for e in manager_emails if e]

    if not manager_emails:
        logger.info("No manager emails found for og=%s; subscription-ended email skipped", og.id)
        return

    try:
        import html as _html
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": "Your WizScheduler subscription has ended",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {_html.escape(og.name)},</p>"
                f"<p>Your WizScheduler subscription has been canceled. "
                f"You still have read-only access to your data.</p>"
                f"<p><strong>You have {settings.SUBSCRIPTION_GRACE_DAYS} days to reactivate</strong> before "
                f"your data is permanently deleted.</p>"
                f'<p>To resume scheduling, log in and click <em>Reactivate '
                f"Subscription</em>.</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.error(
            "Failed to send subscription-ended email for og=%s",
            og.id,
            exc_info=True,
        )


async def send_deletion_reminder_email(og: OwnershipGroup) -> None:
    """Day-76 reminder: 14 days until the OG's data is permanently deleted.

    Caller (the cron) is responsible for idempotency via
    notified_deletion_reminder_at. Resend errors are swallowed.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping deletion-reminder email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    from sqlalchemy import select
    from backend.models import Company, User
    from backend.database import async_session_factory
    import html as _html
    from datetime import timedelta

    async with async_session_factory() as db:
        company_ids = (await db.execute(
            select(Company.id).where(Company.ownership_group_id == og.id)
        )).scalars().all()
        if not company_ids:
            return
        manager_emails = (await db.execute(
            select(User.email).where(
                User.company_id.in_(company_ids),
                User.user_role == "manager",
            )
        )).scalars().all()
        manager_emails = [e for e in manager_emails if e]

    if not manager_emails:
        logger.info("No manager emails for og=%s; deletion-reminder skipped", og.id)
        return

    if og.canceled_at is None:
        # Defensive: caller should have filtered, but don't send a reminder
        # to an active account.
        logger.warning("send_deletion_reminder_email called for og=%s with canceled_at=None", og.id)
        return

    deletion_date = (og.canceled_at + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)).date()

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": f"Your WizScheduler data will be deleted in {settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE} days",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {_html.escape(og.name)},</p>"
                f"<p><strong>Your WizScheduler data is scheduled for permanent "
                f"deletion on {deletion_date.isoformat()}</strong> — "
                f"{settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE} days from today.</p>"
                f"<p>If you want to keep your data, log in and click "
                f"<em>Reactivate Subscription</em> before that date.</p>"
                f"<p>If you do nothing, all of your employees, schedules, "
                f"locations, and historical data will be permanently and "
                f"irreversibly deleted.</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.error(
            "Failed to send deletion-reminder email for og=%s",
            og.id,
            exc_info=True,
        )


async def delete_og_and_dependents(db: AsyncSession, og: OwnershipGroup) -> None:
    """Permanently delete an ownership group and every row referencing it.

    Called by the day-90 cron after send_data_deleted_email. Caller must commit.

    Deletes 19 explicit tables in dependency order plus the OG row.
    storage_snapshots and billing_charges cascade automatically via
    ondelete=CASCADE on their FK to ownership_groups.id.

    PostgreSQL bulk DELETE checks FK constraints at end-of-statement, so
    self-FK tables (employees.id->employees.id, condensed_roles->condensed_roles,
    shift_schedules->shift_schedules) work correctly in a single statement.
    """
    from sqlalchemy import delete, select
    from backend.models import (
        Company,
        Employee,
        EmployeeAffinity,
        EmployeeAvailability,
        EmployeeCompany,
        EmployeeDayBlackout,
        EmployeeInvite,
        EmployeeRole,
        Location,
        Region,
        Role,
        Shift,
        ShiftSchedule,
        ShiftTemplate,
        User,
    )
    from backend.models.condensed_role import CondensedRole
    from backend.models.consent import UserConsent
    from backend.models.department import Department
    from backend.models.employee_role_minutes import EmployeeRoleMinutes
    from backend.models.failure_log import FailureLog
    from backend.models.token_usage import TokenUsage
    from backend.models.token_usage_daily import TokenUsageDaily

    company_ids = (await db.execute(
        select(Company.id).where(Company.ownership_group_id == og.id)
    )).scalars().all()

    if company_ids:
        # 1. shifts — refs shift_schedules, employees, locations, roles
        await db.execute(delete(Shift).where(Shift.company_id.in_(company_ids)))
        # 2. shift_schedules — self-FK, refs employees/locations/roles
        await db.execute(delete(ShiftSchedule).where(ShiftSchedule.company_id.in_(company_ids)))
        # 3. employee_role_minutes — refs employees, roles
        await db.execute(delete(EmployeeRoleMinutes).where(EmployeeRoleMinutes.company_id.in_(company_ids)))
        # 4. employee_roles — pivot employees ↔ roles
        await db.execute(delete(EmployeeRole).where(EmployeeRole.company_id.in_(company_ids)))
        # 5. employee_affinities — refs 2 employees
        await db.execute(delete(EmployeeAffinity).where(EmployeeAffinity.company_id.in_(company_ids)))
        # 6. employee_invites
        await db.execute(delete(EmployeeInvite).where(EmployeeInvite.company_id.in_(company_ids)))
        # 7. employee_day_blackouts
        await db.execute(delete(EmployeeDayBlackout).where(EmployeeDayBlackout.company_id.in_(company_ids)))
        # 8. employee_availability
        await db.execute(delete(EmployeeAvailability).where(EmployeeAvailability.company_id.in_(company_ids)))
        # 9. employee_companies — refs employees + companies (employee_id is CASCADE)
        await db.execute(delete(EmployeeCompany).where(EmployeeCompany.company_id.in_(company_ids)))
        # 10. departments — refs locations
        await db.execute(delete(Department).where(Department.company_id.in_(company_ids)))
        # 11. shift_templates — refs locations
        await db.execute(delete(ShiftTemplate).where(ShiftTemplate.company_id.in_(company_ids)))
        # 12. employees — self-FK + refs roles, users
        await db.execute(delete(Employee).where(Employee.company_id.in_(company_ids)))
        # 13. locations — refs regions
        await db.execute(delete(Location).where(Location.company_id.in_(company_ids)))
        # 14. condensed_roles — self-FK + refs roles; condensed_role_mappings cascade
        await db.execute(delete(CondensedRole).where(CondensedRole.company_id.in_(company_ids)))
        # 15. roles
        await db.execute(delete(Role).where(Role.company_id.in_(company_ids)))
        # 16. regions
        await db.execute(delete(Region).where(Region.company_id.in_(company_ids)))
        # 17. user_consents — refs users
        await db.execute(delete(UserConsent).where(UserConsent.company_id.in_(company_ids)))
        # 18. failure_logs
        await db.execute(delete(FailureLog).where(FailureLog.company_id.in_(company_ids)))
        # 19. users
        await db.execute(delete(User).where(User.company_id.in_(company_ids)))
        # 20. companies
        await db.execute(delete(Company).where(Company.id.in_(company_ids)))

    # 21. token_usage (OG-level, no FK cascade)
    await db.execute(delete(TokenUsage).where(TokenUsage.ownership_group_id == og.id))
    # 22. token_usage_daily (OG-level, no FK cascade)
    await db.execute(delete(TokenUsageDaily).where(TokenUsageDaily.ownership_group_id == og.id))

    # storage_snapshots + billing_charges cascade automatically.

    # The OG row itself.
    await db.delete(og)


async def process_cancellation_lifecycle(db: AsyncSession) -> dict:
    """Daily cron driver.

    Walks all OGs with canceled_at set; sends day-76 reminder once, performs
    day-90 deletion once. Returns a small summary dict for logging.
    """
    from sqlalchemy import select
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    reminder_threshold_days = (
        settings.SUBSCRIPTION_GRACE_DAYS
        - settings.SUBSCRIPTION_REMINDER_DAYS_BEFORE_DELETE
    )

    canceled_ogs = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.canceled_at.is_not(None))
    )).scalars().all()

    reminders_sent = 0
    deletions_done = 0

    for og in canceled_ogs:
        # canceled_at column is timezone-aware UTC in PG; SQLite drops tz.
        canceled_at = og.canceled_at
        if canceled_at.tzinfo is None:
            canceled_at = canceled_at.replace(tzinfo=timezone.utc)
        age_days = (now - canceled_at).days

        # Day 76: send reminder (once)
        if age_days >= reminder_threshold_days and og.notified_deletion_reminder_at is None:
            await send_deletion_reminder_email(og)
            og.notified_deletion_reminder_at = now
            reminders_sent += 1

        # Day 90: delete (the row goes away so this is naturally idempotent)
        if age_days >= settings.SUBSCRIPTION_GRACE_DAYS:
            await send_data_deleted_email(og)  # must happen BEFORE delete
            await delete_og_and_dependents(db, og)
            deletions_done += 1

    await db.commit()
    return {"reminders_sent": reminders_sent, "deletions_done": deletions_done}


async def send_data_deleted_email(og: OwnershipGroup) -> None:
    """Final notice: the OG's data has just been (or is about to be) deleted.

    Must be called BEFORE delete_og_and_dependents so we still have access
    to the manager email addresses. Resend errors are swallowed.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping data-deleted email for og=%s (RESEND_API_KEY unset)", og.id)
        return

    from sqlalchemy import select
    from backend.models import Company, User
    from backend.database import async_session_factory
    import html as _html

    async with async_session_factory() as db:
        company_ids = (await db.execute(
            select(Company.id).where(Company.ownership_group_id == og.id)
        )).scalars().all()
        if not company_ids:
            return
        manager_emails = (await db.execute(
            select(User.email).where(
                User.company_id.in_(company_ids),
                User.user_role == "manager",
            )
        )).scalars().all()
        manager_emails = [e for e in manager_emails if e]

    if not manager_emails:
        logger.info("No manager emails for og=%s; data-deleted email skipped", og.id)
        return

    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": list(manager_emails),
            "subject": "Your WizScheduler data has been deleted",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hi {_html.escape(og.name)},</p>"
                f"<p>As scheduled, your WizScheduler account and all "
                f"associated data have been <strong>permanently and "
                f"irreversibly deleted</strong>.</p>"
                f"<p>This includes all employees, schedules, locations, "
                f"and historical records.</p>"
                f"<p>If you'd like to use WizScheduler again, you can "
                f"sign up for a new account from scratch.</p>"
                f"<p>Thank you for using WizScheduler.</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.error(
            "Failed to send data-deleted email for og=%s",
            og.id,
            exc_info=True,
        )
