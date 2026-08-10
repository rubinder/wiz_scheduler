"""Free-plan limits and plan-state derivation.

Plan is DERIVED from ownership_groups columns, never stored:

    paid  <=>  stripe_subscription_id IS NOT NULL AND canceled_at IS NULL
    free  <=>  otherwise

A stored `plan` column would be a second source of truth that drifts the
first time a Stripe webhook is missed or replayed out of order. Because
checkout runs in mode="subscription", every existing paying ownership group
derives to "paid" with no migration.

A Company with no ownership_group_id is treated as UNLIMITED, matching the
convention in dependencies.require_active_billing. No production path creates
one — the state exists only in seed data and tests.
"""

import logging
from datetime import datetime
from typing import Literal, TypedDict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import (
    count_employees_for_group,
    count_locations_for_group,
    get_ownership_group_id,
)

logger = logging.getLogger(__name__)


class LimitCount(TypedDict):
    count: int
    limit: int | None  # None == unlimited (paid)


class PlanState(TypedDict):
    plan: Literal["free", "paid"]
    canceled_at: datetime | None
    locations: LimitCount
    employees: LimitCount
    over_limit: bool
    can_generate_local: bool
    can_generate_ai: bool
    block_reason: str | None


def _unlimited(canceled_at: datetime | None = None) -> PlanState:
    return PlanState(
        plan="paid",
        canceled_at=canceled_at,
        locations=LimitCount(count=0, limit=None),
        employees=LimitCount(count=0, limit=None),
        over_limit=False,
        can_generate_local=True,
        can_generate_ai=True,
        block_reason=None,
    )


async def get_plan_state(db: AsyncSession, company_id: str) -> PlanState:
    """Resolve the plan state for the ownership group owning *company_id*."""
    og_id = await get_ownership_group_id(db, str(company_id))
    if not og_id:
        # No ownership group — seed/dev data. Ungated, as in
        # require_active_billing.
        return _unlimited()

    og = await db.get(OwnershipGroup, og_id)
    if og is None:
        return _unlimited()

    is_paid = og.stripe_subscription_id is not None and og.canceled_at is None
    if is_paid:
        state = _unlimited()
        state["locations"]["count"] = await count_locations_for_group(db, og_id)
        state["employees"]["count"] = await count_employees_for_group(db, og_id)
        return state

    loc_count = await count_locations_for_group(db, og_id)
    emp_count = await count_employees_for_group(db, og_id)
    over_limit = (
        loc_count > settings.FREE_PLAN_MAX_LOCATIONS
        or emp_count > settings.FREE_PLAN_MAX_EMPLOYEES
    )

    # An over-limit free OG is only reachable by downgrade (paid -> canceled
    # while over the limits), because every write path is capped.
    block_reason: str | None = None
    if over_limit:
        block_reason = (
            "subscription_canceled" if og.canceled_at is not None
            else "plan_limit_exceeded"
        )

    return PlanState(
        plan="free",
        canceled_at=og.canceled_at,
        locations=LimitCount(
            count=loc_count, limit=settings.FREE_PLAN_MAX_LOCATIONS
        ),
        employees=LimitCount(
            count=emp_count, limit=settings.FREE_PLAN_MAX_EMPLOYEES
        ),
        over_limit=over_limit,
        can_generate_local=not over_limit,
        can_generate_ai=False,
        block_reason=block_reason,
    )


def _limit_error(
    limit: str, max_allowed: int, current: int, attempted: int
) -> HTTPException:
    noun = "locations" if limit == "locations" else "employees"
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "plan_limit_exceeded",
            "message": (
                f"Free plan allows {max_allowed} {noun}. "
                f"Upgrade to add more."
            ),
            "limit": limit,
            "max": max_allowed,
            "current": current,
            "attempted": attempted,
        },
    )


async def assert_can_add(
    db: AsyncSession,
    company_id: str,
    *,
    locations: int = 0,
    employees: int = 0,
) -> None:
    """Raise 402 if adding this many rows would exceed the free plan.

    No-op when the ownership group is paid or absent.

    Takes a row lock on the ownership_groups row before counting: count-then-
    insert is not atomic, so two managers both sitting at 4/5 employees could
    otherwise both pass and both insert. The lock is per ownership group and
    only taken on the free path, so paid tenants pay nothing for it.
    """
    og_id = await get_ownership_group_id(db, str(company_id))
    if not og_id:
        return

    og = await db.get(OwnershipGroup, og_id)
    if og is None:
        return
    if og.stripe_subscription_id is not None and og.canceled_at is None:
        return  # paid — unlimited, and no lock taken

    # Serialize concurrent adds for this ownership group.
    await db.execute(
        select(OwnershipGroup.id)
        .where(OwnershipGroup.id == og_id)
        .with_for_update()
    )

    if locations:
        current = await count_locations_for_group(db, og_id)
        if current + locations > settings.FREE_PLAN_MAX_LOCATIONS:
            raise _limit_error(
                "locations", settings.FREE_PLAN_MAX_LOCATIONS, current, locations
            )

    if employees:
        current = await count_employees_for_group(db, og_id)
        if current + employees > settings.FREE_PLAN_MAX_EMPLOYEES:
            raise _limit_error(
                "employees", settings.FREE_PLAN_MAX_EMPLOYEES, current, employees
            )
