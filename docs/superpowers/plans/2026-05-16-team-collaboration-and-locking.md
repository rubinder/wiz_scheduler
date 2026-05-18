# Team Collaboration + Schedule Lock + Employee UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four manager-surface improvements on `feat-team-and-locking`: (1) manager-invite flow scoped to the Ownership Group, (2) per-Company schedule temporal lock with 5-minute TTL, (3) location filter on `/manager/employees`, (4) split of the 1145-LoC `EmployeeAssociation` page into separate Availability and Association pages with their own sidebar entries.

**Architecture:** Backend adds two tables in one Alembic revision (`manager_invites`, `schedule_locks`). The lock is a single-row-per-Company with `UNIQUE(company_id)`; acquire uses delete-expired-then-insert with a savepoint to handle the `UniqueViolation` cleanly. Manager invites are OG-scoped and the acceptor picks a Company at accept time. Frontend lock UX is a 409 toast with a live countdown. Page split keeps backend untouched; shared helpers move to a `_employeesShared.ts` module.

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.x async · Alembic · pytest-asyncio · React 18 · TypeScript · Vite · Tailwind · Resend

**Spec:** `docs/superpowers/specs/2026-05-16-team-collaboration-and-locking-design.md`

**Branch:** `feat-team-and-locking` (already created from `main`)

---

## Task 1: Alembic migration + ORM models for `manager_invites` and `schedule_locks`

**Files:**
- Create: `backend/alembic/versions/0023_add_manager_invites_and_schedule_locks.py`
- Create: `backend/models/manager_invite.py`
- Create: `backend/models/schedule_lock.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/config.py` (add `SCHEDULE_LOCK_TTL_SECONDS`)
- Test: `tests/test_models_team_and_locking.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_team_and_locking.py`:

```python
"""Verify the new ORM models map to their tables and have the expected
columns and constraints."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    ManagerInvite,
    OwnershipGroup,
    ScheduleLock,
    User,
)


@pytest.mark.asyncio
async def test_manager_invite_round_trip(db_session: AsyncSession, seeded_company):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    seeded_company.ownership_group_id = og.id
    await db_session.flush()

    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="newmgr@example.com",
        token="tok_" + "x" * 30,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    row = (await db_session.execute(
        sa.select(ManagerInvite).where(ManagerInvite.token == invite.token)
    )).scalar_one()
    assert row.ownership_group_id == og.id
    assert row.status == "pending"
    assert row.accepted_company_id is None


@pytest.mark.asyncio
async def test_schedule_lock_unique_per_company(
    db_session: AsyncSession, seeded_company
):
    """Cannot have two active locks for the same company."""
    user_id = seeded_company.manager_user_id
    company_id = seeded_company.company_id
    now = datetime.now(timezone.utc)

    lock1 = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation="generate",
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(lock1)
    await db_session.commit()

    lock2 = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation="approve",
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(lock2)
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
```

`seeded_company` is a new fixture — add to `tests/conftest.py` (read the file first to find the right insertion point and existing fixtures to compose with). The fixture should return an object with `company_id`, `manager_user_id`, and `og_id` attributes that point at the existing seed-fixture's manager + company. If a similar fixture already exists, use it instead of adding another.

- [ ] **Step 2: Run the failing test**

Run: `pytest tests/test_models_team_and_locking.py --no-header -q`
Expected: `ImportError` because `ManagerInvite` and `ScheduleLock` don't exist yet.

- [ ] **Step 3: Create `backend/models/manager_invite.py`**

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ManagerInvite(Base):
    __tablename__ = "manager_invites"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False, index=True
    )
    invited_by_user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_company_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=True
    )
```

`CURRENT_TIMESTAMP` (not `now()`) so the SQLite-backed tests work. Postgres accepts it too.

- [ ] **Step 4: Create `backend/models/schedule_lock.py`**

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ScheduleLock(Base):
    __tablename__ = "schedule_locks"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_schedule_locks_company_id"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    locked_by_user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
```

- [ ] **Step 5: Export the new models from `backend/models/__init__.py`**

Append `ManagerInvite` and `ScheduleLock` to both the imports section and the `__all__` list. Imports:

```python
from backend.models.manager_invite import ManagerInvite
from backend.models.schedule_lock import ScheduleLock
```

And in `__all__`:

```python
    "ManagerInvite",
    "ScheduleLock",
```

- [ ] **Step 6: Add the TTL setting to `backend/config.py`**

Inside the `Settings` class, after the other integer settings, add:

```python
    SCHEDULE_LOCK_TTL_SECONDS: int = 300  # 5 minutes
```

- [ ] **Step 7: Create the Alembic migration**

Create `backend/alembic/versions/0023_add_manager_invites_and_schedule_locks.py`:

```python
"""add manager_invites and schedule_locks

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manager_invites",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "ownership_group_id",
            sa.String(length=8),
            sa.ForeignKey("ownership_groups.id"),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            sa.String(length=8),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_company_id",
            sa.String(length=8),
            sa.ForeignKey("companies.id"),
            nullable=True,
        ),
        sa.UniqueConstraint("token", name="uq_manager_invites_token"),
    )
    op.create_index(
        "ix_manager_invites_og_id",
        "manager_invites",
        ["ownership_group_id"],
    )
    op.create_index(
        "ix_manager_invites_token",
        "manager_invites",
        ["token"],
    )

    op.create_table(
        "schedule_locks",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=8),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column(
            "locked_by_user_id",
            sa.String(length=8),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", name="uq_schedule_locks_company_id"),
    )


def downgrade() -> None:
    op.drop_table("schedule_locks")
    op.drop_index("ix_manager_invites_token", table_name="manager_invites")
    op.drop_index("ix_manager_invites_og_id", table_name="manager_invites")
    op.drop_table("manager_invites")
```

- [ ] **Step 8: Run tests, verify PASS**

Run: `pytest tests/test_models_team_and_locking.py --no-header -q`
Expected: 2 PASS.

Also run the full suite to confirm nothing broke: `pytest --no-header -q`
Expected: 0 regressions.

- [ ] **Step 9: Commit**

