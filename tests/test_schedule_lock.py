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
