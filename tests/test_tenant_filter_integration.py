"""The ORM tenant filter limits reads to the request's companies.

Each check reads through a fresh session: an object already in a session's
identity map is returned by ``session.get`` without any SQL, which would
hide a missing filter.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Location, Region
from backend.tenant import tenant_context
from tests.conftest import _id, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def two_tenants(db_session: AsyncSession) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for name in ("mine", "theirs"):
        company_id, region_id, location_id = _id(), _id(), _id()
        db_session.add(Company(id=company_id, name=name, slug=_id()))
        await db_session.flush()
        db_session.add(Region(id=region_id, company_id=company_id, name=f"{name} region"))
        await db_session.flush()
        db_session.add(Location(
            id=location_id, company_id=company_id, region_id=region_id,
            name=f"{name} store", timezone="UTC",
        ))
        rows[name] = {"company": company_id, "region": region_id, "location": location_id}
    await db_session.commit()
    return rows


async def _as_tenant(company_ids: list[str] | None, read):
    token = tenant_context.set(company_ids)
    try:
        async with get_test_session_factory()() as session:
            return await read(session)
    finally:
        tenant_context.reset(token)


async def test_select_returns_only_the_tenants_rows(two_tenants) -> None:
    mine, theirs = two_tenants["mine"], two_tenants["theirs"]
    ids = await _as_tenant(
        [mine["company"]],
        lambda s: s.execute(
            select(Region.id).where(Region.id.in_([mine["region"], theirs["region"]]))
        ),
    )
    assert ids.scalars().all() == [mine["region"]]


async def test_get_by_primary_key_misses_another_tenants_row(two_tenants) -> None:
    theirs = two_tenants["theirs"]
    got = await _as_tenant(
        [two_tenants["mine"]["company"]], lambda s: s.get(Region, theirs["region"]),
    )
    assert got is None


async def test_joined_tables_are_filtered_too(two_tenants) -> None:
    """A join reaches a second tenant table; the criteria apply to it as well."""
    mine, theirs = two_tenants["mine"], two_tenants["theirs"]

    async def read(session):
        # A location of ours joined to *their* region by id: the region side
        # must be filtered out, so the join yields nothing.
        return (await session.execute(
            select(Location.id)
            .join(Region, Region.id == theirs["region"])
            .where(Location.id == mine["location"])
        )).scalars().all()

    assert await _as_tenant([mine["company"]], read) == []


async def test_no_tenant_context_reads_everything(two_tenants) -> None:
    """Background jobs and unauthenticated routes run without a tenant."""
    mine, theirs = two_tenants["mine"], two_tenants["theirs"]
    ids = await _as_tenant(
        None,
        lambda s: s.execute(
            select(Region.id).where(Region.id.in_([mine["region"], theirs["region"]]))
        ),
    )
    assert sorted(ids.scalars().all()) == sorted([mine["region"], theirs["region"]])