```bash
git add backend/models/manager_invite.py backend/models/schedule_lock.py \
        backend/models/__init__.py backend/config.py \
        backend/alembic/versions/0023_add_manager_invites_and_schedule_locks.py \
        tests/test_models_team_and_locking.py tests/conftest.py
git commit -m "feat(backend): manager_invites + schedule_locks models + migration"
```

---

## Task 2: `schedule_lock` service (acquire / release)

**Files:**
- Create: `backend/services/schedule_lock.py`
- Test: `tests/test_schedule_lock.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schedule_lock.py`:

```python
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
    assert isinstance(exc.value.locked_by_full_name, str)


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
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_schedule_lock.py --no-header -q`
Expected: 5 errors — module not found.

- [ ] **Step 3: Implement `backend/services/schedule_lock.py`**

```python
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
    """Acquire the per-Company lock or raise LockHeld."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.SCHEDULE_LOCK_TTL_SECONDS)

    # Sweep stale rows for this company.
    await db.execute(
        delete(ScheduleLock).where(
            ScheduleLock.company_id == company_id,
            ScheduleLock.expires_at < now,
        )
    )
    await db.flush()

    lock = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation=operation,
        acquired_at=now,
        expires_at=expires_at,
    )
    db.add(lock)
    try:
        # Use a savepoint so a UniqueViolation here doesn't poison the
        # outer transaction.
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # Reload the active lock + holder name to surface in LockHeld.
        existing = (await db.execute(
            select(ScheduleLock).where(ScheduleLock.company_id == company_id)
        )).scalar_one()
        holder = (await db.execute(
            select(User).where(User.id == existing.locked_by_user_id)
        )).scalar_one_or_none()
        full_name = (holder.full_name if holder and holder.full_name else "another manager")
        raise LockHeld(full_name, existing.expires_at)
    return lock


async def release(db: AsyncSession, lock_id: str) -> None:
    """Delete the lock row by id. No-op if already gone."""
    await db.execute(delete(ScheduleLock).where(ScheduleLock.id == lock_id))
    await db.flush()
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_schedule_lock.py --no-header -q`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/schedule_lock.py tests/test_schedule_lock.py
git commit -m "feat(backend): schedule_lock acquire/release service"
```

---

## Task 3: Wire the lock into `POST /schedules/generate`

**Files:**
- Modify: `backend/routers/schedules.py`
- Test: `tests/test_schedules.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedules.py`:

```python
async def test_generate_returns_409_when_locked(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """If a non-expired lock exists for the Company, /generate returns 409."""
    from datetime import datetime, timedelta, timezone
    from backend.models import ScheduleLock

    db_session.add(ScheduleLock(
        company_id=seeded_company.company_id,
        locked_by_user_id=seeded_company.manager_user_id,
        operation="generate",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "schedule_locked"
    assert "locked_by" in body["detail"]
    assert "expires_at" in body["detail"]


async def test_generate_releases_lock_after_stream(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """When the stream completes successfully, the lock row is gone."""
    from sqlalchemy import select
    from backend.models import ScheduleLock

    # Replace the pipeline with a stub that yields nothing.
    async def fake_pipeline(**kwargs):
        if False:
            yield {}
        return
    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    assert resp.status_code == 200
    # Drain the stream so the generator finishes.
    async for _ in resp.aiter_lines():
        pass

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert rows == []


async def test_generate_releases_lock_on_exception(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """If the pipeline raises mid-stream, the lock is still released."""
    from sqlalchemy import select
    from backend.models import ScheduleLock

    async def boom(**kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover
    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", boom
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    async for _ in resp.aiter_lines():
        pass

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert rows == []
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_schedules.py -k "lock" --no-header -q`
Expected: 3 FAIL — lock isn't wired yet.

- [ ] **Step 3: Add lock integration in `backend/routers/schedules.py`**

At the top of the file, add the import:

```python
from backend.services.schedule_lock import LockHeld, acquire as acquire_lock, release as release_lock
```

In `generate_schedule`, immediately after the existing AI-credit pre-check and before `template_ids = ...` assembly, acquire the lock:

```python
    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="generate",
        )
    except LockHeld as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "schedule_locked",
                "locked_by": e.locked_by_full_name,
                "expires_at": e.expires_at.isoformat(),
            },
        )
```

Then wrap the body of `event_stream` in a `try / finally` that releases on every exit path:

```python
    async def event_stream():
        try:
            # ... existing event_stream body ...
        finally:
            try:
                await release_lock(db, lock.id)
                await db.commit()
            except Exception:
                logger.exception("Failed to release schedule lock for company=%s", current_user.company_id)
```

Keep the existing `try/except Exception as exc:` (the failure-logging block) inside the outer `try` so its behaviour is preserved. The pattern is: outer `try` for lock release, inner `try` for pipeline error logging.

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_schedules.py -k "lock" --no-header -q`
Expected: 3 PASS.

Run the full schedules test file: `pytest tests/test_schedules.py --no-header -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/schedules.py tests/test_schedules.py
git commit -m "feat(backend): acquire/release schedule lock around /generate"
```

---

## Task 4: Wire the lock into `POST /schedules/{id}/approve`

**Files:**
- Modify: `backend/routers/schedules.py`
- Test: `tests/test_schedules.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schedules.py`:

```python
async def test_approve_returns_409_when_locked(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company,
):
    from datetime import datetime, timedelta, timezone
    from backend.models import ScheduleLock, ShiftSchedule

    sched = ShiftSchedule(
        company_id=seeded_company.company_id,
        location_id=seeded_company.location_id,
        week_start_date=datetime(2026, 5, 18).date(),
        status="draft",
        raw_llm_output="[]",
    )
    db_session.add(sched)
    db_session.add(ScheduleLock(
        company_id=seeded_company.company_id,
        locked_by_user_id=seeded_company.manager_user_id,
        operation="generate",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/schedules/{sched.id}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "schedule_locked"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_schedules.py::test_approve_returns_409_when_locked --no-header -q`
Expected: FAIL — approve doesn't check the lock.

- [ ] **Step 3: Wire the lock into `approve_schedule`**

In `approve_schedule`, right after fetching the schedule and verifying it's not already approved (around the `schedule.status = "approved"` line), wrap the mutation:

```python
    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="approve",
        )
    except LockHeld as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "schedule_locked",
                "locked_by": e.locked_by_full_name,
                "expires_at": e.expires_at.isoformat(),
            },
        )

    try:
        # ... the rest of the existing approve_schedule body, ending with
        # `await db.commit()` and `return ShiftScheduleResponse(...)`
    finally:
        await release_lock(db, lock.id)
        await db.commit()
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_schedules.py --no-header -q`
Expected: all approve tests + the new lock test pass.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/schedules.py tests/test_schedules.py
git commit -m "feat(backend): acquire/release schedule lock around /approve"
```

---

## Task 5: Manager-invite email helper

**Files:**
- Create: `backend/services/manager_invite_email.py`
- Test: `tests/test_manager_invite_email.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_manager_invite_email.py`:

```python
"""Verify the manager-invite email helper hits Resend with the right payload."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.services.manager_invite_email import send_manager_invite_email


@pytest.mark.asyncio
async def test_send_manager_invite_email_calls_resend(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")

    sent = []

    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)

    class FakeResend:
        api_key = None
        Emails = FakeEmails()

    import sys
    monkeypatch.setitem(sys.modules, "resend", FakeResend)

    await send_manager_invite_email(
        email="newmgr@example.com",
        group_name="Acme OG",
        invite_url="https://app.wiz.test/accept-manager-invite?token=abc",
    )
    assert len(sent) == 1
    assert "newmgr@example.com" in sent[0]["to"]
    assert "Acme OG" in sent[0]["html"]
    assert "accept-manager-invite?token=abc" in sent[0]["html"]
    assert "manager" in sent[0]["subject"].lower()


@pytest.mark.asyncio
async def test_send_manager_invite_email_noop_without_resend_key(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    await send_manager_invite_email(
        email="x@y.test", group_name="Acme", invite_url="https://x.test/y"
    )  # must not raise
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_manager_invite_email.py --no-header -q`
Expected: ImportError.

- [ ] **Step 3: Implement the helper**

Create `backend/services/manager_invite_email.py`:

```python
"""Resend-backed email helper for manager invites."""
from __future__ import annotations

import html as _html
import logging

from backend.config import settings

logger = logging.getLogger(__name__)


async def send_manager_invite_email(
    email: str,
    group_name: str,
    invite_url: str,
) -> None:
    """Send the manager-invite email. No-op when RESEND_API_KEY is unset.

    Mirrors the visual style of the existing employee-invite email
    (backend/routers/invites.py::_send_invite_email).
    """
    if not settings.RESEND_API_KEY:
        logger.info(
            "RESEND_API_KEY not set — skipping manager-invite email to %s",
            (email[:3] + "***") if email else "?",
        )
        return

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [email],
            "subject": f"You've been invited as a manager on {_html.escape(group_name)}",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hello,</p>"
                f"<p>You've been invited to become a manager on "
                f"<strong>{_html.escape(group_name)}</strong> in WizScheduler. "
                f"Click below to set up your account and pick which company "
                f"you'll manage:</p>"
                f'<p><a href="{invite_url}" style="display:inline-block;'
                f"padding:10px 24px;background-color:#4f46e5;color:#ffffff;"
                f"text-decoration:none;border-radius:6px;font-weight:600;\">"
                f"Set Up Your Manager Account</a></p>"
                f"<p>This link expires in 7 days.</p>"
                f'<p style="color:#6b7280;font-size:12px;">If the button doesn'
                f"'t work, copy and paste this URL: {invite_url}</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.exception("Failed to send manager-invite email")
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_manager_invite_email.py --no-header -q`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/manager_invite_email.py tests/test_manager_invite_email.py
git commit -m "feat(backend): manager_invite email helper"
```

---

## Task 6: Manager-invite endpoints + schemas

**Files:**
- Create: `backend/routers/manager_invites.py`
- Create: `backend/schemas/manager_invite.py`
- Modify: `backend/routers/__init__.py`
- Modify: `backend/main.py`
- Test: `tests/test_manager_invites.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_manager_invites.py`:

```python
"""Tests for /api/v1/manager-invites endpoints."""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, ManagerInvite, OwnershipGroup, User


@pytest.mark.asyncio
async def test_create_invite_happy_path(
    client: AsyncClient, db_session: AsyncSession, manager_token, seeded_company
):
    # Attach the seed company to an OG so the inviter has one.
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    company = await db_session.get(Company, seeded_company.company_id)
    company.ownership_group_id = og.id
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"email": "new.mgr@acme.test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "new.mgr@acme.test"
    assert body["status"] == "pending"
    assert body["token"]

    rows = (await db_session.execute(select(ManagerInvite))).scalars().all()
    assert len(rows) == 1
    assert rows[0].ownership_group_id == og.id


@pytest.mark.asyncio
async def test_create_invite_400_when_company_has_no_og(
    client: AsyncClient, manager_token, seeded_company
):
    resp = await client.post(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"email": "x@y.test"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_info_returns_companies(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    db_session.add(Company(name="Sister Co", ownership_group_id=og.id))
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="new@acme.test",
        token="t_" + "y" * 40,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.get(f"/api/v1/manager-invites/info?token={invite.token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "new@acme.test"
    assert body["group_name"] == "Acme OG"
    assert body["expired"] is False
    company_names = {c["name"] for c in body["companies"]}
    assert "Sister Co" in company_names


@pytest.mark.asyncio
async def test_accept_happy_path(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="accept@acme.test",
        token="t_" + "z" * 40,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": seeded_company.company_id,
            "full_name": "New Manager",
            "password": "supersecret",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()

    await db_session.refresh(invite)
    assert invite.status == "accepted"
    assert invite.accepted_company_id == seeded_company.company_id

    new_user = (await db_session.execute(
        select(User).where(User.email == "accept@acme.test")
    )).scalar_one()
    assert new_user.user_role == "manager"
    assert new_user.company_id == seeded_company.company_id


@pytest.mark.asyncio
async def test_accept_410_on_expired(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="late@acme.test",
        token="t_expired_zz",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": seeded_company.company_id,
            "full_name": "L M",
            "password": "x" * 12,
        },
    )
    assert resp.status_code == 410
    await db_session.refresh(invite)
    assert invite.status == "expired"


@pytest.mark.asyncio
async def test_accept_400_company_outside_og(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    other_og = OwnershipGroup(name="Other OG")
    db_session.add_all([og, other_og])
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    outsider = Company(name="Outsider", ownership_group_id=other_og.id)
    db_session.add(outsider)
    await db_session.flush()

    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="x@y.test",
        token="t_outsider_zz",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": outsider.id,
            "full_name": "X",
            "password": "x" * 12,
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_invites_returns_og_scope(
    client: AsyncClient, db_session: AsyncSession, manager_token, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    for i in range(2):
        db_session.add(ManagerInvite(
            ownership_group_id=og.id,
            invited_by_user_id=seeded_company.manager_user_id,
            email=f"m{i}@acme.test",
            token=f"t_list_{i}_aaaaaaaaaaaaaaaaaaa",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
    await db_session.commit()

    resp = await client.get(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_manager_invites.py --no-header -q`
Expected: errors (router doesn't exist).

- [ ] **Step 3: Create the Pydantic schemas**

Create `backend/schemas/manager_invite.py`:

```python
from datetime import datetime

from pydantic import BaseModel, EmailStr


class CreateManagerInviteRequest(BaseModel):
    email: EmailStr


class ManagerInviteResponse(BaseModel):
    id: str
    email: str
    token: str
    invite_url: str
    status: str
    created_at: datetime
    expires_at: datetime


class CompanyChoice(BaseModel):
    id: str
    name: str


class ManagerInviteInfoResponse(BaseModel):
    email: str
    group_name: str
    expired: bool
    companies: list[CompanyChoice]


class AcceptManagerInviteRequest(BaseModel):
    token: str
    company_id: str
    full_name: str
    password: str


class AcceptManagerInviteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ListManagerInviteRow(BaseModel):
    id: str
    email: str
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_company_id: str | None
```

- [ ] **Step 4: Create the router**

Create `backend/routers/manager_invites.py`:

```python
"""Manager-invite endpoints. Invites are scoped to an OwnershipGroup; the
accepting user picks which Company in the OG they want to join.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import Company, ManagerInvite, OwnershipGroup, User
from backend.schemas.manager_invite import (
    AcceptManagerInviteRequest,
    AcceptManagerInviteResponse,
    CompanyChoice,
    CreateManagerInviteRequest,
    ListManagerInviteRow,
    ManagerInviteInfoResponse,
    ManagerInviteResponse,
)
from backend.services.manager_invite_email import send_manager_invite_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manager-invites", tags=["manager-invites"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MANAGER_INVITE_EXPIRE_DAYS = 7


def _create_access_token(user_id: str, company_id: str, user_role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "user_role": user_role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _invite_url_for(request: Request, token: str) -> str:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        parsed = urlparse(origin)
        base = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base = str(request.base_url).rstrip("/")
    return f"{base}/accept-manager-invite?token={token}"


@router.post("/", response_model=ManagerInviteResponse)
async def create_manager_invite(
    body: CreateManagerInviteRequest,
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ManagerInviteResponse:
    company = (await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )).scalar_one()
    if not company.ownership_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your Company is not part of an Ownership Group",
        )

    og = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == company.ownership_group_id)
    )).scalar_one()

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=current_user.id,
        email=body.email,
        token=token,
        status="pending",
        expires_at=now + timedelta(days=MANAGER_INVITE_EXPIRE_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    invite_url = _invite_url_for(request, token)
    await send_manager_invite_email(
        email=body.email, group_name=og.name, invite_url=invite_url
    )

    return ManagerInviteResponse(
        id=invite.id,
        email=invite.email,
        token=invite.token,
        invite_url=invite_url,
        status=invite.status,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/info", response_model=ManagerInviteInfoResponse)
async def get_manager_invite_info(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> ManagerInviteInfoResponse:
    invite = (await db.execute(
        select(ManagerInvite).where(ManagerInvite.token == token)
    )).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    now = datetime.now(timezone.utc)
    expired = invite.expires_at < now

    og = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == invite.ownership_group_id)
    )).scalar_one()

    companies = (await db.execute(
        select(Company)
        .where(Company.ownership_group_id == og.id)
        .order_by(Company.name)
    )).scalars().all()

    return ManagerInviteInfoResponse(
        email=invite.email,
        group_name=og.name,
        expired=expired,
        companies=[CompanyChoice(id=c.id, name=c.name) for c in companies],
    )


