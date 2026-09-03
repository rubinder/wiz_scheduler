"""Free-plan generation allowance, counted per LOCATION per calendar month.

The old allowance was pooled: FREE_PLAN_MAX_SCHEDULES_PER_MONTH against a
count of ShiftSchedule ROWS. Generation writes one row per location per run,
so the pooled number meant different things to different tenants — a
two-location free tenant spent its whole allowance on one run, a
one-location tenant got two runs. config.py carried a standing warning
about exactly this.

The rules, for free ownership groups only:

  * a location may end the month holding FREE_PLAN_SCHEDULES_PER_LOCATION
    schedules (1) — a week of coverage;
  * it may run FREE_PLAN_ATTEMPTS_PER_LOCATION generations (2) getting
    there, so a rejected first draft is recoverable;
  * the retry must target the SAME WEEK as the draft it replaces. The
    second attempt is for redoing a bad week, not buying a second one.

Rejecting a draft returns the slot, which is what makes the retry possible
at all. A rejected schedule is one the manager explicitly threw away; it is
not coverage, so it should not consume the allowance for coverage. The
attempts counter is what stops that being an infinite loop.

Paid groups do not pass through here — they meter against
INCLUDED_SCHEDULES_PER_MONTH — and neither does the shared public demo,
which is generated against by every visitor and would otherwise be spent
within minutes of the month turning over.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location, ShiftSchedule
from backend.services.billing import (
    count_schedules_this_month,
    get_ownership_group_id,
    is_demo_group,
    _get_company_ids_for_group,
)

logger = logging.getLogger(__name__)

# A schedule the manager explicitly threw away. Not coverage, so it does not
# hold a location's slot — but it does burn an attempt.
DISCARDED = "rejected"


class LocationQuota(TypedDict):
    location_id: str
    allowed: bool
    code: str | None
    message: str | None
    # Set when a retry is allowed but pinned to a week: the ISO date the
    # next generation for this location must use.
    required_week: str | None
    attempts_used: int
    attempts_max: int


def _month_start(today: date | None = None) -> date:
    # UTC, per the "today is always UTC" rule in CLAUDE.md.
    ref = today or datetime.now(timezone.utc).date()
    return ref.replace(day=1)


def _allow(location_id: str, attempts: int = 0) -> LocationQuota:
    return LocationQuota(
        location_id=location_id,
        allowed=True,
        code=None,
        message=None,
        required_week=None,
        attempts_used=attempts,
        attempts_max=settings.FREE_PLAN_ATTEMPTS_PER_LOCATION,
    )


async def _is_unlimited(db: AsyncSession, company_id: str) -> bool:
    """True when the per-location allowance does not apply to this tenant."""
    from backend.models.ownership_group import OwnershipGroup

    og_id = await get_ownership_group_id(db, str(company_id))
    if not og_id:
        return True  # seed/dev data with no ownership group, as elsewhere
    if is_demo_group(og_id):
        return True

    og = await db.get(OwnershipGroup, og_id)
    if og is None:
        return True
    return og.stripe_subscription_id is not None and og.canceled_at is None


async def resolve_location_quota(
    db: AsyncSession,
    company_id: str,
    location_ids: list[str],
    week_start_date: str | None = None,
) -> dict[str, LocationQuota]:
    """Decide, per location, whether a generation may run this month.

    *week_start_date* is the week the caller intends to generate. When a
    location is mid-retry the answer depends on it, so passing None returns
    the retry as allowed-with-a-required_week rather than as a refusal —
    that lets a caller ask "what is the state of this location?" without
    having chosen a week yet.
    """
    if not location_ids:
        return {}

    if await _is_unlimited(db, company_id):
        return {str(lid): _allow(str(lid)) for lid in location_ids}

    og_id = await get_ownership_group_id(db, str(company_id))
    company_ids = await _get_company_ids_for_group(db, og_id) if og_id else [company_id]

    rows = (await db.execute(
        select(
            ShiftSchedule.location_id,
            ShiftSchedule.week_start_date,
            ShiftSchedule.status,
        ).where(
            ShiftSchedule.company_id.in_(company_ids),
            ShiftSchedule.location_id.in_([str(lid) for lid in location_ids]),
            ShiftSchedule.created_at >= _month_start(),
        )
    )).all()

    by_location: dict[str, list[tuple]] = {}
    for location_id, week, status in rows:
        by_location.setdefault(str(location_id), []).append((week, status))

    max_schedules = settings.FREE_PLAN_SCHEDULES_PER_LOCATION
    max_attempts = settings.FREE_PLAN_ATTEMPTS_PER_LOCATION

    result: dict[str, LocationQuota] = {}
    for raw_id in location_ids:
        location_id = str(raw_id)
        attempts = by_location.get(location_id, [])
        held = [(w, s) for w, s in attempts if s != DISCARDED]

        if len(held) >= max_schedules:
            result[location_id] = LocationQuota(
                location_id=location_id,
                allowed=False,
                code="location_scheduled_this_month",
                message=(
                    "The free plan covers one week per location per month, "
                    "and this location already has a schedule for this "
                    "month. Upgrade to schedule it again."
                ),
                required_week=None,
                attempts_used=len(attempts),
                attempts_max=max_attempts,
            )
            continue

        if len(attempts) >= max_attempts:
            result[location_id] = LocationQuota(
                location_id=location_id,
                allowed=False,
                code="location_retries_exhausted",
                message=(
                    f"The free plan allows {max_attempts} generation "
                    f"attempts per location per month, and this location "
                    f"has used them. Upgrade to keep generating."
                ),
                required_week=None,
                attempts_used=len(attempts),
                attempts_max=max_attempts,
            )
            continue

        if not attempts:
            result[location_id] = _allow(location_id, attempts=0)
            continue

        # Mid-retry: every attempt so far was rejected, so the slot is free
        # again — but only for the week that was thrown away.
        required = attempts[0][0]
        required_iso = required.isoformat() if hasattr(required, "isoformat") else str(required)

        if week_start_date is not None and week_start_date != required_iso:
            result[location_id] = LocationQuota(
                location_id=location_id,
                allowed=False,
                code="retry_week_mismatch",
                message=(
                    f"This location's remaining free generation is a retry "
                    f"of the week starting {required_iso}. Generate that "
                    f"week again, or upgrade to schedule a different one."
                ),
                required_week=required_iso,
                attempts_used=len(attempts),
                attempts_max=max_attempts,
            )
            continue

        quota = _allow(location_id, attempts=len(attempts))
        quota["required_week"] = required_iso
        result[location_id] = quota

    return result


async def all_locations_for_company(
    db: AsyncSession, company_id: str
) -> list[str]:
    """Every location id in *company_id*. Used for the up-front check."""
    rows = (await db.execute(
        select(Location.id).where(Location.company_id == company_id)
    )).scalars().all()
    return [str(r) for r in rows]


async def free_plan_usage(db: AsyncSession, og_id: str) -> tuple[int, int]:
    """(locations holding a schedule this month, locations in the group).

    The pair the banner and the quota strip both report. Expressed in
    locations because that is the unit the allowance is now counted in —
    "1 of 2 locations scheduled this month" is a sentence a manager can act
    on, where the old pooled row count was not.

    The shared public demo stays on the POOLED model it always had. It is
    one tenant generated against by every visitor, so a per-location cap
    would spend it within minutes of each month turning over, while the
    raised pooled cap is what actually stops it being spammed. Two models
    is a wart, but the demo is a genuinely different thing: nobody is
    evaluating whether to upgrade it.
    """
    if is_demo_group(og_id):
        return (
            await count_schedules_this_month(db, og_id),
            settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH,
        )

    company_ids = await _get_company_ids_for_group(db, og_id)
    if not company_ids:
        return (0, 0)

    location_ids = (await db.execute(
        select(Location.id).where(Location.company_id.in_(company_ids))
    )).scalars().all()
    total = len(location_ids)
    if not total:
        return (0, 0)

    rows = (await db.execute(
        select(ShiftSchedule.location_id).where(
            ShiftSchedule.company_id.in_(company_ids),
            ShiftSchedule.created_at >= _month_start(),
            ShiftSchedule.status != DISCARDED,
        ).distinct()
    )).scalars().all()

    held = {str(r) for r in rows} & {str(lid) for lid in location_ids}
    return (len(held), total * settings.FREE_PLAN_SCHEDULES_PER_LOCATION)


async def any_location_available(
    db: AsyncSession, company_id: str, week_start_date: str | None = None
) -> bool:
    """True if at least one of the company's locations may still generate.

    The up-front check for the generate route: if every location is spent
    there is nothing to stream, and the caller deserves a single clear 402
    rather than a stream of per-location refusals.
    """
    og_id = await get_ownership_group_id(db, str(company_id))
    if og_id and is_demo_group(og_id):
        # Demo keeps the pooled cap — see free_plan_usage.
        used = await count_schedules_this_month(db, og_id)
        return used < settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH

    location_ids = await all_locations_for_company(db, company_id)
    if not location_ids:
        # No locations configured. Not a quota problem — let the pipeline
        # return its ordinary empty result rather than a payment error.
        return True
    quota = await resolve_location_quota(
        db, company_id, location_ids, week_start_date
    )
    return any(q["allowed"] for q in quota.values())
