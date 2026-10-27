"""Location-scoped schedule lock (#180): prevents concurrent generate /
approve / edit activity on locations that share employees.

Storage is one row per locked (company_id, location_id), enforced by
UNIQUE(company_id, location_id). An operation locks a *set* of locations
(see `expand_lock_set`) by inserting one row per location under a shared
`lock_group`; `release` deletes the group. Rows are only flushed, never
committed here: a concurrent inserter of the same location blocks on the
unique index until the holder's transaction ends.

Deadlock safety: every row-level effect on a (company, location) key -
deleting its expired row, inserting its new row - happens at that key's
position in ascending location_id order. Two acquirers therefore only ever
wait on a key from the lower end of what they still need, so no wait
cycle can form. For the same reason expired rows are swept per key, just
before that key's insert, and not company-wide up front: a company-wide
sweep would take row locks on keys out of order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Employee, Location, ScheduleLock, User
from backend.services.archive import active
from backend.utils.id_gen import generate_short_id

logger = logging.getLogger(__name__)

_FALLBACK_HOLDER = "another manager"


class LockHeld(Exception):
    """Raised when another operation holds a live lock on a location in the set."""

    def __init__(
        self,
        locked_by_full_name: str,
        expires_at: datetime,
        location_id: str | None = None,
        location_name: str | None = None,
    ):
        self.locked_by_full_name = locked_by_full_name
        self.expires_at = expires_at
        self.location_id = location_id
        self.location_name = location_name
        where = f" on {location_name or location_id}" if location_id else ""
        super().__init__(
            f"Schedule lock held by {locked_by_full_name}{where} "
            f"until {expires_at.isoformat()}"
        )


@dataclass
class LockHandle:
    """What `acquire` returns: release with `release(db, handle.group)`."""

    group: str
    location_ids: list[str]
    expires_at: datetime


async def expand_lock_set(
    db: AsyncSession, company_id: str, seed: set[str] | None
) -> list[str]:
    """The sorted set of location ids an operation on `seed` must lock.

    None means the whole company: every location, archived included.
    Otherwise the seed plus every location of every active employee whose
    location_ids intersect it - one hop, not transitive. Ids that are not
    this company's locations (stale JSON) are dropped so they never reach
    the location_id FK.
    """
    company_location_ids: set[str] = set((await db.execute(
        select(Location.id).where(Location.company_id == company_id)
    )).scalars().all())
    if seed is None:
        return sorted(company_location_ids)

    result: set[str] = set(seed)
    employee_location_lists = (await db.execute(
        select(Employee.location_ids).where(
            Employee.company_id == company_id, active(Employee),
        )
    )).scalars().all()
    # location_ids is a JSON column: intersect in Python, as the rest of
    # the codebase does.
    for location_ids in employee_location_lists:
        ids = {str(lid) for lid in (location_ids or [])}
        if ids & seed:
            result |= ids
    return sorted(result & company_location_ids)


async def acquire(
    db: AsyncSession,
    *,
    company_id: str,
    user_id: str,
    operation: str,
    location_ids: list[str],
    ttl_seconds: int | None = None,
    group: str | None = None,
) -> LockHandle:
    """Lock every location in `location_ids` or raise LockHeld.

    ``ttl_seconds`` overrides ``settings.SCHEDULE_LOCK_TTL_SECONDS``.
    ``group`` extends an already-held lock set: locations that group holds
    are not a conflict, and the new rows join it. It is also a heartbeat:
    every row the group still holds gets the new ``expires_at``, so a long
    run does not lose its lock between writes. A wanted location whose
    group row has vanished (another acquirer swept it after it expired)
    is lost: LockHeld, not a silent re-insert, because that acquirer may
    have written there meanwhile.

    Steps:
      1. Fast path: a live row of another group on any location in the set
         raises LockHeld naming the first such location in sorted order.
      2. Per location, in sorted order and inside one savepoint for the
         whole set: delete that location's expired row, then insert ours.
         Losing the unique race re-reads the holder; if the holder has
         vanished (released meanwhile) the insert is retried once, then
         LockHeld with the "another manager" fallback - never a 500.
         On LockHeld the rows already inserted for this set roll back.
    """
    if not location_ids:
        raise ValueError("acquire needs at least one location")
    ttl = ttl_seconds if ttl_seconds is not None else settings.SCHEDULE_LOCK_TTL_SECONDS
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    wanted = sorted(set(location_ids))
    lock_group = group or generate_short_id()

    held_by_group: set[str] = set()
    if group is not None:
        held_by_group = set((await db.execute(
            select(ScheduleLock.location_id).where(
                ScheduleLock.company_id == company_id,
                ScheduleLock.lock_group == group,
            )
        )).scalars().all())
    to_insert = [lid for lid in wanted if lid not in held_by_group]

    conflict = await _first_live_conflict(db, company_id, to_insert, now)
    if conflict is not None:
        raise await _lock_held(db, conflict.locked_by_user_id, conflict.expires_at,
                               conflict.location_id)

    refreshed: set[str] = set()
    async with db.begin_nested():
        # One pass in ascending location_id order, so every row-level effect
        # - refreshing one of our rows, sweeping and inserting another -
        # keeps the module's lock order (see the module docstring).
        for location_id in sorted(held_by_group | set(to_insert)):
            if location_id in held_by_group:
                updated = (await db.execute(
                    update(ScheduleLock).where(
                        ScheduleLock.company_id == company_id,
                        ScheduleLock.location_id == location_id,
                        ScheduleLock.lock_group == lock_group,
                    ).values(expires_at=expires_at)
                    .execution_options(synchronize_session=False)
                )).rowcount
                if updated:
                    refreshed.add(location_id)
                elif location_id in wanted:
                    holder = await _row_at(db, company_id, location_id)
                    if holder is not None:
                        raise await _lock_held(db, holder.locked_by_user_id,
                                               holder.expires_at, location_id)
                    raise LockHeld(_FALLBACK_HOLDER, expires_at, location_id,
                                   await _location_name(db, location_id))
                # A vanished row outside this call's set is simply dropped:
                # the write that needs it re-acquires it then.
                continue
            await _insert_one(
                db,
                company_id=company_id,
                location_id=location_id,
                lock_group=lock_group,
                user_id=user_id,
                operation=operation,
                now=now,
                expires_at=expires_at,
            )

    return LockHandle(
        group=lock_group,
        location_ids=sorted(refreshed | set(wanted)),
        expires_at=expires_at,
    )


async def _insert_one(
    db: AsyncSession,
    *,
    company_id: str,
    location_id: str,
    lock_group: str,
    user_id: str,
    operation: str,
    now: datetime,
    expires_at: datetime,
) -> None:
    for attempt in range(2):
        try:
            async with db.begin_nested():
                await db.execute(delete(ScheduleLock).where(
                    ScheduleLock.company_id == company_id,
                    ScheduleLock.location_id == location_id,
                    ScheduleLock.expires_at < now,
                ))
                db.add(ScheduleLock(
                    company_id=company_id,
                    location_id=location_id,
                    lock_group=lock_group,
                    locked_by_user_id=user_id,
                    operation=operation,
                    acquired_at=now,
                    expires_at=expires_at,
                ))
                await db.flush()
            return
        except IntegrityError:
            # A concurrent acquirer committed this location first. The
            # savepoint rolled our insert back; the transaction is valid.
            holder = await _row_at(db, company_id, location_id)
            if holder is not None and _as_utc(holder.expires_at) >= now:
                raise await _lock_held(db, holder.locked_by_user_id,
                                       holder.expires_at, location_id)
            # The holder released (or expired) between its insert and our
            # read: retry once.
            logger.info(
                "Schedule lock holder vanished for company=%s location=%s (attempt %d)",
                company_id, location_id, attempt + 1,
            )
    raise LockHeld(_FALLBACK_HOLDER, expires_at, location_id,
                   await _location_name(db, location_id))


async def _first_live_conflict(
    db: AsyncSession, company_id: str, location_ids: list[str], now: datetime
) -> ScheduleLock | None:
    if not location_ids:
        return None
    return (await db.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == company_id,
            ScheduleLock.location_id.in_(location_ids),
            ScheduleLock.expires_at >= now,
        ).order_by(ScheduleLock.location_id).limit(1)
    )).scalar_one_or_none()


async def _row_at(
    db: AsyncSession, company_id: str, location_id: str
) -> ScheduleLock | None:
    return (await db.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == company_id,
            ScheduleLock.location_id == location_id,
        )
    )).scalar_one_or_none()


async def _lock_held(
    db: AsyncSession, user_id: str, expires_at: datetime, location_id: str
) -> LockHeld:
    return LockHeld(
        await _holder_full_name(db, user_id),
        _as_utc(expires_at),
        location_id,
        await _location_name(db, location_id),
    )


def _as_utc(dt: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC-aware. We always store UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _holder_full_name(db: AsyncSession, user_id: str) -> str:
    holder = (await db.execute(
        select(User).where(User.id == user_id)
    )).scalar_one_or_none()
    if holder is None or not holder.full_name:
        return _FALLBACK_HOLDER
    return holder.full_name


async def _location_name(db: AsyncSession, location_id: str) -> str | None:
    return (await db.execute(
        select(Location.name).where(Location.id == location_id)
    )).scalar_one_or_none()


async def release(db: AsyncSession, group: str) -> None:
    """Delete every row of the lock group. No-op if already gone.

    The rows are row-locked in ascending location_id order first, as
    acquire touches them. A bare DELETE locks them in physical order, and a
    row added later by a relock sits later on disk: release could then hold
    B while waiting on A, which a concurrent acquirer sweeping A then B
    turns into a deadlock.
    """
    await db.execute(
        select(ScheduleLock.id).where(ScheduleLock.lock_group == group)
        .order_by(ScheduleLock.location_id).with_for_update()
    )
    await db.execute(delete(ScheduleLock).where(ScheduleLock.lock_group == group))
    await db.flush()