@router.post("/accept", response_model=AcceptManagerInviteResponse)
async def accept_manager_invite(
    body: AcceptManagerInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> AcceptManagerInviteResponse:
    invite = (await db.execute(
        select(ManagerInvite).where(ManagerInvite.token == body.token)
    )).scalar_one_or_none()
    if invite is None or invite.status != "pending":
        raise HTTPException(status_code=404, detail="Invite not found or already used")

    now = datetime.now(timezone.utc)
    if invite.expires_at < now:
        invite.status = "expired"
        await db.commit()
        raise HTTPException(status_code=410, detail="Invite expired")

    company = await db.get(Company, body.company_id)
    if company is None or company.ownership_group_id != invite.ownership_group_id:
        raise HTTPException(
            status_code=400,
            detail="Chosen company is not part of the invite's ownership group",
        )

    # Email-within-Company uniqueness
    dup = (await db.execute(
        select(User).where(
            User.email == invite.email, User.company_id == company.id
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="A user with that email already exists in the chosen company",
        )

    new_user = User(
        company_id=company.id,
        email=invite.email,
        hashed_password=pwd_context.hash(body.password),
        full_name=body.full_name,
        user_role="manager",
    )
    db.add(new_user)
    await db.flush()

    invite.status = "accepted"
    invite.accepted_at = now
    invite.accepted_company_id = company.id
    await db.commit()

    return AcceptManagerInviteResponse(
        access_token=_create_access_token(new_user.id, company.id, "manager"),
    )


@router.get("/", response_model=list[ListManagerInviteRow])
async def list_manager_invites(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ListManagerInviteRow]:
    company = (await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )).scalar_one()
    if not company.ownership_group_id:
        return []

    rows = (await db.execute(
        select(ManagerInvite)
        .where(ManagerInvite.ownership_group_id == company.ownership_group_id)
        .order_by(ManagerInvite.created_at.desc())
    )).scalars().all()
    return [
        ListManagerInviteRow(
            id=r.id, email=r.email, status=r.status,
            created_at=r.created_at, expires_at=r.expires_at,
            accepted_at=r.accepted_at, accepted_company_id=r.accepted_company_id,
        )
        for r in rows
    ]
```

- [ ] **Step 5: Register the router**

Modify `backend/routers/__init__.py` to add `manager_invites` to the imports list (mirror the existing pattern — read the file and add a single import line in alphabetical position).

Modify `backend/main.py`: add `manager_invites` to the multi-line `from backend.routers import (...)` block, then add `app.include_router(manager_invites.router, prefix=api_prefix)` right after the existing `invites.router` line.

- [ ] **Step 6: Run, verify PASS**

Run: `pytest tests/test_manager_invites.py --no-header -q`
Expected: 7 PASS.

Run the full backend suite: `pytest --no-header -q`
Expected: no regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/manager_invites.py backend/schemas/manager_invite.py \
        backend/routers/__init__.py backend/main.py \
        tests/test_manager_invites.py
git commit -m "feat(backend): manager-invite endpoints"
```

---

## Task 7: Frontend lock handling in `useScheduleStream`

**Files:**
- Modify: `frontend/src/hooks/useScheduleStream.ts`
- Modify: `frontend/src/api/schedules.ts`

- [ ] **Step 1: Add `LockedError` to the stream hook**

Read `frontend/src/hooks/useScheduleStream.ts` first to understand the current signature. Then add an exported error class and have the hook reject with it on a 409:

```ts
export class ScheduleLockedError extends Error {
  constructor(
    public readonly lockedBy: string,
    public readonly expiresAt: Date,
  ) {
    super(`Schedule locked by ${lockedBy} until ${expiresAt.toISOString()}`);
    this.name = "ScheduleLockedError";
  }
}
```

In the fetch handling, immediately after `response = await fetch(...)`:

```ts
if (response.status === 409) {
  const body = await response.json().catch(() => null);
  const detail = body?.detail ?? {};
  if (detail.code === "schedule_locked") {
    throw new ScheduleLockedError(
      String(detail.locked_by ?? "another manager"),
      new Date(String(detail.expires_at ?? Date.now())),
    );
  }
}
```

Keep the rest of the streaming logic unchanged.

- [ ] **Step 2: Mirror the same handling in `frontend/src/api/schedules.ts::approveSchedule`**

The existing `approveSchedule` wrapper goes through `apiFetch`. Either (a) inspect the response status before it throws, or (b) catch the `apiFetch` error and re-throw `ScheduleLockedError` when the response body has `detail.code === "schedule_locked"`. Read `src/api/client.ts` first to see how `apiFetch` surfaces the response body. Add the same `ScheduleLockedError` export here (re-export from the hook file to keep one source of truth).

Note: the export of `ScheduleLockedError` lives in `useScheduleStream.ts`. In `schedules.ts`, `import { ScheduleLockedError } from "../hooks/useScheduleStream";` and re-use.

- [ ] **Step 3: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useScheduleStream.ts frontend/src/api/schedules.ts
git commit -m "feat(billing-fe): surface 409 schedule lock as ScheduleLockedError"
```

---

## Task 8: Lock toast on `Schedule.tsx`

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx`
- Modify: `frontend/src/i18n/en.ts`
- Modify: all 18 other locale files in `frontend/src/i18n/`

- [ ] **Step 1: Add the toast component**

In `Schedule.tsx`, add a `LockedToast` component (in the same file — no new file unless the page is already overcrowded):

```tsx
function LockedToast({
  lockedBy,
  expiresAt,
  onExpired,
}: {
  lockedBy: string;
  expiresAt: Date;
  onExpired: () => void;
}) {
  const { t } = useLanguage();
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000)),
  );
  useEffect(() => {
    const id = setInterval(() => {
      const next = Math.max(0, Math.floor((expiresAt.getTime() - Date.now()) / 1000));
      setRemaining(next);
      if (next === 0) onExpired();
    }, 1000);
    return () => clearInterval(id);
  }, [expiresAt, onExpired]);
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const body = t.schedule.lockedToastBody
    .replace("{locked_by}", lockedBy)
    .replace("{countdown}", `${mm}:${ss}`);
  return (
    <div className="fixed bottom-4 right-4 max-w-sm rounded-lg bg-amber-100 border border-amber-400 text-amber-900 p-4 shadow-lg z-50">
      <div className="font-semibold">{t.schedule.lockedToastTitle}</div>
      <div className="text-sm">{body}</div>
    </div>
  );
}
```

- [ ] **Step 2: Wire the toast state into the page**

At the top of the `Schedule` component:

```tsx
const [lockedBy, setLockedBy] = useState<string | null>(null);
const [lockExpiresAt, setLockExpiresAt] = useState<Date | null>(null);
const lockActive = lockedBy !== null && lockExpiresAt !== null;
```

In the existing `handleGenerateClick` (or whatever the existing handler is called), wrap the call:

```tsx
try {
  await runGeneration(/*existing args*/);
} catch (err) {
  if (err instanceof ScheduleLockedError) {
    setLockedBy(err.lockedBy);
    setLockExpiresAt(err.expiresAt);
    return;
  }
  throw err;
}
```

Do the same in the approve handler.

Disable the Generate / Approve buttons while `lockActive` is true (add `disabled={lockActive}` to the existing button props).

Render the toast at the bottom of the page JSX:

```tsx
{lockActive && lockExpiresAt && (
  <LockedToast
    lockedBy={lockedBy!}
    expiresAt={lockExpiresAt}
    onExpired={() => { setLockedBy(null); setLockExpiresAt(null); }}
  />
)}
```

- [ ] **Step 3: Add the i18n keys**

In `frontend/src/i18n/en.ts`, inside the `schedule:` block, add:

```ts
lockedToastTitle: "Schedule activity in progress",
lockedToastBody: "{locked_by} is generating or approving a schedule. Try again in {countdown}.",
```

Add the same keys (English copy as placeholder) to all 18 non-English locale files: `ar.ts bn.ts de.ts es.ts fr.ts hi.ts id.ts ja.ts mr.ts pcm.ts pt.ts ru.ts ta.ts te.ts tr.ts ur.ts vi.ts zh.ts`. Mirror the billing-i18n PR pattern from PR #21 — English copies are acceptable until a real translator does a pass.

Also update `frontend/src/i18n/types.ts` (search for `schedule:` and add the two new key names).

- [ ] **Step 4: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 5: Manual smoke (optional but recommended)**

Start the dev backend + frontend. In one browser window log in as manager M1. Open a terminal and `psql` insert a non-expired lock for M1's Company directly. Click Generate in the UI; the toast appears with the countdown.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx frontend/src/i18n/*.ts
git commit -m "feat(billing-fe): lock-aware toast on /manager/schedule"
```

---

## Task 9: Frontend API client + types for manager invites

**Files:**
- Create: `frontend/src/api/managerInvites.ts`
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Add the TS types**

In `frontend/src/types/index.ts`, append:

```ts
export interface ManagerInvite {
  id: string;
  email: string;
  status: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_company_id: string | null;
}

export interface ManagerInviteInfo {
  email: string;
  group_name: string;
  expired: boolean;
  companies: { id: string; name: string }[];
}
```

- [ ] **Step 2: Create the API wrapper**

Create `frontend/src/api/managerInvites.ts`:

```ts
import { apiFetch } from "./client";
import type { ManagerInvite, ManagerInviteInfo } from "../types";

export async function listManagerInvites(): Promise<ManagerInvite[]> {
  return apiFetch<ManagerInvite[]>("/api/v1/manager-invites/");
}

export async function createManagerInvite(email: string): Promise<ManagerInvite & { token: string; invite_url: string }> {
  return apiFetch("/api/v1/manager-invites/", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function getManagerInviteInfo(token: string): Promise<ManagerInviteInfo> {
  return apiFetch<ManagerInviteInfo>(`/api/v1/manager-invites/info?token=${encodeURIComponent(token)}`);
}

export async function acceptManagerInvite(args: {
  token: string;
  company_id: string;
  full_name: string;
  password: string;
}): Promise<{ access_token: string; token_type: string }> {
  return apiFetch("/api/v1/manager-invites/accept", {
    method: "POST",
    body: JSON.stringify(args),
  });
}
```

- [ ] **Step 3: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/managerInvites.ts frontend/src/types/index.ts
git commit -m "feat(billing-fe): managerInvites API wrappers + types"
```

---

## Task 10: `/manager/team` page

**Files:**
- Create: `frontend/src/pages/manager/Team.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/i18n/en.ts` (and 18 other locales)

- [ ] **Step 1: Create the page**

Create `frontend/src/pages/manager/Team.tsx`. The page lists pending + accepted invites in a table and exposes an "Invite manager" button that opens a modal containing an email input.

The page must follow the visual style of the existing `Employees.tsx` (table styling, action buttons, error banner, modal). Read `Employees.tsx` for the exact theme tokens to use. The modal posts to `createManagerInvite`, refreshes the list on success, and surfaces 4xx errors in an inline banner.

The accepted-company column on each row should render as the Company name when available (look up against `listGroupCompanies` from `frontend/src/api/company.ts`).

- [ ] **Step 2: Register the route**

In `frontend/src/App.tsx`, add inside the `/manager` route block:

```tsx
<Route path="team" element={<Team />} />
```

- [ ] **Step 3: Add the sidebar entry**

In `frontend/src/components/layout/Sidebar.tsx`, append to `baseManagerLinks` (right after the `company` entry):

```ts
{ to: "/manager/team", labelKey: "team" },
```

- [ ] **Step 4: Add the i18n keys**

In `frontend/src/i18n/en.ts`, inside the `nav:` block add `team: "Team",`. Inside (or after) the existing per-page key sections add a new `team:` block with at least:

```ts
team: {
  title: "Team",
  inviteButton: "Invite manager",
  emailLabel: "Email",
  emailPlaceholder: "manager@example.com",
  sendInvite: "Send invite",
  cancel: "Cancel",
  pendingStatus: "Pending",
  acceptedStatus: "Accepted",
  expiredStatus: "Expired",
  columnEmail: "Email",
  columnStatus: "Status",
  columnInvitedAt: "Invited",
  columnAcceptedAt: "Accepted",
  columnCompany: "Company",
  noInvites: "No manager invites yet.",
},
```

Add the matching English-placeholder copy in all 18 other locale files. Update `frontend/src/i18n/types.ts` to include `team` in `Translations` and `team` in `nav`.

- [ ] **Step 5: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/manager/Team.tsx frontend/src/App.tsx \
        frontend/src/components/layout/Sidebar.tsx frontend/src/i18n/
git commit -m "feat(billing-fe): /manager/team page + sidebar entry"
```

---

## Task 11: `/accept-manager-invite` page

**Files:**
- Create: `frontend/src/pages/AcceptManagerInvite.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create the page**

Create `frontend/src/pages/AcceptManagerInvite.tsx`. The page reads `?token=...` from the URL, calls `getManagerInviteInfo`, and renders one of:
- "This link has expired" if `info.expired`
- "Invite not found" on 404
- A form with: read-only email, Company dropdown (`info.companies`), full name input, password input, "Accept invite" button.

On submit, call `acceptManagerInvite`, stash the returned `access_token` in localStorage using the same key the existing `AcceptInvite.tsx` page uses (read it first to confirm), then redirect to `/manager/dashboard`.

Visual style mirrors `AcceptInvite.tsx`.

- [ ] **Step 2: Register the route**

In `frontend/src/App.tsx`, add:

```tsx
<Route path="/accept-manager-invite" element={<AcceptManagerInvite />} />
```

(in the same block as the existing `/accept-invite` route — outside the `ProtectedLayout`).

- [ ] **Step 3: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Manual smoke**

Start backend + frontend. Manually create a manager invite by POSTing as the seeded manager. Open the printed `invite_url` in an incognito window; fill the form; verify the new user is logged in and lands on `/manager/dashboard` with manager permissions.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AcceptManagerInvite.tsx frontend/src/App.tsx
git commit -m "feat(billing-fe): /accept-manager-invite page"
```

---

## Task 12: Extract shared helpers from EmployeeAssociation

**Files:**
- Create: `frontend/src/pages/manager/_employeesShared.ts`
- Modify: `frontend/src/pages/manager/EmployeeAssociation.tsx`

- [ ] **Step 1: Move the pure helpers**

Open `frontend/src/pages/manager/EmployeeAssociation.tsx`. Move these into a new file `frontend/src/pages/manager/_employeesShared.ts`:

- `formatDate`
- `formatTime`
- `isAllDay`
- `getLevelOptions`
- `getLevelLabel`
- `getLevelColor`

Keep their existing signatures. The exported names retain the same casing.

- [ ] **Step 2: Import them back into the page**

Replace the in-file declarations of those functions with:

```ts
import {
  formatDate,
  formatTime,
  isAllDay,
  getLevelOptions,
  getLevelLabel,
  getLevelColor,
} from "./_employeesShared";
```

- [ ] **Step 3: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/manager/_employeesShared.ts frontend/src/pages/manager/EmployeeAssociation.tsx
git commit -m "refactor(billing-fe): extract employee page shared helpers"
```

---

## Task 13: Create `EmployeeAvailability.tsx` and slim `EmployeeAssociation.tsx`

**Files:**
- Create: `frontend/src/pages/manager/EmployeeAvailability.tsx`
- Modify: `frontend/src/pages/manager/EmployeeAssociation.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/i18n/en.ts` (and 18 other locales) + `frontend/src/i18n/types.ts`

- [ ] **Step 1: Copy the file as the basis for EmployeeAvailability**

```bash
cp frontend/src/pages/manager/EmployeeAssociation.tsx frontend/src/pages/manager/EmployeeAvailability.tsx
```

- [ ] **Step 2: Trim EmployeeAvailability.tsx to availability-only**

In `EmployeeAvailability.tsx`:

- Rename the default export from `function EmployeeAssociation()` to `function EmployeeAvailability()`.
- Delete the affinities state (`affinities`, `editingAffinityId`, etc) and all affinity handlers (`handleSave`, `handleDelete` for affinities — *keep* `handleAddAvailability`, `handleDeleteAvailability`, `handleImport7Shifts`, `handleImportDeputy`).
- Delete the `tab` state and the tab-switch buttons; the page now renders only the availability view.
- Remove imports that only the affinities branch uses (`createAffinity`, `deleteAffinity`, `updateAffinity`, `EmployeeAffinity`, `getLevelOptions`, `getLevelLabel`, `getLevelColor`).
- Update the page heading to read from a new i18n key `employeeAvailability.title` (add to `en.ts` + locales + types).

- [ ] **Step 3: Trim EmployeeAssociation.tsx to affinities-only**

In `EmployeeAssociation.tsx`:

- Delete the availability state (`allAvailability`, `editingAvailabilityId`, etc) and the availability handlers (`handleAddAvailability`, `handleDeleteAvailability`, `handleImport7Shifts`, `handleImportDeputy`).
- Delete the `tab` state and tab-switch buttons.
- Remove imports that only the availability branch uses (`createAvailability`, `deleteAvailability`, `listAllAvailability`, `EmployeeAvailability`, plus the 7shifts/Deputy imports + their related state).
- Keep `formatDate`, `formatTime`, `isAllDay` only if affinities use them; otherwise remove the imports.

- [ ] **Step 4: Update routing**

In `frontend/src/App.tsx`, add the new route inside the `/manager` block:

```tsx
<Route path="employee-availability" element={<EmployeeAvailability />} />
```

Existing `employee-association` route stays mapped to the slimmed `EmployeeAssociation`.

Add the import at the top:

```tsx
import EmployeeAvailability from "./pages/manager/EmployeeAvailability";
```

- [ ] **Step 5: Update the sidebar**

In `frontend/src/components/layout/Sidebar.tsx`, replace `postEmployeeManagerLinks` with:

```ts
const postEmployeeManagerLinks: NavItem[] = [
  { to: "/manager/employee-availability", labelKey: "employeeAvailability" },
  { to: "/manager/employee-association",  labelKey: "employeeAssociation" },
  { to: "/manager/shift-templates",        labelKey: "shiftTemplates" },
  { to: "/manager/schedule",               labelKey: "schedule" },
  { to: "/manager/export-schedules",       labelKey: "exportSchedules" },
  { to: "/manager/data-privacy",           labelKey: "dataPrivacy" },
];
```

And update the gate so both availability + association links are stripped until at least one employee exists:

```ts
const links = isManager
  ? hasEmployees
    ? [...baseManagerLinks, ...postEmployeeManagerLinks]
    : [...baseManagerLinks, ...postEmployeeManagerLinks.slice(2)]
  : employeeLinks;
```

- [ ] **Step 6: Update i18n**

In `frontend/src/i18n/en.ts` inside the `nav:` block, add `employeeAvailability: "Employee Availability",`. Also add a top-level `employeeAvailability:` section (mirroring the existing `employeeAssociation` block) with `title`, plus any per-page strings that previously lived under `employeeAssociation.<availability-tab>`. Copy/paste those keys verbatim — they were already translated for previous PRs.

Repeat the new keys in all 18 non-English locales (English placeholders). Update `frontend/src/i18n/types.ts`.

- [ ] **Step 7: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 8: Manual smoke**

Start frontend. Log in as the seeded manager. Confirm both sidebar entries appear (after at least one Employee has been created). Visit `/manager/employee-availability` and verify availability list + add form + 7shifts import work. Visit `/manager/employee-association` and verify affinities table works.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/manager/EmployeeAvailability.tsx \
        frontend/src/pages/manager/EmployeeAssociation.tsx \
        frontend/src/App.tsx frontend/src/components/layout/Sidebar.tsx \
        frontend/src/i18n/
git commit -m "feat(billing-fe): split EmployeeAssociation into Availability + Association pages"
```

---

## Task 14: Location filter on `/manager/employees`

**Files:**
- Modify: `frontend/src/pages/manager/Employees.tsx`
- Modify: `frontend/src/i18n/en.ts` (and 18 other locales) + `frontend/src/i18n/types.ts`

- [ ] **Step 1: Add the filter state and dropdown**

In `Employees.tsx`, add new state alongside the existing ones:

```ts
const [locationFilter, setLocationFilter] = useState<string>("all");
```

Above the employees table (next to the existing Add Employee / Import buttons), render:

```tsx
<div className="flex items-center gap-2">
  <label className={`text-sm ${text.muted}`}>{t.employees.filterLocationLabel}</label>
  <select
    value={locationFilter}
    onChange={(e) => setLocationFilter(e.target.value)}
    className={`px-3 py-1 rounded border ${border.default} ${bg.surface} ${text.primary}`}
  >
    <option value="all">{t.employees.filterAllLocations}</option>
    {locations.map((l) => (
      <option key={l.id} value={l.id}>{l.name}</option>
    ))}
  </select>
</div>
```

- [ ] **Step 2: Apply the filter in the render path**

Replace the existing `employees.map(...)` (the loop that renders table rows) with a derived list:

```ts
const displayedEmployees = locationFilter === "all"
  ? employees
  : employees.filter((e) => e.location_ids?.includes(locationFilter));
```

Render from `displayedEmployees` instead of `employees`. Verify that the Add and Edit flows still operate on the underlying `employees` array, not the filtered view.

- [ ] **Step 3: i18n keys**

In `frontend/src/i18n/en.ts`, inside the `employees:` block:

```ts
filterLocationLabel: "Location:",
filterAllLocations: "All locations",
```

Add to 18 non-English locales with the same English copy as a placeholder. Update `frontend/src/i18n/types.ts`.

- [ ] **Step 4: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/manager/Employees.tsx frontend/src/i18n/
git commit -m "feat(billing-fe): location filter on /manager/employees"
```

---

## Task 15: Final verification + push + PR

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest --no-header -q`
Expected: 0 failures. New tests this PR: 5 (test_schedule_lock) + 3 (schedule generate lock) + 1 (approve lock) + 7 (manager_invites) + 2 (manager_invite_email) + 2 (models) = 20 additions.

- [ ] **Step 2: Run frontend type-check + production build**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build
```

Expected: clean.

- [ ] **Step 3: Manual smoke matrix**

For each of these flows, click through the running dev environment and confirm behaviour:

- [ ] Existing manager creates a manager invite from `/manager/team`; verifies row appears.
- [ ] Open the printed `invite_url` in an incognito window; accept the invite into a Company in the OG; new user lands on `/manager/dashboard`.
- [ ] As manager A, click Generate. While the stream is running, in a second browser as manager B click Generate — B sees the locked toast with A's name + countdown.
- [ ] After the toast timer hits zero, B's Generate button re-enables.
- [ ] `/manager/employees` filter dropdown narrows the rows by selected Location; "All" restores.
- [ ] `/manager/employee-availability` shows availability + import; `/manager/employee-association` shows only affinities.
- [ ] Sidebar gate: with no employees seeded, neither availability nor association links appear.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin feat-team-and-locking
```

- [ ] **Step 5: Open the PR**

```bash
gh pr create --title "Team collaboration + schedule lock + employee UX" --body "$(cat <<'EOF'
## Summary
- **Manager invitations** scoped to the OwnershipGroup; acceptor picks a Company in the OG at accept time. New `manager_invites` table + endpoints + Team page.
- **Schedule temporal lock** — single Postgres row per Company (5-minute TTL). `/generate` and `/approve` both acquire/release; second manager sees a 409 with a live-countdown toast.
- **Employees-by-location filter** on `/manager/employees` — client-side dropdown, no backend changes.
- **Split EmployeeAssociation** into separate `EmployeeAvailability` + `EmployeeAssociation` pages with their own sidebar entries.

## Migration
`0023_add_manager_invites_and_schedule_locks` — adds two tables. No data backfill.

## Test plan
- [x] 20 new backend tests pass
- [x] Full backend suite green
- [x] Frontend `tsc --noEmit` + `vite build` clean
- [ ] After deploy: lock-aware toast renders for a second manager during an in-flight generate
- [ ] After deploy: manager invite end-to-end (create → email → accept → manage)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Confirm PR URL in your final report.**

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-05-16-team-collaboration-and-locking-design.md`):
- ✅ §1 Manager invitations — Tasks 1, 5, 6, 9, 10, 11
- ✅ §2 Schedule temporal lock — Tasks 1, 2, 3, 4, 7, 8
- ✅ §3 Employees-by-location filter — Task 14
- ✅ §4 Split availability/association — Tasks 12, 13
- ✅ Migration 0023 — Task 1
- ✅ Rollout order (backend → frontend) — preserved by task ordering

**Type/name consistency:**
- `LockHeld(locked_by_full_name, expires_at)` — exception used in Tasks 2, 3, 4 ✓
- `acquire(db, *, company_id, user_id, operation)` — keyword-only after `db` ✓
- `release(db, lock_id)` — Tasks 2, 3, 4 ✓
- `ScheduleLockedError(lockedBy, expiresAt)` — frontend, Tasks 7, 8 ✓
- `ManagerInvite.status` values: `pending | accepted | expired` — covered in Task 6 tests ✓

**No placeholders.** Every code block is concrete. Where a step describes a refactor (Tasks 12, 13) the engineer is asked to read the existing file first — that's intentional because the existing file is 1145 LoC and the diff is too large to inline.

**Risk callouts:**
- Tests use SQLite via `aiosqlite`; `server_default=text("now()")` is replaced with `CURRENT_TIMESTAMP` on the ORM models for SQLite compatibility, while the Alembic migration uses Postgres `now()` (correct for prod).
- The `seeded_company` fixture is new; Task 1 calls out that the implementer must read `tests/conftest.py` and either compose with existing fixtures or add a minimal one.
- Tasks 12–13 are large refactors of a 1145-LoC file. The TS build is the verification gate; the manual smoke in Task 15 Step 3 catches any runtime regression before the PR opens.
