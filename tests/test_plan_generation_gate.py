"""The five rows of the generation decision table."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, ShiftSchedule, User
from backend.models.ownership_group import OwnershipGroup
from backend.config import settings
from backend.services.plan import check_can_generate, get_plan_state
from fastapi import HTTPException
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    """A free tenant with the two locations the free plan allows.

    Real Location rows matter now: the generation allowance is counted per
    location, so a tenant with none has nothing to spend and nothing to
    block. Under the old pooled row count the location rows were irrelevant
    and these tests did not create any.
    """
    from backend.models import Location, Region

    og_id, company_id, region_id = _id(), _id(), _id()
    loc_a, loc_b = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    for lid, name in ((loc_a, "A"), (loc_b, "B")):
        db_session.add(Location(id=lid, company_id=company_id,
                                region_id=region_id, name=name,
                                timezone="America/New_York"))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id,
            "locations": [loc_a, loc_b]}


async def _make_over_limit(db: AsyncSession, company_id: str) -> None:
    for i in range(settings.FREE_PLAN_MAX_EMPLOYEES + 1):  # one over the cap
        db.add(Employee(id=_id(), company_id=company_id, full_name=f"E{i}"))
    await db.commit()


async def test_paid_can_generate_both(db_session: AsyncSession, tenant: dict):
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    await db_session.commit()

    await check_can_generate(db_session, tenant["company_id"], use_local=True)
    await check_can_generate(db_session, tenant["company_id"], use_local=False)


async def test_free_within_limit_local_ok_ai_blocked(
    db_session: AsyncSession, tenant: dict
):
    await check_can_generate(db_session, tenant["company_id"], use_local=True)

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=False)
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "ai_requires_paid_plan"


async def test_free_over_limit_blocks_local_too(
    db_session: AsyncSession, tenant: dict
):
    await _make_over_limit(db_session, tenant["company_id"])

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=True)
    assert exc.value.detail["code"] == "plan_limit_exceeded"


async def test_canceled_within_limit_local_ok(
    db_session: AsyncSession, tenant: dict
):
    """Cancel -> free: a shrunken tenant keeps working."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    await check_can_generate(db_session, tenant["company_id"], use_local=True)

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=False)
    assert exc.value.detail["code"] == "ai_requires_paid_plan"


async def test_canceled_over_limit_keeps_grace_block(
    db_session: AsyncSession, tenant: dict
):
    """The downgraded over-limit state — today's 90-day read-only grace."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()
    await _make_over_limit(db_session, tenant["company_id"])

    for use_local in (True, False):
        with pytest.raises(HTTPException) as exc:
            await check_can_generate(
                db_session, tenant["company_id"], use_local=use_local
            )
        assert exc.value.detail["code"] == "subscription_canceled"


async def test_no_ownership_group_can_generate(db_session: AsyncSession):
    company_id = _id()
    db_session.add(Company(id=company_id, name="Orphan", slug=_id()))
    await db_session.commit()

    await check_can_generate(db_session, company_id, use_local=True)
    await check_can_generate(db_session, company_id, use_local=False)


async def _add_schedules(
    db: AsyncSession,
    company_id: str,
    n: int,
    location_ids: list[str],
    status: str = "draft",
) -> None:
    from datetime import date
    for _ in range(n):
        db.add(ShiftSchedule(
            id=_id(),
            company_id=company_id,
            location_id=location_ids[_ % len(location_ids)],
            week_start_date=date(2026, 8, 10),
            status=status,
        ))
    await db.commit()


async def test_free_under_generation_cap_can_generate_local(
    db_session: AsyncSession, tenant: dict
):
    await _add_schedules(db_session, tenant["company_id"], 1, [tenant["locations"][0]])
    # Location B is untouched, so generation is still possible.
    await check_can_generate(db_session, tenant["company_id"], use_local=True)


async def test_free_at_generation_cap_blocks_local(
    db_session: AsyncSession, tenant: dict
):
    # Every location spent — this is what the coarse gate fires on.
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=True)
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "schedule_limit_reached"
    assert exc.value.detail["used"] == 2
    assert exc.value.detail["max"] == 2


async def test_free_at_generation_cap_blocks_ai_too(
    db_session: AsyncSession, tenant: dict
):
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=False)
    assert exc.value.detail["code"] == "schedule_limit_reached"


async def test_paid_is_not_subject_to_free_generation_cap(
    db_session: AsyncSession, tenant: dict
):
    """Paid keeps INCLUDED_SCHEDULES_PER_MONTH=50-then-metered; the free cap must not apply."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    await db_session.commit()
    await _add_schedules(db_session, tenant["company_id"], 20, tenant["locations"])

    await check_can_generate(db_session, tenant["company_id"], use_local=True)
    await check_can_generate(db_session, tenant["company_id"], use_local=False)


