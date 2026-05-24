"""Tests for the schedule-lock service: acquire / release / expiry."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ScheduleLock
from backend.services.schedule_lock import LockHeld, acquire, release


@pytest.mark.asyncio
async def test_acquire_when_no_row(db_session: AsyncSession, seeded_company):
    lock = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="generate",
    )
    assert lock.company_id == seeded_company.company_id
    assert lock.locked_by_user_id == seeded_company.manager_user_id
    assert lock.operation == "generate"
    assert lock.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_acquire_replaces_expired(db_session: AsyncSession, seeded_company):
    now = datetime.now(timezone.utc)
    stale = ScheduleLock(
        company_id=seeded_company.company_id,
        locked_by_user_id=seeded_company.manager_user_id,
        operation="generate",
        expires_at=now - timedelta(seconds=1),  # already expired
    )
    db_session.add(stale)
    await db_session.commit()

    fresh = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="approve",
    )
    assert fresh.operation == "approve"
    assert fresh.expires_at > now

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == fresh.id


@pytest.mark.asyncio
async def test_acquire_raises_when_held(db_session: AsyncSession, seeded_company):
    await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="generate",
    )
    with pytest.raises(LockHeld) as exc:
        await acquire(
            db_session,
            company_id=seeded_company.company_id,
            user_id=seeded_company.manager_user_id,
            operation="generate",
        )
    assert exc.value.expires_at > datetime.now(timezone.utc)
    assert exc.value.locked_by_full_name == "Test Manager"


@pytest.mark.asyncio
async def test_release_deletes_row(db_session: AsyncSession, seeded_company):
    lock = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="generate",
    )
    await release(db_session, lock.id)
    rows = (await db_session.execute(
        select(ScheduleLock).where(ScheduleLock.id == lock.id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_release_idempotent(db_session: AsyncSession, seeded_company):
    """Releasing a row that's already gone must not raise."""
    await release(db_session, "nope0001")
    rows = (await db_session.execute(
        select(ScheduleLock).where(ScheduleLock.company_id == seeded_company.company_id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_acquire_honours_ttl_seconds_override(
    db_session: AsyncSession, seeded_company
):
    """When ttl_seconds is passed, expires_at = now + that value (not the
    default SCHEDULE_LOCK_TTL_SECONDS). Uses 15 to match the production
    LOCAL generate value."""
    before = datetime.now(timezone.utc)
    lock = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="generate",
        ttl_seconds=15,
    )
    exp = lock.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = (exp - before).total_seconds()
    assert 13 <= delta <= 17, f"expected ~15s TTL, got {delta:.1f}s"


@pytest.mark.asyncio
async def test_acquire_honours_ai_ttl_value(
    db_session: AsyncSession, seeded_company
):
    """Symmetric test for the AI value (80s) so any future regression on
    either setting flips one of these two tests."""
    before = datetime.now(timezone.utc)
    lock = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="generate",
        ttl_seconds=80,
    )
    exp = lock.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = (exp - before).total_seconds()
    assert 78 <= delta <= 82, f"expected ~80s TTL, got {delta:.1f}s"


def test_production_ttl_values_match_documented_choices():
    """Pin the production TTL values so future tuning is intentional.

    AI=80 covers single-location Anthropic p99; LOCAL=15 is a safety margin
    over sub-second local computation; default=300 is for approve and any
    future caller that doesn't pass an explicit TTL.
    """
    from backend.config import settings
    assert settings.SCHEDULE_LOCK_TTL_AI_GENERATE_SECONDS == 80
    assert settings.SCHEDULE_LOCK_TTL_LOCAL_GENERATE_SECONDS == 15
    assert settings.SCHEDULE_LOCK_TTL_SECONDS == 300


@pytest.mark.asyncio
async def test_acquire_default_ttl_uses_settings(
    db_session: AsyncSession, seeded_company
):
    """When no ttl_seconds is passed, expires_at falls back to settings."""
    from backend.config import settings
    before = datetime.now(timezone.utc)
    lock = await acquire(
        db_session,
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        operation="approve",
    )
    exp = lock.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = (exp - before).total_seconds()
    expected = settings.SCHEDULE_LOCK_TTL_SECONDS
    assert (expected - 5) <= delta <= (expected + 5), (
        f"expected ~{expected}s TTL, got {delta:.1f}s"
    )
