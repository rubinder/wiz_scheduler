"""Per-Company schedule lock: prevents concurrent generate/approve activity.

Storage is a single row per Company in schedule_locks, enforced by a
UNIQUE(company_id) constraint. Acquire deletes expired rows first, then
inserts a new one; the database does the contention check.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import ScheduleLock, User

logger = logging.getLogger(__name__)


class LockHeld(Exception):
    """Raised when another session holds a non-expired lock for the company."""

    def __init__(self, locked_by_full_name: str, expires_at: datetime):
        self.locked_by_full_name = locked_by_full_name
        self.expires_at = expires_at
        super().__init__(
            f"Schedule lock held by {locked_by_full_name} until {expires_at.isoformat()}"
        )


async def acquire(
    db: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    operation: str,
) -> ScheduleLock:
    """Acquire the per-Company lock or raise LockHeld.

    Strategy:
      1. Sweep stale (expired) rows for this Company.
      2. SELECT any remaining active lock. If found, raise LockHeld
         immediately - no INSERT attempted, no savepoint needed. This is
         the common contention path.
      3. INSERT the new lock inside a savepoint. The savepoint only matters
         for the rare cross-process race where two callers both passed the
         SELECT check and one loses the UNIQUE-constraint race at INSERT
         time. On that race, reload + raise LockHeld.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.SCHEDULE_LOCK_TTL_SECONDS)

    # 1. Sweep stale rows for this company.
    await db.execute(
        delete(ScheduleLock).where(
            ScheduleLock.company_id == company_id,
            ScheduleLock.expires_at < now,
        )
    )
    await db.flush()

    # 2. Common contention path: an active lock is already present.
    existing = (await db.execute(
        select(ScheduleLock).where(ScheduleLock.company_id == company_id)
    )).scalar_one_or_none()
    if existing is not None:
        raise LockHeld(
            await _holder_full_name(db, existing.locked_by_user_id),
            _as_utc(existing.expires_at),
        )

    # 3. Race-fallback path: try to insert, savepoint-protect against the
    #    cross-session race where a concurrent caller beat us to it.
    lock = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation=operation,
        acquired_at=now,
        expires_at=expires_at,
    )
    db.add(lock)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # Concurrent acquire - reload and raise. The savepoint rolled the
        # failed insert back; the outer transaction remains valid.
        existing = (await db.execute(
            select(ScheduleLock).where(ScheduleLock.company_id == company_id)
        )).scalar_one()
        raise LockHeld(
            await _holder_full_name(db, existing.locked_by_user_id),
            _as_utc(existing.expires_at),
        )
    return lock


def _as_utc(dt: datetime) -> datetime:
    """Normalize a (possibly naive) datetime from SQLite to UTC-aware.

    SQLAlchemy's DateTime(timezone=True) is honored by Postgres but ignored
    by SQLite, which strips tzinfo on round-trip. We always store UTC, so
    re-attaching UTC tzinfo when missing is correct.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _holder_full_name(db: AsyncSession, user_id: str) -> str:
    holder = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if holder is None or not holder.full_name:
        return "another manager"
    return holder.full_name


async def release(db: AsyncSession, lock_id: str) -> None:
    """Delete the lock row by id. No-op if already gone."""
    await db.execute(delete(ScheduleLock).where(ScheduleLock.id == lock_id))
    await db.flush()
