"""Postgres row-level security limits a request's session to its companies (#183).

These read through raw SQL, so nothing at the ORM layer filters: what comes
back is what the database itself allows.
"""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import RLS_COMPANY_IDS
from backend.models import Company, Region
from tests.conftest import _apply_rls_policies, _get_test_engine, _id, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def two_regions(db_session: AsyncSession) -> tuple[str, str, str, str]:
    a, b = _id(), _id()
    db_session.add_all([
        Company(id=a, name="A", slug=_id()), Company(id=b, name="B", slug=_id()),
    ])
    await db_session.flush()
    ra, rb = _id(), _id()
    db_session.add_all([
        Region(id=ra, company_id=a, name="A region"),
        Region(id=rb, company_id=b, name="B region"),
    ])
    await db_session.commit()
    async with _get_test_engine().begin() as conn:
        if not (await conn.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'regions'"
        ))).scalar():
            await _apply_rls_policies(conn)
    return a, b, ra, rb


async def _region_ids(session: AsyncSession) -> set[str]:
    return set((await session.execute(text("SELECT id FROM regions"))).scalars().all())


async def test_a_bound_session_sees_only_its_companies(two_regions, monkeypatch) -> None:
    a, _, ra, rb = two_regions
    monkeypatch.setattr(settings, "RLS_ENFORCE", True)
    async with get_test_session_factory()() as session:
        session.info[RLS_COMPANY_IDS] = [a]
        assert await _region_ids(session) == {ra}


async def test_the_binding_survives_a_rollback(two_regions, monkeypatch) -> None:
    """A rollback mid-request must not leave the rest of it unrestricted."""
    a, _, ra, _ = two_regions
    monkeypatch.setattr(settings, "RLS_ENFORCE", True)
    async with get_test_session_factory()() as session:
        session.info[RLS_COMPANY_IDS] = [a]
        await _region_ids(session)
        await session.rollback()
        assert await _region_ids(session) == {ra}
        await session.commit()
        assert await _region_ids(session) == {ra}


async def test_a_bound_session_cannot_write_another_companys_row(two_regions, monkeypatch) -> None:
    a, b, _, _ = two_regions
    monkeypatch.setattr(settings, "RLS_ENFORCE", True)
    async with get_test_session_factory()() as session:
        session.info[RLS_COMPANY_IDS] = [a]
        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text("INSERT INTO regions (id, company_id, name) VALUES (:id, :c, 'x')"),
                {"id": _id(), "c": b},
            )


async def test_unbound_sessions_and_the_flag_off_are_unrestricted(two_regions, monkeypatch) -> None:
    """Background jobs and unauthenticated routes keep the owner's view, and
    with RLS_ENFORCE off nothing changes for anyone."""
    a, _, ra, rb = two_regions
    monkeypatch.setattr(settings, "RLS_ENFORCE", True)
    async with get_test_session_factory()() as session:
        assert {ra, rb} <= await _region_ids(session)

    monkeypatch.setattr(settings, "RLS_ENFORCE", False)
    async with get_test_session_factory()() as session:
        session.info[RLS_COMPANY_IDS] = [a]
        assert {ra, rb} <= await _region_ids(session)
