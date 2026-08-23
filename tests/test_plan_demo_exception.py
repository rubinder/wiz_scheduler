"""The public demo tenant's raised generation cap.

The demo group is a free-plan group with no Stripe subscription, but it is
shared by every visitor, so the ordinary 2/month allowance is spent almost at
once. It gets a raised generation cap and nothing else: the location and
employee caps still apply, so the demo keeps showing free-plan shape.
"""

from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, Location, Region, ShiftSchedule
from backend.models.ownership_group import OwnershipGroup
from backend.services.plan import check_can_generate, get_plan_state
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def demo_tenant(db_session: AsyncSession) -> dict:
    """An OG whose id matches settings.DEMO_OWNERSHIP_GROUP_ID."""
    og_id = settings.DEMO_OWNERSHIP_GROUP_ID
    company_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="Demo Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Demo Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id}


@pytest_asyncio.fixture
async def plain_tenant(db_session: AsyncSession) -> dict:
    """An ordinary free OG, for contrast."""
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Plain Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Plain Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id}


async def _add_schedules(db: AsyncSession, company_id: str, n: int) -> None:
    for _ in range(n):
        db.add(ShiftSchedule(
            id=_id(),
            company_id=company_id,
            location_id=_id(),
            week_start_date=date(2026, 8, 10),
            status="draft",
        ))
    await db.commit()


async def test_demo_group_gets_raised_generation_cap(
    db_session: AsyncSession, demo_tenant: dict
):
    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["plan"] == "free"
    assert state["schedules"]["limit"] == settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH
    assert state["schedules"]["limit"] == 50


async def test_ordinary_free_group_keeps_the_normal_cap(
    db_session: AsyncSession, plain_tenant: dict
):
    """The exception must not leak to every free tenant."""
    state = await get_plan_state(db_session, plain_tenant["company_id"])
    assert state["schedules"]["limit"] == settings.FREE_PLAN_MAX_SCHEDULES_PER_MONTH
    # And it is genuinely the ordinary cap, not the demo one.
    assert state["schedules"]["limit"] != settings.DEMO_PLAN_MAX_SCHEDULES_PER_MONTH


async def test_demo_generates_past_the_ordinary_cap(
    db_session: AsyncSession, demo_tenant: dict
):
    """At 5 used, an ordinary free tenant is blocked; the demo is not."""
    await _add_schedules(db_session, demo_tenant["company_id"], 5)
    await check_can_generate(db_session, demo_tenant["company_id"], use_local=True)

    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["block_reason"] is None


async def test_demo_blocks_at_its_own_cap(
    db_session: AsyncSession, demo_tenant: dict
):
    await _add_schedules(db_session, demo_tenant["company_id"], 50)

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(
            db_session, demo_tenant["company_id"], use_local=True
        )
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "schedule_limit_reached"
    assert exc.value.detail["used"] == 50
    assert exc.value.detail["max"] == 50


async def test_demo_still_has_no_ai(db_session: AsyncSession, demo_tenant: dict):
    """The raised cap covers local runs only — AI stays paid-only."""
    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["can_generate_ai"] is False

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(
            db_session, demo_tenant["company_id"], use_local=False
        )
    assert exc.value.detail["code"] == "ai_requires_paid_plan"


async def test_demo_still_capped_on_employees(
    db_session: AsyncSession, demo_tenant: dict
):
    """Only the generation cap is lifted. Going over the employee cap must
    still set over_limit, or the demo would stop representing the free plan."""
    for _ in range(settings.FREE_PLAN_MAX_EMPLOYEES + 1):
        db_session.add(Employee(
            id=_id(),
            company_id=demo_tenant["company_id"],
            full_name="E",
            email=f"{_id()}@example.com",
        ))
    await db_session.commit()

    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["over_limit"] is True
    assert state["block_reason"] == "plan_limit_exceeded"
    assert state["can_generate_local"] is False


async def test_demo_still_capped_on_locations(
    db_session: AsyncSession, demo_tenant: dict
):
    region_id = _id()
    db_session.add(Region(
        id=region_id, company_id=demo_tenant["company_id"], name="R"
    ))
    await db_session.flush()
    for _ in range(settings.FREE_PLAN_MAX_LOCATIONS + 1):
        db_session.add(Location(
            id=_id(),
            company_id=demo_tenant["company_id"],
            region_id=region_id,
            name="L",
            timezone="America/New_York",
        ))
    await db_session.commit()

    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["over_limit"] is True


async def test_exception_can_be_disabled(
    db_session: AsyncSession, demo_tenant: dict, monkeypatch
):
    """Empty DEMO_OWNERSHIP_GROUP_ID turns the exception off, so the demo
    group falls back to the ordinary free cap."""
    monkeypatch.setattr(settings, "DEMO_OWNERSHIP_GROUP_ID", "")

    state = await get_plan_state(db_session, demo_tenant["company_id"])
    assert state["schedules"]["limit"] == settings.FREE_PLAN_MAX_SCHEDULES_PER_MONTH
