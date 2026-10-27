"""Tests for the schedule-lock service (#180): location-scoped acquire /
release / expiry, and the lock-set expansion."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, ScheduleLock
from backend.services import schedule_lock as lock_service
from backend.services.schedule_lock import (
    LockHandle, LockHeld, acquire, expand_lock_set, release,
)
from tests.conftest import COMPANY_ID, REGION_ID, _id, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def locs(db_session: AsyncSession, seeded_company) -> SimpleNamespace:
    """Four locations A < B < C < D (ids sorted), A being the seeded one's
    sibling. Ids are chosen so their sort order is the letter order."""
    ids = sorted(_id() for _ in range(4))
    names = ["Alpha", "Bravo", "Charlie", "Delta"]
    for loc_id, name in zip(ids, names):
        db_session.add(Location(
            id=loc_id, company_id=COMPANY_ID, region_id=REGION_ID,
            name=name, timezone="America/New_York",
        ))
    await db_session.commit()
    return SimpleNamespace(
        a=ids[0], b=ids[1], c=ids[2], d=ids[3],
        company_id=seeded_company.company_id,
        user_id=seeded_company.manager_user_id,
        seeded=seeded_company.location_id,
    )


async def _acquire(db: AsyncSession, locs: SimpleNamespace, location_ids: list[str],
                   **kw) -> LockHandle:
    return await acquire(
        db, company_id=locs.company_id, user_id=locs.user_id,
        operation=kw.pop("operation", "generate"), location_ids=location_ids, **kw,
    )


async def _rows(db: AsyncSession, company_id: str) -> list[ScheduleLock]:
    return list((await db.execute(
        select(ScheduleLock).where(ScheduleLock.company_id == company_id)
        .order_by(ScheduleLock.location_id)
    )).scalars().all())


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


async def test_acquire_inserts_one_row_per_location_under_one_group(
    db_session: AsyncSession, locs,
):
    handle = await _acquire(db_session, locs, [locs.c, locs.a])
    assert handle.location_ids == [locs.a, locs.c]
    assert handle.expires_at > datetime.now(timezone.utc)
    rows = await _rows(db_session, locs.company_id)
    assert [r.location_id for r in rows] == [locs.a, locs.c]
    assert {r.lock_group for r in rows} == {handle.group}
    assert all(r.locked_by_user_id == locs.user_id for r in rows)
    assert all(r.operation == "generate" for r in rows)


async def test_disjoint_sets_acquire_independently(db_session: AsyncSession, locs):
    first = await _acquire(db_session, locs, [locs.a, locs.b])
    second = await _acquire(db_session, locs, [locs.c, locs.d])
    assert first.group != second.group
    assert len(await _rows(db_session, locs.company_id)) == 4


async def test_overlapping_set_raises_lock_held_naming_the_location(
    db_session: AsyncSession, locs,
):
    await _acquire(db_session, locs, [locs.b, locs.c])
    with pytest.raises(LockHeld) as exc:
        await _acquire(db_session, locs, [locs.a, locs.c, locs.d, locs.b])
    # The first conflicting location in sorted order.
    assert exc.value.location_id == locs.b
    assert exc.value.location_name == "Bravo"
    assert exc.value.locked_by_full_name == "Test Manager"
    assert exc.value.expires_at > datetime.now(timezone.utc)
    # Nothing of the losing set was left behind.
    rows = await _rows(db_session, locs.company_id)
    assert [r.location_id for r in rows] == [locs.b, locs.c]


async def test_acquire_rejects_an_empty_set(db_session: AsyncSession, locs):
    with pytest.raises(ValueError):
        await _acquire(db_session, locs, [])


async def test_expired_rows_are_swept(db_session: AsyncSession, locs):
    now = datetime.now(timezone.utc)
    db_session.add(ScheduleLock(
        company_id=locs.company_id, location_id=locs.a, lock_group=_id(),
        locked_by_user_id=locs.user_id, operation="generate",
        expires_at=now - timedelta(seconds=1),
    ))
    await db_session.commit()

    fresh = await _acquire(db_session, locs, [locs.a], operation="approve")
    rows = await _rows(db_session, locs.company_id)
    assert len(rows) == 1
    assert rows[0].lock_group == fresh.group
    assert rows[0].operation == "approve"
    assert fresh.expires_at > now


async def test_acquire_extends_its_own_group(db_session: AsyncSession, locs):
    """Task 2's pool re-check grows a held set under the same group: the
    locations it already holds are not a conflict."""
    handle = await _acquire(db_session, locs, [locs.a])
    grown = await _acquire(db_session, locs, [locs.a, locs.b], group=handle.group)
    assert grown.group == handle.group
    rows = await _rows(db_session, locs.company_id)
    assert [(r.location_id, r.lock_group) for r in rows] == [
        (locs.a, handle.group), (locs.b, handle.group),
    ]


async def test_archived_location_in_the_set_locks(db_session: AsyncSession, locs):
    loc = await db_session.get(Location, locs.b)
    loc.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    handle = await _acquire(db_session, locs, [locs.b])
    assert handle.location_ids == [locs.b]


# ---------------------------------------------------------------------------
# The IntegrityError fallback: never a 500
# ---------------------------------------------------------------------------


async def _commit_live_row(db: AsyncSession, locs, location_id: str) -> ScheduleLock:
    row = ScheduleLock(
        company_id=locs.company_id, location_id=location_id, lock_group=_id(),
        locked_by_user_id=locs.user_id, operation="generate",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(row)
    await db.commit()
    return row


async def test_vanished_holder_is_retried_and_acquires(
    db_session: AsyncSession, locs, monkeypatch,
):
    """The insert lost the unique race, but the winner released before we
    could read it: retry the insert instead of failing on a missing row."""
    held = await _commit_live_row(db_session, locs, locs.a)

    async def _no_fast_path(*args, **kwargs):
        return None

    real_row_at = lock_service._row_at

    async def _holder_vanishes(db, company_id, location_id):
        await db.execute(delete(ScheduleLock).where(ScheduleLock.id == held.id))
        await db.flush()
        return await real_row_at(db, company_id, location_id)

    monkeypatch.setattr(lock_service, "_first_live_conflict", _no_fast_path)
    monkeypatch.setattr(lock_service, "_row_at", _holder_vanishes)

    handle = await _acquire(db_session, locs, [locs.a])
    rows = await _rows(db_session, locs.company_id)
    assert [(r.location_id, r.lock_group) for r in rows] == [(locs.a, handle.group)]


async def test_holder_that_keeps_vanishing_raises_lock_held_not_500(
    db_session: AsyncSession, locs, monkeypatch,
):
    await _commit_live_row(db_session, locs, locs.a)

    async def _nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(lock_service, "_first_live_conflict", _nothing)
    monkeypatch.setattr(lock_service, "_row_at", _nothing)

    with pytest.raises(LockHeld) as exc:
        await _acquire(db_session, locs, [locs.a])
    assert exc.value.locked_by_full_name == "another manager"
    assert exc.value.location_id == locs.a
    assert exc.value.location_name == "Alpha"


async def test_lost_race_names_the_live_holder(
    db_session: AsyncSession, locs, monkeypatch,
):
    await _commit_live_row(db_session, locs, locs.b)

    async def _no_fast_path(*args, **kwargs):
        return None

    monkeypatch.setattr(lock_service, "_first_live_conflict", _no_fast_path)
    with pytest.raises(LockHeld) as exc:
        await _acquire(db_session, locs, [locs.a, locs.b])
    assert exc.value.locked_by_full_name == "Test Manager"
    assert exc.value.location_id == locs.b
    # A's row from the losing attempt was rolled back with it.
    rows = await _rows(db_session, locs.company_id)
    assert [r.location_id for r in rows] == [locs.b]


# ---------------------------------------------------------------------------
# Concurrency: two sessions, overlapping sets (Review Focus 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("first_outcome", ["commit", "rollback"])
async def test_overlapping_sets_in_two_sessions_never_deadlock(
    locs, first_outcome: str,
):
    """{A,B} and {B,C} acquired concurrently. The second inserter blocks on
    B's unique-index entry until the first transaction ends: after a commit
    it gets LockHeld naming B, after a rollback it acquires. Either way no
    DeadlockDetected and no IntegrityError escapes."""
    factory = get_test_session_factory()
    first_has_lock = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> LockHandle:
        async with factory() as db:
            handle = await _acquire(db, locs, [locs.a, locs.b])
            first_has_lock.set()
            await release_first.wait()
            if first_outcome == "commit":
                await db.commit()
            else:
                await db.rollback()
            return handle

    async def second() -> LockHandle:
        await first_has_lock.wait()
        async with factory() as db:
            try:
                return await _acquire(db, locs, [locs.c, locs.b])
            finally:
                await db.commit()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_has_lock.wait()
    # Give the second session time to reach (and block on) B's insert.
    await asyncio.sleep(0.5)
    assert not second_task.done()
    release_first.set()

    first_handle = await asyncio.wait_for(first_task, timeout=10)
    results = await asyncio.wait_for(
        asyncio.gather(second_task, return_exceptions=True), timeout=10,
    )
    outcome = results[0]

    async with factory() as db:
        rows = await _rows(db, locs.company_id)
    if first_outcome == "commit":
        assert isinstance(outcome, LockHeld), outcome
        assert outcome.location_id == locs.b
        assert [(r.location_id, r.lock_group) for r in rows] == [
            (locs.a, first_handle.group), (locs.b, first_handle.group),
        ]
    else:
        assert isinstance(outcome, LockHandle), outcome
        assert outcome.location_ids == [locs.b, locs.c]
        assert [(r.location_id, r.lock_group) for r in rows] == [
            (locs.b, outcome.group), (locs.c, outcome.group),
        ]


async def test_reverse_order_requests_are_sorted_before_inserting(locs):
    """Callers passing {B,A} and {A,B} still insert A first, so neither can
    hold B while waiting for A."""
    factory = get_test_session_factory()
    async with factory() as one, factory() as two:
        await _acquire(one, locs, [locs.b, locs.a])
        task = asyncio.create_task(_acquire(two, locs, [locs.a, locs.b]))
        await asyncio.sleep(0.3)
        await one.commit()
        with pytest.raises(LockHeld) as exc:
            await asyncio.wait_for(task, timeout=10)
        assert exc.value.location_id == locs.a
        await two.rollback()


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


async def test_release_removes_the_whole_group(db_session: AsyncSession, locs):
    mine = await _acquire(db_session, locs, [locs.a, locs.b])
    other = await _acquire(db_session, locs, [locs.c])
    await release(db_session, mine.group)
    rows = await _rows(db_session, locs.company_id)
    assert [(r.location_id, r.lock_group) for r in rows] == [(locs.c, other.group)]


async def test_release_idempotent(db_session: AsyncSession, locs):
    handle = await _acquire(db_session, locs, [locs.a])
    await release(db_session, handle.group)
    await release(db_session, handle.group)
    await release(db_session, "nope0001")
    assert await _rows(db_session, locs.company_id) == []


# ---------------------------------------------------------------------------
# TTLs (unchanged by #180)
# ---------------------------------------------------------------------------


async def test_acquire_honours_ttl_seconds_override(db_session: AsyncSession, locs):
    """Uses 15 to match the production LOCAL generate value."""
    before = datetime.now(timezone.utc)
    handle = await _acquire(db_session, locs, [locs.a], ttl_seconds=15)
    delta = (handle.expires_at - before).total_seconds()
    assert 13 <= delta <= 17, f"expected ~15s TTL, got {delta:.1f}s"
    row = (await _rows(db_session, locs.company_id))[0]
    stored = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    assert abs((stored - handle.expires_at).total_seconds()) < 1


async def test_acquire_honours_ai_ttl_value(db_session: AsyncSession, locs):
    """Symmetric test for the AI value (80s) so any future regression on
    either setting flips one of these two tests."""
    before = datetime.now(timezone.utc)
    handle = await _acquire(db_session, locs, [locs.a], ttl_seconds=80)
    delta = (handle.expires_at - before).total_seconds()
    assert 78 <= delta <= 82, f"expected ~80s TTL, got {delta:.1f}s"


def test_production_ttl_values_match_documented_choices():
    """Pin the production TTL values so future tuning is intentional.

    AI=80 covers single-location Anthropic p99; LOCAL=15 is a safety margin
    over sub-second local computation; default=120 is for approve and any
    future caller that doesn't pass an explicit TTL.
    """
    from backend.config import settings
    assert settings.SCHEDULE_LOCK_TTL_AI_GENERATE_SECONDS == 80
    assert settings.SCHEDULE_LOCK_TTL_LOCAL_GENERATE_SECONDS == 15
    assert settings.SCHEDULE_LOCK_TTL_SECONDS == 120


async def test_acquire_default_ttl_uses_settings(db_session: AsyncSession, locs):
    from backend.config import settings
    before = datetime.now(timezone.utc)
    handle = await _acquire(db_session, locs, [locs.a], operation="approve")
    delta = (handle.expires_at - before).total_seconds()
    expected = settings.SCHEDULE_LOCK_TTL_SECONDS
    assert (expected - 5) <= delta <= (expected + 5), (
        f"expected ~{expected}s TTL, got {delta:.1f}s"
    )


# ---------------------------------------------------------------------------
# expand_lock_set
# ---------------------------------------------------------------------------


def _employee(location_ids: list[str], *, archived: bool = False,
              company_id: str = COMPANY_ID) -> Employee:
    return Employee(
        id=_id(), company_id=company_id, full_name="E " + _id(),
        location_ids=location_ids,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )


async def test_expand_adds_locations_of_employees_sharing_the_seed(
    db_session: AsyncSession, locs,
):
    db_session.add_all([
        _employee([locs.c, locs.a]),   # shares A -> brings C
        _employee([locs.d]),           # unrelated
    ])
    await db_session.commit()
    assert await expand_lock_set(db_session, locs.company_id, {locs.a}) == sorted(
        [locs.a, locs.c]
    )


async def test_expand_is_one_hop_not_transitive(db_session: AsyncSession, locs):
    db_session.add_all([_employee([locs.a, locs.b]), _employee([locs.b, locs.c])])
    await db_session.commit()
    assert await expand_lock_set(db_session, locs.company_id, {locs.a}) == [
        locs.a, locs.b,
    ]


async def test_expand_ignores_archived_employees(db_session: AsyncSession, locs):
    db_session.add(_employee([locs.a, locs.d], archived=True))
    await db_session.commit()
    assert await expand_lock_set(db_session, locs.company_id, {locs.a}) == [locs.a]


async def test_expand_none_means_every_location_of_the_company(
    db_session: AsyncSession, locs,
):
    loc = await db_session.get(Location, locs.d)
    loc.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    result = await expand_lock_set(db_session, locs.company_id, None)
    assert result == sorted([locs.seeded, locs.a, locs.b, locs.c, locs.d])


async def test_expand_result_is_sorted(db_session: AsyncSession, locs):
    db_session.add(_employee([locs.d, locs.b, locs.a]))
    await db_session.commit()
    result = await expand_lock_set(db_session, locs.company_id, {locs.d})
    assert result == [locs.a, locs.b, locs.d]


async def test_expand_drops_ids_that_are_not_this_companys_locations(
    db_session: AsyncSession, locs,
):
    """A stale id in an employee's location_ids JSON must not reach the
    location_id FK and turn into a 500."""
    db_session.add(_employee([locs.a, "gone0001"]))
    await db_session.commit()
    assert await expand_lock_set(db_session, locs.company_id, {locs.a}) == [locs.a]


# ---------------------------------------------------------------------------
# Final-review fix round
# ---------------------------------------------------------------------------


async def test_extending_a_group_refreshes_every_row_it_holds(
    db_session: AsyncSession, locs,
):
    """M1. A relock is the run's heartbeat: rows the group already holds,
    in the requested set or not, get the new expiry."""
    handle = await _acquire(db_session, locs, [locs.a, locs.c], ttl_seconds=60)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    for row in await _rows(db_session, locs.company_id):
        row.expires_at = past
    await db_session.commit()

    grown = await _acquire(db_session, locs, [locs.a, locs.b], group=handle.group,
                           ttl_seconds=60)
    db_session.expire_all()
    rows = await _rows(db_session, locs.company_id)
    assert [r.location_id for r in rows] == [locs.a, locs.b, locs.c]
    assert all(r.expires_at == grown.expires_at for r in rows)
    assert grown.expires_at > datetime.now(timezone.utc)
    assert grown.location_ids == [locs.a, locs.b, locs.c]


async def test_a_swept_row_of_the_group_is_lost_not_silently_retaken(
    db_session: AsyncSession, locs,
):
    """M1. Another acquirer swept the group's expired row and took the
    location: extending the group must raise, naming that holder."""
    handle = await _acquire(db_session, locs, [locs.a], ttl_seconds=60)
    await db_session.execute(delete(ScheduleLock).where(ScheduleLock.lock_group == handle.group))
    db_session.add(ScheduleLock(
        company_id=locs.company_id, location_id=locs.a, lock_group=_id(),
        locked_by_user_id=locs.user_id, operation="approve",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()
    with pytest.raises(LockHeld) as exc:
        await _acquire(db_session, locs, [locs.a], group=handle.group)
    assert exc.value.location_id == locs.a


async def test_release_and_a_concurrent_sweep_never_deadlock(locs):
    """M2. Group G holds expired B and C, then A added later (a relock), so
    A sits after B and C on disk. A second session sweeps A, then B, the
    way acquire({A, B}) does, while G is released. Release must take its
    rows in location_id order (A first, where it waits) rather than disk
    order (B and C, then wait on A while the sweeper waits on B)."""
    factory = get_test_session_factory()
    group = _id()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with factory() as s:
        for lid in (locs.b, locs.c):
            s.add(ScheduleLock(company_id=locs.company_id, location_id=lid,
                               lock_group=group, locked_by_user_id=locs.user_id,
                               operation="generate", expires_at=past))
        await s.commit()
        s.add(ScheduleLock(company_id=locs.company_id, location_id=locs.a,
                           lock_group=group, locked_by_user_id=locs.user_id,
                           operation="generate", expires_at=past))
        await s.commit()

    def sweep(location_id: str):
        return delete(ScheduleLock).where(
            ScheduleLock.company_id == locs.company_id,
            ScheduleLock.location_id == location_id,
            ScheduleLock.expires_at < datetime.now(timezone.utc),
        )

    async with factory() as sweeper, factory() as releaser:
        await sweeper.execute(sweep(locs.a))  # holds A's row lock
        releasing = asyncio.create_task(release(releaser, group))
        await asyncio.sleep(0.5)  # release now waits on A (and, unfixed, holds B)
        await asyncio.wait_for(sweeper.execute(sweep(locs.b)), 10)
        await sweeper.commit()
        await asyncio.wait_for(releasing, 10)
        await releaser.commit()

    async with factory() as s:
        assert await _rows(s, locs.company_id) == []
