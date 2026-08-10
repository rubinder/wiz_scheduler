"""Tests for backend/services/plan.py and free-plan counting helpers."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import count_locations_for_group
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
