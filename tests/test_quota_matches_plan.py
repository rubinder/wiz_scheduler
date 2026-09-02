"""The quota strip and the plan banner must report the same number.

The Schedule page renders two figures: check_schedule_quota's
schedules_included ("Schedules this month: X / Y") and get_plan_state's
schedules.limit ("... N of M generations this month"). They used to come from
unrelated sources, so a free tenant saw 0/50 against 0/5 and the demo saw
0/250 against 0/50.

Both now resolve through the same source for free groups —
location_quota.free_plan_usage, which counts LOCATIONS holding a schedule
this month. These tests pin that agreement so the two cannot drift apart
again.
"""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, ShiftSchedule
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import check_schedule_quota
from backend.services.plan import get_plan_state
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _make_og(db: AsyncSession, og_id: str, *, paid: bool = False,
                   credits: float = 0.0) -> str:
    db.add(OwnershipGroup(
        id=og_id, name="G", ai_credits_usd=credits,
        stripe_subscription_id="sub_live" if paid else None,
    ))
    await db.flush()
    company_id = _id()
    db.add(Company(id=company_id, name="C", slug=_id(),
                   ownership_group_id=og_id))
    await db.commit()
    return company_id


async def _add_schedules(db: AsyncSession, company_id: str, n: int) -> None:
    for _ in range(n):
        db.add(ShiftSchedule(
            id=_id(), company_id=company_id, location_id=_id(),
            week_start_date=date(2026, 8, 10), status="draft",
        ))
    await db.commit()


async def test_free_tenant_sees_one_number(db_session: AsyncSession):
    """The reported bug, for an ordinary free tenant: strip said 50, banner
    said 5."""
    company_id = await _make_og(db_session, _id())

    quota = await check_schedule_quota(db_session, company_id)
    plan = await get_plan_state(db_session, company_id)

    # The agreement is the point, whatever the number happens to be.
    assert quota["schedules_included"] == plan["schedules"]["limit"]
    assert quota["schedules_used"] == plan["schedules"]["count"]


async def test_demo_tenant_sees_one_number(db_session: AsyncSession):
    """The reported bug as seen on the demo: strip said 250, banner said 50."""
    company_id = await _make_og(db_session, settings.DEMO_OWNERSHIP_GROUP_ID)

    quota = await check_schedule_quota(db_session, company_id)
    plan = await get_plan_state(db_session, company_id)

    assert quota["schedules_included"] == plan["schedules"]["limit"]
    assert quota["schedules_included"] == settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH
    assert quota["schedules_included"] == 50


async def test_paid_tenant_keeps_metered_threshold(db_session: AsyncSession):
    """Paid groups meter against INCLUDED_SCHEDULES_PER_MONTH and pay overage past it.
    That behaviour is unchanged."""
    company_id = await _make_og(db_session, _id(), paid=True)

    quota = await check_schedule_quota(db_session, company_id)
    assert quota["schedules_included"] == settings.INCLUDED_SCHEDULES_PER_MONTH
    assert quota["plan"] == "paid"

    plan = await get_plan_state(db_session, company_id)
    assert plan["schedules"]["limit"] is None  # unlimited; metered, not capped


async def test_free_tenant_marked_free_for_the_ui(db_session: AsyncSession):
    """The UI needs this to offer Upgrade instead of Buy credits."""
    company_id = await _make_og(db_session, _id())
    quota = await check_schedule_quota(db_session, company_id)
    assert quota["plan"] == "free"


async def test_credits_do_not_unblock_a_free_tenant(db_session: AsyncSession):
    """Credits are a paid-plan overage mechanism. check_can_generate raises
    schedule_limit_reached on the plan cap regardless of balance, so reporting
    can_generate=True here would let the UI sell credits that buy nothing."""
    from backend.models import Location, Region

    company_id = await _make_og(db_session, _id(), credits=25.0)

    # Give the tenant a location and spend its one free week, so this is a
    # genuinely over-allowance free group rather than one with nothing to
    # schedule.
    region_id, location_id = _id(), _id()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    db_session.add(Location(id=location_id, company_id=company_id,
                            region_id=region_id, name="L",
                            timezone="America/New_York"))
    await db_session.flush()
    db_session.add(ShiftSchedule(
        id=_id(), company_id=company_id, location_id=location_id,
        week_start_date=date(2026, 8, 10), status="draft",
    ))
    await db_session.commit()

    quota = await check_schedule_quota(db_session, company_id)
    assert quota["is_over_included"] is True
    assert quota["purchased_credits_usd"] == 25.0
    assert quota["can_generate"] is False


async def test_credits_still_unblock_a_paid_tenant(db_session: AsyncSession):
    """The paid overage path must be untouched by the fix above."""
    company_id = await _make_og(db_session, _id(), paid=True, credits=25.0)
    await _add_schedules(db_session, company_id, settings.INCLUDED_SCHEDULES_PER_MONTH)

    quota = await check_schedule_quota(db_session, company_id)
    assert quota["is_over_included"] is True
    assert quota["can_generate"] is True


async def test_demo_agreement_holds_at_the_cap(db_session: AsyncSession):
    """Both figures still agree once the demo's allowance is spent."""
    company_id = await _make_og(db_session, settings.DEMO_OWNERSHIP_GROUP_ID)
    await _add_schedules(
        db_session, company_id, settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH
    )

    quota = await check_schedule_quota(db_session, company_id)
    plan = await get_plan_state(db_session, company_id)

    assert quota["schedules_included"] == plan["schedules"]["limit"]
    assert quota["schedules_used"] == plan["schedules"]["count"]
    assert quota["is_over_included"] is True
    assert plan["block_reason"] == "schedule_limit_reached"


async def test_no_tenant_slug_is_hardcoded(db_session: AsyncSession):
    """The demo exception keys off DEMO_OWNERSHIP_GROUP_ID, not a company
    slug. A company that happens to use the old demo slug gets nothing."""
    og_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    company_id = _id()
    db_session.add(Company(id=company_id, name="Acme Corp", slug="acme-corp",
                           ownership_group_id=og_id))
    await db_session.commit()

    quota = await check_schedule_quota(db_session, company_id)
    # Ordinary free rules, never the demo's raised pooled cap.
    assert quota["schedules_included"] != settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH
    assert quota["schedules_included"] != 250
