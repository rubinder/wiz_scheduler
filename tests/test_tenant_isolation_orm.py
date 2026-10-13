"""Tests for automatic ORM tenant filtering via context variables.

Tests that the tenant_scope_disabled escape hatch works, and that tenants
cannot accidentally see each other's data through ORM queries.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.company import Company
from backend.models.ownership_group import OwnershipGroup
from backend.models.region import Region
from backend.models.location import Location
from backend.models.role import Role
from backend.tenant import set_tenant_context, tenant_scope_disabled


@pytest.mark.asyncio
async def test_tenant_filtering_blocks_cross_tenant_reads(db: AsyncSession) -> None:
    """Verify that ORM queries are filtered by company_id when tenant context is set."""
    og_a = OwnershipGroup(name="Org A")
    og_b = OwnershipGroup(name="Org B")
    db.add_all([og_a, og_b])
    await db.flush()

    company_a = Company(name="Company A", slug="company-a", ownership_group_id=og_a.id)
    company_b = Company(name="Company B", slug="company-b", ownership_group_id=og_b.id)
    db.add_all([company_a, company_b])
    await db.flush()

    region_a = Region(name="Region A", company_id=company_a.id)
    region_b = Region(name="Region B", company_id=company_b.id)
    db.add_all([region_a, region_b])
    await db.flush()

    location_a = Location(name="Loc A", company_id=company_a.id, region_id=region_a.id)
    location_b = Location(name="Loc B", company_id=company_b.id, region_id=region_b.id)
    db.add_all([location_a, location_b])
    await db.flush()

    set_tenant_context([company_a.id])

    result = await db.execute(select(Location))
    locations = list(result.scalars().all())

    assert len(locations) == 1
    assert locations[0].id == location_a.id


@pytest.mark.asyncio
async def test_tenant_escape_hatch_allows_cross_tenant_reads(db: AsyncSession) -> None:
    """Verify that tenant_scope_disabled escape hatch bypasses filtering."""
    og_a = OwnershipGroup(name="Org A")
    og_b = OwnershipGroup(name="Org B")
    db.add_all([og_a, og_b])
    await db.flush()

    company_a = Company(name="Company A", slug="company-a", ownership_group_id=og_a.id)
    company_b = Company(name="Company B", slug="company-b", ownership_group_id=og_b.id)
    db.add_all([company_a, company_b])
    await db.flush()

    region_a = Region(name="Region A", company_id=company_a.id)
    region_b = Region(name="Region B", company_id=company_b.id)
    db.add_all([region_a, region_b])
    await db.flush()

    location_a = Location(name="Loc A", company_id=company_a.id, region_id=region_a.id)
    location_b = Location(name="Loc B", company_id=company_b.id, region_id=region_b.id)
    db.add_all([location_a, location_b])
    await db.flush()

    set_tenant_context([company_a.id])

    with tenant_scope_disabled():
        result = await db.execute(select(Location))
        locations = list(result.scalars().all())

    assert len(locations) == 2


@pytest.mark.asyncio
async def test_multi_tenant_filtering(db: AsyncSession) -> None:
    """Verify filtering works with multiple tenants in the context."""
    og_a = OwnershipGroup(name="Org A")
    og_b = OwnershipGroup(name="Org B")
    og_c = OwnershipGroup(name="Org C")
    db.add_all([og_a, og_b, og_c])
    await db.flush()

    company_a = Company(name="Company A", slug="company-a", ownership_group_id=og_a.id)
    company_b = Company(name="Company B", slug="company-b", ownership_group_id=og_b.id)
    company_c = Company(name="Company C", slug="company-c", ownership_group_id=og_c.id)
    db.add_all([company_a, company_b, company_c])
    await db.flush()

    region_a = Region(name="Region A", company_id=company_a.id)
    region_b = Region(name="Region B", company_id=company_b.id)
    region_c = Region(name="Region C", company_id=company_c.id)
    db.add_all([region_a, region_b, region_c])
    await db.flush()

    location_a = Location(name="Loc A", company_id=company_a.id, region_id=region_a.id)
    location_b = Location(name="Loc B", company_id=company_b.id, region_id=region_b.id)
    location_c = Location(name="Loc C", company_id=company_c.id, region_id=region_c.id)
    db.add_all([location_a, location_b, location_c])
    await db.flush()

    set_tenant_context([company_a.id, company_b.id])

    result = await db.execute(select(Location).order_by(Location.id))
    locations = list(result.scalars().all())

    assert len(locations) == 2
    assert {loc.id for loc in locations} == {location_a.id, location_b.id}