async def test_over_limit_takes_precedence_over_generation_cap(
    db_session: AsyncSession, tenant: dict
):
    """A downgraded, over-limit tenant is told about the seat limit, not the cap."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()
    await _make_over_limit(db_session, tenant["company_id"])
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=True)
    assert exc.value.detail["code"] == "subscription_canceled"


async def test_plan_state_reports_schedule_usage(
    db_session: AsyncSession, tenant: dict
):
    """Usage is now reported in LOCATIONS: how many hold a schedule this
    month, out of how many could. "1 of 2 locations scheduled" is a sentence
    a manager can act on; the old pooled row count was not."""
    await _add_schedules(db_session, tenant["company_id"], 1, [tenant["locations"][0]])
    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["schedules"]["count"] == 1
    assert state["schedules"]["limit"] == 2


async def test_plan_state_block_reason_schedule_limit_reached(
    db_session: AsyncSession, tenant: dict
):
    """A free tenant that is NOT over_limit but has exhausted the monthly
    generation cap must get block_reason="schedule_limit_reached" (final-
    review FIX 6), not None. PlanBanner otherwise falls back to its
    "AI requires a paid plan" copy for any null block_reason, which is
    wrong for a tenant well within employee/location limits who simply
    used up their free generations this month."""
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])
    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["over_limit"] is False
    assert state["block_reason"] == "schedule_limit_reached"


async def test_plan_state_block_reason_none_under_generation_cap(
    db_session: AsyncSession, tenant: dict
):
    """Sanity check on the other side of the boundary: still under the cap,
    still no block_reason."""
    await _add_schedules(db_session, tenant["company_id"], 1, [tenant["locations"][0]])
    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["block_reason"] is None


async def test_plan_state_over_limit_block_reason_unaffected_by_generation_cap(
    db_session: AsyncSession, tenant: dict
):
    """over_limit must still win: a downgraded, over-limit tenant who has
    ALSO exhausted the generation cap is told about the seat limit, not the
    generation cap — over_limit itself and its precedence are unchanged by
    FIX 6."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()
    await _make_over_limit(db_session, tenant["company_id"])
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])

    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["over_limit"] is True
    assert state["block_reason"] == "subscription_canceled"


async def test_one_run_at_two_locations_no_longer_spends_the_whole_allowance(
    db_session: AsyncSession, tenant: dict
):
    """The rows-vs-runs problem, resolved.

    Generation writes one ShiftSchedule row PER LOCATION PER RUN, and the old
    allowance was a pooled count of ROWS. So a free tenant with two locations
    spent its whole month on a SINGLE run while a one-location tenant got two
    runs — the same number meaning different things to different tenants.
    config.py carried a standing WARNING about it and the test that used to
    live here pinned the behaviour so it would fail the day someone fixed it.

    This is that day. The allowance is now per location, so one run across
    two locations spends one week at each, which is exactly what it should
    cost.
    """
    assert settings.FREE_PLAN_MAX_LOCATIONS == 2
    assert settings.FREE_PLAN_SCHEDULES_PER_LOCATION == 1

    # One run across both locations = one row each.
    await _add_schedules(db_session, tenant["company_id"], 2, tenant["locations"])

    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["schedules"]["count"] == 2
    assert state["schedules"]["limit"] == 2
    assert state["block_reason"] == "schedule_limit_reached"


async def test_spending_one_location_leaves_the_other_generatable(
    db_session: AsyncSession, tenant: dict
):
    """The property the pooled counter could not express."""
    await _add_schedules(db_session, tenant["company_id"], 1, [tenant["locations"][0]])

    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["schedules"]["count"] == 1
    assert state["block_reason"] is None

    # Not blocked: location B has not been scheduled.
    await check_can_generate(db_session, tenant["company_id"], use_local=True)


async def test_a_rejected_schedule_leaves_the_gate_open(
    db_session: AsyncSession, tenant: dict
):
    """Rejecting frees the slot, so the coarse gate must not fire on a
    tenant whose only schedules were thrown away — they still have a retry.
    The aggregate count alone would read "full" here."""
    await _add_schedules(
        db_session, tenant["company_id"], 2, tenant["locations"], status="rejected"
    )

    await check_can_generate(db_session, tenant["company_id"], use_local=True)

    state = await get_plan_state(db_session, tenant["company_id"])
    assert state["schedules"]["count"] == 0
    assert state["block_reason"] is None
