"""Tests for backend/services/plan.py and free-plan counting helpers."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import count_locations_for_group
from backend.services.plan import get_plan_state
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


OG_ID = _id()


@pytest_asyncio.fixture
async def og_with_two_companies(db_session: AsyncSession) -> OwnershipGroup:
    """An OG spanning two companies — the shape most likely to hide an
    off-by-one that leaks free capacity."""
    og = OwnershipGroup(id=OG_ID, name="Group")
    db_session.add(og)
    await db_session.flush()

    c1 = Company(id=_id(), name="C1", slug=_id(), ownership_group_id=OG_ID)
    c2 = Company(id=_id(), name="C2", slug=_id(), ownership_group_id=OG_ID)
    db_session.add_all([c1, c2])
    await db_session.flush()

    r1 = Region(id=_id(), company_id=c1.id, name="R1")
    r2 = Region(id=_id(), company_id=c2.id, name="R2")
    db_session.add_all([r1, r2])
    await db_session.flush()

    db_session.add_all([
        Location(id=_id(), company_id=c1.id, region_id=r1.id,
                 name="L1", timezone="UTC"),
        Location(id=_id(), company_id=c2.id, region_id=r2.id,
                 name="L2", timezone="UTC"),
    ])
    await db_session.commit()
    return og


async def test_count_locations_spans_all_companies_in_group(
    db_session: AsyncSession, og_with_two_companies: OwnershipGroup
):
    assert await count_locations_for_group(db_session, OG_ID) == 2


async def test_count_locations_unknown_group_is_zero(db_session: AsyncSession):
    assert await count_locations_for_group(db_session, _id()) == 0


async def test_count_locations_excludes_other_groups(
    db_session: AsyncSession, og_with_two_companies: OwnershipGroup
):
    """A naive implementation that counted every location in the database
    would pass the additive/empty-group tests above but leak capacity
    across ownership groups. Prove isolation explicitly."""
    other_og_id = _id()
    db_session.add(OwnershipGroup(id=other_og_id, name="Other Group"))
    await db_session.flush()

    other_company_id = _id()
    db_session.add(Company(id=other_company_id, name="Other Co", slug=_id(),
                           ownership_group_id=other_og_id))
    await db_session.flush()

    other_region_id = _id()
    db_session.add(Region(id=other_region_id, company_id=other_company_id, name="OR"))
    await db_session.flush()

    db_session.add(Location(id=_id(), company_id=other_company_id,
                            region_id=other_region_id, name="OL", timezone="UTC"))
    await db_session.commit()

    assert await count_locations_for_group(db_session, OG_ID) == 2


@pytest_asyncio.fixture
async def free_og(db_session: AsyncSession) -> tuple[str, str]:
    """Returns (og_id, company_id) for a free OG with no Stripe subscription."""
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Free Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Free Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return og_id, company_id


async def test_no_ownership_group_is_unlimited(db_session: AsyncSession):
    """Seed/dev companies without an OG must not be capped — this is what
    keeps the existing test suite and seed.py working."""
    company_id = _id()
    db_session.add(Company(id=company_id, name="Orphan", slug=_id()))
    await db_session.commit()

    state = await get_plan_state(db_session, company_id)
    assert state["plan"] == "paid"
    assert state["over_limit"] is False
    assert state["can_generate_ai"] is True


async def test_og_without_subscription_is_free(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    _, company_id = free_og
    state = await get_plan_state(db_session, company_id)
    assert state["plan"] == "free"
    assert state["employees"]["limit"] == settings.FREE_PLAN_MAX_EMPLOYEES
    assert state["locations"]["limit"] == settings.FREE_PLAN_MAX_LOCATIONS
    assert state["can_generate_local"] is True
    assert state["can_generate_ai"] is False
    assert state["block_reason"] is None


async def test_active_subscription_is_paid(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    og_id, company_id = free_og
    og = await db_session.get(OwnershipGroup, og_id)
    og.stripe_subscription_id = "sub_123"
    await db_session.commit()

    state = await get_plan_state(db_session, company_id)
    assert state["plan"] == "paid"
    assert state["employees"]["limit"] is None
    assert state["can_generate_ai"] is True


async def test_canceled_subscription_is_free(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    og_id, company_id = free_og
    og = await db_session.get(OwnershipGroup, og_id)
    og.stripe_subscription_id = "sub_123"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    state = await get_plan_state(db_session, company_id)
    assert state["plan"] == "free"
    assert state["canceled_at"] is not None


async def test_downgraded_over_limit_blocks_all_generation(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    """Only reachable by a paid OG that grew past the limits then canceled."""
    og_id, company_id = free_og
    og = await db_session.get(OwnershipGroup, og_id)
    og.stripe_subscription_id = "sub_123"
    og.canceled_at = datetime.now(timezone.utc)
    for i in range(settings.FREE_PLAN_MAX_EMPLOYEES + 1):  # one over the cap
        db_session.add(Employee(id=_id(), company_id=company_id,
                                full_name=f"E{i}"))
    await db_session.commit()

    state = await get_plan_state(db_session, company_id)
    assert state["over_limit"] is True
    assert state["can_generate_local"] is False
    assert state["can_generate_ai"] is False
    assert state["block_reason"] == "subscription_canceled"


async def test_free_over_limit_block_reason(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    _, company_id = free_og
    for i in range(settings.FREE_PLAN_MAX_EMPLOYEES + 1):  # one over the cap
        db_session.add(Employee(id=_id(), company_id=company_id,
                                full_name=f"E{i}"))
    await db_session.commit()

    state = await get_plan_state(db_session, company_id)
    assert state["over_limit"] is True
    assert state["can_generate_local"] is False
    assert state["block_reason"] == "plan_limit_exceeded"


from fastapi import HTTPException

from backend.config import settings
from backend.services.plan import assert_can_add


async def test_assert_can_add_allows_up_to_limit(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    _, company_id = free_og
    for i in range(settings.FREE_PLAN_MAX_EMPLOYEES - 1):
        db_session.add(Employee(id=_id(), company_id=company_id,
                                full_name=f"E{i}"))
    await db_session.commit()

    # (cap - 1) existing + 1 == cap, exactly at the limit.
    await assert_can_add(db_session, company_id, employees=1)


async def test_assert_can_add_refuses_over_limit(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    _, company_id = free_og
    for i in range(settings.FREE_PLAN_MAX_EMPLOYEES):
        db_session.add(Employee(id=_id(), company_id=company_id,
                                full_name=f"E{i}"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await assert_can_add(db_session, company_id, employees=1)

    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "plan_limit_exceeded"
    assert exc.value.detail["limit"] == "employees"
    assert exc.value.detail["max"] == settings.FREE_PLAN_MAX_EMPLOYEES
    assert exc.value.detail["current"] == settings.FREE_PLAN_MAX_EMPLOYEES
    assert exc.value.detail["attempted"] == 1


async def test_assert_can_add_refuses_oversized_bulk(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    """An upload larger than the whole cap is refused whole."""
    _, company_id = free_og
    oversized = settings.FREE_PLAN_MAX_EMPLOYEES + 7
    with pytest.raises(HTTPException) as exc:
        await assert_can_add(db_session, company_id, employees=oversized)
    assert exc.value.detail["attempted"] == oversized
    assert exc.value.detail["current"] == 0


async def test_assert_can_add_location_past_the_cap_refused(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    og_id, company_id = free_og
    region_id = _id()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    for i in range(settings.FREE_PLAN_MAX_LOCATIONS):
        db_session.add(Location(id=_id(), company_id=company_id,
                                region_id=region_id, name=f"L{i}",
                                timezone="UTC"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await assert_can_add(db_session, company_id, locations=1)
    assert exc.value.detail["limit"] == "locations"
    assert exc.value.detail["max"] == settings.FREE_PLAN_MAX_LOCATIONS


async def test_assert_can_add_noop_when_paid(
    db_session: AsyncSession, free_og: tuple[str, str]
):
    og_id, company_id = free_og
    og = await db_session.get(OwnershipGroup, og_id)
    og.stripe_subscription_id = "sub_123"
    for i in range(50):
        db_session.add(Employee(id=_id(), company_id=company_id,
                                full_name=f"E{i}"))
    await db_session.commit()

    await assert_can_add(db_session, company_id, employees=100)


async def test_assert_can_add_noop_without_ownership_group(
    db_session: AsyncSession
):
    company_id = _id()
    db_session.add(Company(id=company_id, name="Orphan", slug=_id()))
    await db_session.commit()
    await assert_can_add(db_session, company_id, employees=100)
