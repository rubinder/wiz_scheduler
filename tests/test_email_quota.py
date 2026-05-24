"""Tests for the per-OG daily email cap (#42)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import OgEmailSendLog, OwnershipGroup
from backend.services.email_quota import check_and_log_email
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture
def og_id():
    return _id()


async def _make_og(db_session: AsyncSession, og_id: str) -> None:
    db_session.add(OwnershipGroup(id=og_id, name="TestOG", ai_credits_usd=0.0))
    await db_session.commit()


async def test_under_cap_logs_and_returns_true(db_session: AsyncSession, og_id: str):
    await _make_og(db_session, og_id)

    assert await check_and_log_email(db_session, og_id, "welcome") is True
    await db_session.commit()

    rows = (await db_session.execute(
        select(OgEmailSendLog).where(OgEmailSendLog.ownership_group_id == og_id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "welcome"


async def test_no_og_returns_true_and_skips_log(db_session: AsyncSession):
    """Sends before an OG exists (registration welcome) must succeed and
    not write an audit row."""
    assert await check_and_log_email(db_session, None, "welcome") is True
    rows = (await db_session.execute(select(OgEmailSendLog))).scalars().all()
    assert rows == []


async def test_at_cap_blocks_and_does_not_log(db_session: AsyncSession, og_id: str):
    """Above the cap: returns False AND does not write a new audit row
    (otherwise the deny window extends every time the attacker pings)."""
    await _make_og(db_session, og_id)

    # Seed exactly OG_EMAIL_DAILY_CAP rows in the last 24h
    now = datetime.now(timezone.utc)
    for i in range(settings.OG_EMAIL_DAILY_CAP):
        db_session.add(OgEmailSendLog(
            ownership_group_id=og_id, kind="manager_invite",
            sent_at=now - timedelta(minutes=30 + i),
        ))
    await db_session.commit()

    assert await check_and_log_email(db_session, og_id, "manager_invite") is False
    await db_session.commit()

    rows = (await db_session.execute(
        select(OgEmailSendLog).where(OgEmailSendLog.ownership_group_id == og_id)
    )).scalars().all()
    assert len(rows) == settings.OG_EMAIL_DAILY_CAP, (
        "blocked sends must not write a new audit row"
    )


async def test_rolls_off_after_24h(db_session: AsyncSession, og_id: str):
    """Rows older than 24h don't count toward the cap."""
    await _make_og(db_session, og_id)

    now = datetime.now(timezone.utc)
    # All cap rows are 25h old
    for _ in range(settings.OG_EMAIL_DAILY_CAP):
        db_session.add(OgEmailSendLog(
            ownership_group_id=og_id, kind="manager_invite",
            sent_at=now - timedelta(hours=25),
        ))
    await db_session.commit()

    assert await check_and_log_email(db_session, og_id, "manager_invite") is True


async def test_per_og_isolation(db_session: AsyncSession):
    """Capping OG A doesn't affect OG B."""
    og_a = _id()
    og_b = _id()
    db_session.add(OwnershipGroup(id=og_a, name="A", ai_credits_usd=0.0))
    db_session.add(OwnershipGroup(id=og_b, name="B", ai_credits_usd=0.0))
    await db_session.commit()

    now = datetime.now(timezone.utc)
    for _ in range(settings.OG_EMAIL_DAILY_CAP):
        db_session.add(OgEmailSendLog(
            ownership_group_id=og_a, kind="x", sent_at=now - timedelta(minutes=10),
        ))
    await db_session.commit()

    assert await check_and_log_email(db_session, og_a, "x") is False
    assert await check_and_log_email(db_session, og_b, "x") is True
