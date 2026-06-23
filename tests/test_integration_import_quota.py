"""Tests for the per-OG integration-import cooldown (#44)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import IntegrationImport, OwnershipGroup
from backend.services.integration_import_quota import begin_integration_import
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _make_og(db_session: AsyncSession) -> str:
    og_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="ImportOG", ai_credits_usd=0.0))
    await db_session.commit()
    return og_id


async def test_first_call_writes_row_and_succeeds(db_session: AsyncSession):
    og_id = await _make_og(db_session)
    row = await begin_integration_import(db_session, og_id, "7shifts")
    assert row is not None
    assert row.integration == "7shifts"

    rows = (await db_session.execute(
        select(IntegrationImport).where(
            IntegrationImport.ownership_group_id == og_id,
        )
    )).scalars().all()
    assert len(rows) == 1


async def test_no_og_skips_log_and_succeeds(db_session: AsyncSession):
    """Without an OG (dev/single-Company state) no cooldown applies."""
    row = await begin_integration_import(db_session, None, "7shifts")
    assert row is None

    rows = (await db_session.execute(select(IntegrationImport))).scalars().all()
    assert rows == []


async def test_burst_allows_n_calls_then_429(db_session: AsyncSession):
    """The first INTEGRATION_IMPORT_BURST calls succeed; the next is gated."""
    og_id = await _make_og(db_session)
    for _ in range(settings.INTEGRATION_IMPORT_BURST):
        row = await begin_integration_import(db_session, og_id, "7shifts")
        assert row is not None

    with pytest.raises(HTTPException) as exc:
        await begin_integration_import(db_session, og_id, "7shifts")
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "import_cooldown"
    assert exc.value.detail["retry_after_seconds"] > 0


async def test_second_call_within_burst_succeeds(db_session: AsyncSession):
    """A single follow-up import no longer trips the cooldown (burst > 1)."""
    og_id = await _make_og(db_session)
    await begin_integration_import(db_session, og_id, "7shifts")
    row = await begin_integration_import(db_session, og_id, "7shifts")
    assert row is not None


async def test_different_integrations_have_separate_cooldowns(
    db_session: AsyncSession,
):
    """Cooldown is per (og, integration). A 7shifts import doesn't gate
    a Deputy import."""
    og_id = await _make_og(db_session)
    await begin_integration_import(db_session, og_id, "7shifts")
    row = await begin_integration_import(db_session, og_id, "deputy")
    assert row is not None


async def test_cooldown_expires(db_session: AsyncSession):
    """A row older than the cooldown window doesn't gate."""
    og_id = await _make_og(db_session)
    db_session.add(IntegrationImport(
        ownership_group_id=og_id,
        integration="7shifts",
        started_at=(
            datetime.now(timezone.utc)
            - timedelta(minutes=settings.INTEGRATION_IMPORT_COOLDOWN_MINUTES + 1)
        ),
    ))
    await db_session.commit()

    row = await begin_integration_import(db_session, og_id, "7shifts")
    assert row is not None
