# Special Hours Days + Per-Day Template Overrides — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Special Hours Days feature: managers configure per-location, per-date operating hours (holidays, special events); the system clones a chosen recurring `ShiftTemplate` into a one-day variant; the scheduler resolves templates per day so overrides win on their date and the recurring template applies on every other day.

**Architecture:** One migration (`0025`) adds `shift_templates.specific_date` nullable column + a new `special_hours_days` table. A new `template_resolver.py` returns `dict[date, ShiftTemplate]` per location, plugged into `scheduling/nodes.py::load_location_context`. Frontend adds a `/manager/special-hours` page with a create/edit modal, a row-level Duplicate-to-other-locations action, and a small badge on the Schedule page for affected days. Google Business Profile import is **out of scope** for this PR (deferred per the spec).

**Tech Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.x async · Alembic · pytest-asyncio · React 18 · TypeScript · Vite · Tailwind

**Spec:** `docs/superpowers/specs/2026-05-23-special-hours-days-and-per-day-templates-design.md`

**Branch:** `feat-special-hours-days` (already created from `main`, spec already committed)

---

## Task 1: Migration 0025 + ShiftTemplate.specific_date + SpecialHoursDay model

**Files:**
- Create: `backend/alembic/versions/0025_add_special_hours_days_and_shift_template_specific_date.py`
- Create: `backend/models/special_hours_day.py`
- Modify: `backend/models/shift_template.py`
- Modify: `backend/models/__init__.py`
- Test: `tests/test_models_special_hours.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_special_hours.py`:

```python
"""Verify the new ORM model + ShiftTemplate.specific_date column."""
from datetime import date, time, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate, SpecialHoursDay


@pytest.mark.asyncio
async def test_shift_template_specific_date_defaults_null(
    db_session: AsyncSession, seed_location, seed_company
):
    tmpl = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    assert tmpl.specific_date is None


@pytest.mark.asyncio
async def test_shift_template_specific_date_can_be_set(
    db_session: AsyncSession, seed_location, seed_company
):
    target = date(2026, 12, 24)
    tmpl = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Christmas Eve",
        weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=target,
    )
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    assert tmpl.specific_date == target


@pytest.mark.asyncio
async def test_special_hours_day_round_trip(
    db_session: AsyncSession, seed_location, seed_company
):
    row = SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label="Christmas Eve",
    )
    db_session.add(row)
    await db_session.commit()
    loaded = (await db_session.execute(
        sa.select(SpecialHoursDay).where(SpecialHoursDay.id == row.id)
    )).scalar_one()
    assert loaded.label == "Christmas Eve"
    assert loaded.shift_template_id is None


@pytest.mark.asyncio
async def test_special_hours_day_unique_per_location_date(
    db_session: AsyncSession, seed_location, seed_company
):
    target = date(2026, 12, 24)
    db_session.add(SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=target,
        open_time=time(9, 0),
        close_time=time(14, 0),
    ))
    await db_session.commit()

    db_session.add(SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=target,
        open_time=time(10, 0),
        close_time=time(15, 0),
    ))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
```

If the existing test fixtures don't expose `seed_company` (a `Company`) and `seed_location` (a `Location`), read `tests/conftest.py` first and use whichever fixtures are there. The intent is: a Company + Location to attach rows to.

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_models_special_hours.py --no-header -q`
Expected: ImportError because `SpecialHoursDay` and `specific_date` don't exist yet.

- [ ] **Step 3: Add the column to `backend/models/shift_template.py`**

Read the file first. Find the existing `Mapped[...] = mapped_column(...)` declarations. Add:

```python
from datetime import date
...
specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

The `Date` and `date` imports may need adding — check what's already imported. Don't remove anything.

- [ ] **Step 4: Create `backend/models/special_hours_day.py`**

```python
from datetime import date, datetime, time
from sqlalchemy import Date, DateTime, ForeignKey, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class SpecialHoursDay(Base):
    __tablename__ = "special_hours_days"
    __table_args__ = (
        UniqueConstraint(
            "location_id", "date", name="uq_special_hours_days_location_date"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open_time: Mapped[time] = mapped_column(Time, nullable=False)
    close_time: Mapped[time] = mapped_column(Time, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    shift_template_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("shift_templates.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
```

`CURRENT_TIMESTAMP` (not `now()`) so the SQLite-backed tests work. The migration below uses `now()` for Postgres.

- [ ] **Step 5: Export the new model from `backend/models/__init__.py`**

Add the import alongside the others (preserve alphabetical-ish ordering) and add `"SpecialHoursDay"` to `__all__`:

```python
from backend.models.special_hours_day import SpecialHoursDay
```

```python
    "SpecialHoursDay",
```

- [ ] **Step 6: Create the Alembic migration**

Create `backend/alembic/versions/0025_add_special_hours_days_and_shift_template_specific_date.py`:

```python
"""add special_hours_days and shift_templates.specific_date

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_templates",
        sa.Column("specific_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_shift_templates_location_specific_date",
        "shift_templates",
        ["location_id", "specific_date"],
        postgresql_where=sa.text("specific_date IS NOT NULL"),
    )

    op.create_table(
        "special_hours_days",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=8),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            sa.String(length=8),
            sa.ForeignKey("locations.id"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=False),
        sa.Column("close_time", sa.Time(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column(
            "shift_template_id",
            sa.String(length=8),
            sa.ForeignKey("shift_templates.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "location_id", "date", name="uq_special_hours_days_location_date"
        ),
    )
    op.create_index(
        "ix_special_hours_days_company_id",
        "special_hours_days",
        ["company_id"],
    )
    op.create_index(
        "ix_special_hours_days_location_id",
        "special_hours_days",
        ["location_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_special_hours_days_location_id", table_name="special_hours_days"
    )
    op.drop_index(
        "ix_special_hours_days_company_id", table_name="special_hours_days"
    )
    op.drop_table("special_hours_days")
    op.drop_index(
        "ix_shift_templates_location_specific_date",
        table_name="shift_templates",
    )
    op.drop_column("shift_templates", "specific_date")
```

- [ ] **Step 7: Run tests, verify PASS**

Run: `pytest tests/test_models_special_hours.py --no-header -q` → expect 4 PASS.

Run: `pytest --no-header -q` → expect previous baseline + 4 new = same total or +4, zero regressions.

- [ ] **Step 8: Commit**

```bash
git add backend/models/special_hours_day.py backend/models/shift_template.py \
        backend/models/__init__.py \
        backend/alembic/versions/0025_add_special_hours_days_and_shift_template_specific_date.py \
        tests/test_models_special_hours.py
git commit -m "feat(backend): SpecialHoursDay model + ShiftTemplate.specific_date column"
```

---

## Task 2: Template-cloning service helper

**Files:**
- Create: `backend/services/special_hours.py`
- Test: `tests/test_special_hours_service.py`

This extracts the deep-copy / day-extraction / time-override logic so the router and the tests stay readable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_special_hours_service.py`:

```python
"""Unit tests for the clone_template_for_date helper."""
import copy
from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate
from backend.services.special_hours import clone_template_for_date


@pytest.mark.asyncio
async def test_clone_extracts_matching_dow_and_overrides_times(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[
            {"day_of_week": 0, "roles": [{"role_id": "r1", "role_name": "Cashier",
                                          "required_headcount": 2,
                                          "start_time": "09:00", "end_time": "17:00"}]},
            {"day_of_week": 3, "roles": [{"role_id": "r2", "role_name": "Server",
                                          "required_headcount": 3,
                                          "start_time": "11:00", "end_time": "23:00"}]},
        ],
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    # Thursday 2026-12-24 → dow=3 → matches the second entry
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label="Christmas Eve",
    )
    assert clone.specific_date == date(2026, 12, 24)
    assert clone.name == "Weekly — Christmas Eve"
    assert len(clone.weekly_schedule) == 1
    assert clone.weekly_schedule[0]["day_of_week"] == 3
    roles = clone.weekly_schedule[0]["roles"]
    assert len(roles) == 1
    assert roles[0]["role_name"] == "Server"
    assert roles[0]["start_time"] == "09:00:00"
    assert roles[0]["end_time"] == "14:00:00"
    # Source is unmodified
    assert source.weekly_schedule[1]["roles"][0]["start_time"] == "11:00"


@pytest.mark.asyncio
async def test_clone_falls_back_to_first_nonempty_day_when_dow_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Sparse",
        weekly_schedule=[
            {"day_of_week": 0, "roles": []},  # empty Monday
            {"day_of_week": 1, "roles": [{"role_id": "r1", "role_name": "X",
                                          "required_headcount": 1,
                                          "start_time": "08:00", "end_time": "12:00"}]},
        ],
    )
    db_session.add(source)
    await db_session.commit()

    # Sunday 2026-12-27 → dow=6 → not in source → fallback to first non-empty
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 27),
        open_time=time(10, 0),
        close_time=time(15, 0),
        label=None,
    )
    assert clone.weekly_schedule[0]["day_of_week"] == 6  # mirror the target's dow
    assert clone.weekly_schedule[0]["roles"][0]["role_name"] == "X"
    assert clone.weekly_schedule[0]["roles"][0]["start_time"] == "10:00:00"


@pytest.mark.asyncio
async def test_clone_name_falls_back_to_iso_date_when_label_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Base",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(source)
    await db_session.commit()

    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label=None,
    )
    assert clone.name == "Base — 2026-12-24"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_special_hours_service.py --no-header -q`
Expected: ImportError — module doesn't exist yet.

- [ ] **Step 3: Implement `backend/services/special_hours.py`**

```python
"""Service helpers for the Special Hours Days feature."""
from __future__ import annotations

import copy
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


def _format_time(t: time) -> str:
    """Render as HH:MM:SS to match the format already stored in weekly_schedule."""
    return t.strftime("%H:%M:%S")


async def clone_template_for_date(
    db: AsyncSession,
    *,
    source: ShiftTemplate,
    target_date: date,
    open_time: time,
    close_time: time,
    label: str | None,
) -> ShiftTemplate:
    """Clone `source` into a single-day variant for `target_date`.

    - The clone's `weekly_schedule` is a 1-element list whose `day_of_week`
      is `target_date.weekday()`.
    - Roles are copied from the source's matching-dow entry, or (when the
      source has no matching dow) from the first non-empty entry.
    - Every role's `start_time` / `end_time` is replaced with the special
      open / close.
    - `name = f"{source.name} — {label or target_date.isoformat()}"`.
    - `specific_date` is set on the clone.
    - The clone is added to the session and flushed (so callers see an `id`).
    """
    target_dow = target_date.weekday()
    src_days = source.weekly_schedule or []

    matching = next(
        (copy.deepcopy(d) for d in src_days if d.get("day_of_week") == target_dow),
        None,
    )
    if matching is None or not matching.get("roles"):
        # Fall back to the first non-empty day; rewrite its dow to match the target.
        fallback = next(
            (copy.deepcopy(d) for d in src_days if d.get("roles")),
            {"day_of_week": target_dow, "roles": []},
        )
        fallback["day_of_week"] = target_dow
        day_entry = fallback
    else:
        day_entry = matching

    open_str = _format_time(open_time)
    close_str = _format_time(close_time)
    for role in day_entry.get("roles", []):
        role["start_time"] = open_str
        role["end_time"] = close_str

    clone = ShiftTemplate(
        company_id=source.company_id,
        location_id=source.location_id,
        name=f"{source.name} — {label or target_date.isoformat()}",
        weekly_schedule=[day_entry],
        specific_date=target_date,
    )
    db.add(clone)
    await db.flush()
    return clone
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_special_hours_service.py --no-header -q` → expect 3 PASS.

Run: `pytest --no-header -q` → expect 0 regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/services/special_hours.py tests/test_special_hours_service.py
git commit -m "feat(backend): clone_template_for_date service helper"
```

---

## Task 3: Special Hours endpoints + schemas

**Files:**
- Create: `backend/schemas/special_hours.py`
- Create: `backend/routers/special_hours.py`
- Modify: `backend/main.py` (register the router)
- Test: `tests/test_special_hours_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_special_hours_api.py`:

```python
"""Tests for /api/v1/special-hours endpoints."""
from datetime import date, time

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate, SpecialHoursDay


@pytest.mark.asyncio
async def test_create_with_explicit_draft_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_company, seed_shift_template
):
    """Happy path: create a SpecialHoursDay with a chosen recurring template
    as the draft. The clone is created and linked."""
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "label": "Christmas Eve",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "Christmas Eve"
    assert body["shift_template_id"]

    cloned = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == body["shift_template_id"])
    )).scalar_one()
    assert cloned.specific_date == date(2026, 12, 24)
    assert cloned.location_id == seed_location.id


@pytest.mark.asyncio
async def test_create_picks_recurring_when_no_draft_template_provided(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    """When draft_template_id is omitted, the server picks the recurring
    template for the location (one with specific_date IS NULL)."""
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-25",
            "open_time": "10:00",
            "close_time": "16:00",
        },
    )
    assert resp.status_code == 200

    cloned_id = resp.json()["shift_template_id"]
    cloned = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == cloned_id)
    )).scalar_one()
    # Source was seed_shift_template
    assert cloned.name.startswith(seed_shift_template.name + " — ")


@pytest.mark.asyncio
async def test_create_400_when_location_has_no_recurring_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location
):
    # No seed_shift_template fixture → no recurring template exists
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "no_recurring_template"


@pytest.mark.asyncio
async def test_create_409_on_duplicate_location_date(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    first = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "10:00",
            "close_time": "15:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "duplicate"


@pytest.mark.asyncio
async def test_list_filters_by_location_and_date_range(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    for d in ("2026-12-24", "2026-12-25", "2027-01-01"):
        await client.post(
            "/api/v1/special-hours/",
            headers={"Authorization": f"Bearer {manager_token}"},
            json={
                "location_id": seed_location.id,
                "date": d,
                "open_time": "09:00",
                "close_time": "14:00",
                "draft_template_id": seed_shift_template.id,
            },
        )

    resp = await client.get(
        "/api/v1/special-hours/?from_date=2026-12-25&to_date=2027-01-01",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {row["date"] for row in body} == {"2026-12-25", "2027-01-01"}


@pytest.mark.asyncio
async def test_put_updates_times_and_propagates_to_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    create = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    sh_id = create.json()["id"]

    upd = await client.put(
        f"/api/v1/special-hours/{sh_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"open_time": "10:00", "close_time": "13:00", "label": "Half day"},
    )
    assert upd.status_code == 200

    tmpl_id = upd.json()["shift_template_id"]
    tmpl = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
    )).scalar_one()
    roles = tmpl.weekly_schedule[0]["roles"]
    if roles:
        assert roles[0]["start_time"] == "10:00:00"
        assert roles[0]["end_time"] == "13:00:00"


@pytest.mark.asyncio
async def test_delete_removes_both_rows(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    create = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    sh_id = create.json()["id"]
    tmpl_id = create.json()["shift_template_id"]

    delete = await client.delete(
        f"/api/v1/special-hours/{sh_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert delete.status_code == 204

    assert (await db_session.execute(
        select(SpecialHoursDay).where(SpecialHoursDay.id == sh_id)
    )).scalar_one_or_none() is None
    assert (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cross_company_isolation(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template,
):
    """A manager cannot create a SpecialHoursDay on a location that doesn't
    belong to their Company. We test only the read side here — the create
    side relies on require_manager + the location-belongs-to-company check
    inside the router (covered by mutation paths)."""
    from backend.models import Company, Location, Region, OwnershipGroup
    # Create a foreign Company + Region + Location
    other_og = OwnershipGroup(name="Other OG")
    other_co = Company(name="Other Co", slug="other-co", ownership_group_id=None)
    db_session.add_all([other_og, other_co])
    await db_session.flush()
    other_region = Region(company_id=other_co.id, name="R")
    db_session.add(other_region)
    await db_session.flush()
    other_loc = Location(
        company_id=other_co.id, region_id=other_region.id,
        name="OtherLoc", timezone="UTC",
    )
    db_session.add(other_loc)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": other_loc.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert resp.status_code == 404  # location not found within caller's Company
```

The fixtures `seed_location`, `seed_company`, `seed_shift_template`, and `manager_token` are existing in `tests/conftest.py`. If any of these names differs in the actual conftest, adapt to the real names — but do not invent new fixtures.

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_special_hours_api.py --no-header -q` → expect errors (router doesn't exist).

- [ ] **Step 3: Create the Pydantic schemas**

Create `backend/schemas/special_hours.py`:

```python
from datetime import date, datetime, time
from pydantic import BaseModel


class CreateSpecialHoursDayRequest(BaseModel):
    location_id: str
    date: date
    open_time: time
    close_time: time
    label: str | None = None
    draft_template_id: str | None = None


class UpdateSpecialHoursDayRequest(BaseModel):
    date: date | None = None
    open_time: time | None = None
    close_time: time | None = None
    label: str | None = None


class SpecialHoursDayResponse(BaseModel):
    id: str
    company_id: str
    location_id: str
    date: date
    open_time: time
    close_time: time
    label: str | None
    shift_template_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Create the router**

Create `backend/routers/special_hours.py`:

```python
"""Special Hours Days endpoints."""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, ShiftTemplate, SpecialHoursDay, User
from backend.schemas.special_hours import (
    CreateSpecialHoursDayRequest,
    SpecialHoursDayResponse,
    UpdateSpecialHoursDayRequest,
)
from backend.services.special_hours import clone_template_for_date

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/special-hours", tags=["special-hours"])


async def _verify_location_in_company(
    db: AsyncSession, location_id: str, company_id: str
) -> Location:
    loc = (await db.execute(
        select(Location).where(
            Location.id == location_id, Location.company_id == company_id
        )
    )).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return loc


async def _pick_recurring_template(
    db: AsyncSession, location_id: str
) -> ShiftTemplate:
    rows = (await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.location_id == location_id,
            ShiftTemplate.specific_date.is_(None),
        )
    )).scalars().all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "no_recurring_template",
                    "message": "Location has no recurring template"},
        )
    return rows[0]


@router.post("/", response_model=SpecialHoursDayResponse)
async def create_special_hours_day(
    body: CreateSpecialHoursDayRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> SpecialHoursDayResponse:
    if body.close_time <= body.open_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "close_before_open",
                    "message": "close_time must be after open_time"},
        )

    await _verify_location_in_company(
        db, body.location_id, str(current_user.company_id)
    )

    # Duplicate pre-check (UNIQUE constraint will also catch this — pre-check
    # gives us a clean 409 with a structured detail without a savepoint).
    existing = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.location_id == body.location_id,
            SpecialHoursDay.date == body.date,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "duplicate",
                    "message": "Special hours already exist for this date"},
        )

    # Resolve the draft template
    if body.draft_template_id:
        draft = (await db.execute(
            select(ShiftTemplate).where(
                ShiftTemplate.id == body.draft_template_id,
                ShiftTemplate.company_id == current_user.company_id,
                ShiftTemplate.location_id == body.location_id,
                ShiftTemplate.specific_date.is_(None),
            )
        )).scalar_one_or_none()
        if draft is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_draft_template",
                        "message": "Draft template not found or not a recurring template for this location"},
            )
    else:
        draft = await _pick_recurring_template(db, body.location_id)

    clone = await clone_template_for_date(
        db,
        source=draft,
        target_date=body.date,
        open_time=body.open_time,
        close_time=body.close_time,
        label=body.label,
    )

    row = SpecialHoursDay(
        company_id=current_user.company_id,
        location_id=body.location_id,
        date=body.date,
        open_time=body.open_time,
        close_time=body.close_time,
        label=body.label,
        shift_template_id=clone.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SpecialHoursDayResponse.model_validate(row)


@router.get("/", response_model=list[SpecialHoursDayResponse])
async def list_special_hours_days(
    location_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[SpecialHoursDayResponse]:
    q = select(SpecialHoursDay).where(
        SpecialHoursDay.company_id == current_user.company_id
    )
    if location_id is not None:
        q = q.where(SpecialHoursDay.location_id == location_id)
    if from_date is not None:
        q = q.where(SpecialHoursDay.date >= from_date)
    if to_date is not None:
        q = q.where(SpecialHoursDay.date <= to_date)
    q = q.order_by(SpecialHoursDay.date.asc(), SpecialHoursDay.location_id.asc())
    rows = (await db.execute(q)).scalars().all()
    return [SpecialHoursDayResponse.model_validate(r) for r in rows]


@router.put("/{special_hours_id}", response_model=SpecialHoursDayResponse)
async def update_special_hours_day(
    special_hours_id: str,
    body: UpdateSpecialHoursDayRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> SpecialHoursDayResponse:
    row = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.id == special_hours_id,
            SpecialHoursDay.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    new_open = body.open_time if body.open_time is not None else row.open_time
    new_close = body.close_time if body.close_time is not None else row.close_time
    if new_close <= new_open:
        raise HTTPException(
            status_code=400,
            detail={"code": "close_before_open",
                    "message": "close_time must be after open_time"},
        )

    times_changed = (
        body.open_time is not None or body.close_time is not None
    )
    date_changed = body.date is not None and body.date != row.date

    if body.date is not None:
        row.date = body.date
    if body.open_time is not None:
        row.open_time = body.open_time
    if body.close_time is not None:
        row.close_time = body.close_time
    if body.label is not None:
        row.label = body.label

    # Propagate to the linked template (weekly_schedule[0] only — manager
    # multi-day edits to the clone keep their other-day entries).
    if row.shift_template_id and (times_changed or date_changed):
        tmpl = (await db.execute(
            select(ShiftTemplate).where(ShiftTemplate.id == row.shift_template_id)
        )).scalar_one_or_none()
        if tmpl is not None:
            if date_changed:
                tmpl.specific_date = row.date
            if times_changed and tmpl.weekly_schedule:
                day0 = tmpl.weekly_schedule[0]
                open_str = row.open_time.strftime("%H:%M:%S")
                close_str = row.close_time.strftime("%H:%M:%S")
                for r in day0.get("roles", []):
                    r["start_time"] = open_str
                    r["end_time"] = close_str
                # SQLAlchemy doesn't auto-detect mutations to nested JSON.
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tmpl, "weekly_schedule")

    await db.commit()
    await db.refresh(row)
    return SpecialHoursDayResponse.model_validate(row)


@router.delete("/{special_hours_id}", status_code=204)
async def delete_special_hours_day(
    special_hours_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.id == special_hours_id,
            SpecialHoursDay.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    tmpl_id = row.shift_template_id
    await db.delete(row)
    if tmpl_id:
        tmpl = (await db.execute(
            select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
        )).scalar_one_or_none()
        if tmpl is not None:
            await db.delete(tmpl)
    await db.commit()
```

- [ ] **Step 5: Register the router**

Read `backend/main.py`. Find the multi-line `from backend.routers import (...)` block. Add `special_hours` alphabetically. Then add `app.include_router(special_hours.router, prefix=api_prefix)` near the related router include lines.

- [ ] **Step 6: Run, verify PASS**

Run: `pytest tests/test_special_hours_api.py --no-header -q` → expect 8 PASS.

Run: `pytest --no-header -q` → expect 0 regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/special_hours.py backend/schemas/special_hours.py \
        backend/main.py tests/test_special_hours_api.py
git commit -m "feat(backend): special-hours endpoints (create/list/update/delete)"
```

---

## Task 4: Template resolver

**Files:**
- Create: `backend/scheduling/template_resolver.py`
- Test: `tests/test_template_resolver.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_template_resolver.py`:

```python
"""Tests for resolve_templates_for_week."""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate
from backend.scheduling.template_resolver import (
    LocationMissingTemplate,
    resolve_templates_for_week,
)


WEEK = [date(2026, 12, 21) + i * __import__("datetime").timedelta(days=1)  # type: ignore[arg-type]
        for i in range(7)]  # 2026-12-21 .. 2026-12-27 (Mon..Sun)


@pytest.mark.asyncio
async def test_resolver_picks_specific_date_when_present(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly", weekly_schedule=[{"day_of_week": i, "roles": []} for i in range(7)],
    )
    special = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Xmas Eve", weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=date(2026, 12, 24),
    )
    db_session.add_all([recurring, special])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    assert result[date(2026, 12, 24)].id == special.id
    for d in WEEK:
        if d != date(2026, 12, 24):
            assert result[d].id == recurring.id


@pytest.mark.asyncio
async def test_resolver_returns_recurring_when_no_override(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly", weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(recurring)
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    for d in WEEK:
        assert result[d].id == recurring.id


@pytest.mark.asyncio
async def test_resolver_honours_selected_template_ids(
    db_session: AsyncSession, seed_location, seed_company
):
    a = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="A", weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    b = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="B", weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add_all([a, b])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=[b.id],
    )
    for d in WEEK:
        assert result[d].id == b.id


@pytest.mark.asyncio
async def test_resolver_raises_when_no_recurring_and_no_override(
    db_session: AsyncSession, seed_location
):
    with pytest.raises(LocationMissingTemplate):
        await resolve_templates_for_week(
            db_session,
            location_id=seed_location.id,
            week_dates=WEEK,
            selected_template_ids=None,
        )


@pytest.mark.asyncio
async def test_resolver_ignores_specific_dates_outside_the_week(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly", weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    outside = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Outside", weekly_schedule=[{"day_of_week": 0, "roles": []}],
        specific_date=date(2027, 1, 15),
    )
    db_session.add_all([recurring, outside])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    for d in WEEK:
        assert result[d].id == recurring.id
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_template_resolver.py --no-header -q` → expect ImportError.

- [ ] **Step 3: Implement `backend/scheduling/template_resolver.py`**

```python
"""Per-day ShiftTemplate resolution.

The scheduler needs to know, for each calendar date in the target week, which
ShiftTemplate to apply. Precedence:

1. ShiftTemplate where (location_id, specific_date) matches the date.
2. The recurring template the manager selected via selected_template_ids
   (filtered to the location's templates with specific_date IS NULL).
3. If no selection: the only recurring template for the location.

If a date has no specific_date row AND the location has no recurring template,
the resolver raises LocationMissingTemplate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


class LocationMissingTemplate(Exception):
    def __init__(self, location_id: str, missing_date: date):
        self.location_id = location_id
        self.missing_date = missing_date
        super().__init__(
            f"Location {location_id} has no template for {missing_date.isoformat()}"
        )


async def resolve_templates_for_week(
    db: AsyncSession,
    *,
    location_id: str,
    week_dates: list[date],
    selected_template_ids: list[str] | None,
) -> dict[date, ShiftTemplate]:
    """Return {date: ShiftTemplate} for every date in week_dates."""
    # 1. Pull all specific_date rows that intersect the week.
    specific_rows = (await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.location_id == location_id,
            ShiftTemplate.specific_date.in_(week_dates),
        )
    )).scalars().all()
    specific_by_date: dict[date, ShiftTemplate] = {
        t.specific_date: t for t in specific_rows if t.specific_date is not None
    }

    # 2. Pull recurring templates.
    recurring_q = select(ShiftTemplate).where(
        ShiftTemplate.location_id == location_id,
        ShiftTemplate.specific_date.is_(None),
    )
    if selected_template_ids:
        recurring_q = recurring_q.where(
            ShiftTemplate.id.in_(selected_template_ids)
        )
    recurring_rows = (await db.execute(recurring_q)).scalars().all()
    recurring = recurring_rows[0] if recurring_rows else None

    result: dict[date, ShiftTemplate] = {}
    for d in week_dates:
        chosen = specific_by_date.get(d) or recurring
        if chosen is None:
            raise LocationMissingTemplate(location_id, d)
        result[d] = chosen
    return result
```

- [ ] **Step 4: Run, verify PASS**

Run: `pytest tests/test_template_resolver.py --no-header -q` → expect 5 PASS.

Run: `pytest --no-header -q` → expect 0 regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/template_resolver.py tests/test_template_resolver.py
git commit -m "feat(backend): per-day template resolver"
```

---

## Task 5: Wire resolver into the scheduling pipeline

**Files:**
- Modify: `backend/scheduling/nodes.py`
- Modify: `backend/scheduling/state.py`
- Modify: `backend/scheduling/prompts.py`
- Modify: `backend/scheduling/local_scheduler.py`
- Test: `tests/test_schedule_pipeline.py` (additions)

This is the most invasive task. Read each modified file in full before editing.

- [ ] **Step 1: Update `SchedulingState`**

In `backend/scheduling/state.py`, change `shift_templates`:

```python
# Old:
shift_templates: dict        # keyed by location_id → ShiftTemplate

# New: keyed by location_id → {date: ShiftTemplate}
shift_templates: dict        # keyed by location_id → dict[date, ShiftTemplate]
```

The type hint is a comment + a TypedDict; just adjust the comment. The runtime value is a dict either way.

- [ ] **Step 2: Update `load_location_context` in `nodes.py`**

In `backend/scheduling/nodes.py::load_location_context`, replace the single-template load with a call to `resolve_templates_for_week`:

```python
from backend.scheduling.template_resolver import (
    LocationMissingTemplate, resolve_templates_for_week,
)
...
# Inside load_location_context, where it currently loads one template:
templates = await resolve_templates_for_week(
    db,
    location_id=current_location["id"],
    week_dates=week_dates,         # list[date] for the target window
    selected_template_ids=state.get("selected_template_ids"),
)
state["shift_templates"][current_location["id"]] = templates
```

If `LocationMissingTemplate` is raised, append to `state["errors"]` and skip the location (mirrors the existing parse-error pattern — never raise inside a node).

`week_dates` is `[week_start_date + timedelta(days=i) for i in range(num_days)]`. `num_days` is already on the state today.

- [ ] **Step 3: Update `build_prompt` in `prompts.py`**

In `backend/scheduling/prompts.py::build_schedule_prompt`, the current `_format_role_requirements` iterates `shift_template.weekly_schedule[]` by day_of_week. Change the call site (and the helper) so it iterates dates in the window and looks up the per-date template:

```python
def _format_role_requirements(
    templates_by_date: dict[date, dict],   # ShiftTemplate-shape dict per date
    week_dates: list[date],
) -> str:
    lines = []
    for d in week_dates:
        tmpl = templates_by_date[d]
        for entry in tmpl["weekly_schedule"]:
            # entry shape: {"day_of_week": int, "roles": [...]}
            for role in entry["roles"]:
                lines.append(
                    f"{d.strftime('%A')} {d.isoformat()} | "
                    f"{role['role_name']} | "
                    f"{role['required_headcount']} | "
                    f"{role['start_time']} - {role['end_time']}"
                )
    return "\n".join(lines)
```

If the existing function signature differs, adapt — the spirit is the same: walk dates, look up the date's template, emit one line per role per date.

- [ ] **Step 4: Update `local_scheduler.py`**

In `backend/scheduling/local_scheduler.py`, look for `template.weekly_schedule[dow]` lookups. Replace with a helper that handles both shapes:

```python
def _roles_for_date(template, target_date) -> list[dict]:
    """Resolve the list of role-requirement dicts for `target_date` from a
    template that may be (a) a recurring 7-day template or (b) a 1-day
    specific_date clone with a single entry."""
    if getattr(template, "specific_date", None) is not None:
        # Single-day clone — its weekly_schedule has length 1.
        days = template.weekly_schedule or []
        return days[0].get("roles", []) if days else []
    dow = target_date.weekday()
    for entry in template.weekly_schedule or []:
        if entry.get("day_of_week") == dow:
            return entry.get("roles", [])
    return []
```

Then every existing `template.weekly_schedule[dow]` style lookup becomes `_roles_for_date(templates[target_date], target_date)`. Read the file carefully — there are likely multiple call sites.

- [ ] **Step 5: Add pipeline tests**

Append to `tests/test_schedule_pipeline.py`:

```python
async def test_pipeline_uses_specific_date_template_when_present(
    db_session, seed_location, seed_company, seed_shift_template, monkeypatch
):
    """When the week contains a date with a specific_date override, the
    LangGraph state should pick up the override for that day and the
    recurring template for others."""
    from datetime import date, time
    from backend.scheduling.template_resolver import resolve_templates_for_week
    from backend.models import ShiftTemplate

    override = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Override",
        weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=date(2026, 12, 24),
    )
    db_session.add(override)
    await db_session.commit()

    week = [date(2026, 12, 21) + __import__("datetime").timedelta(days=i)
            for i in range(7)]
    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=week,
        selected_template_ids=None,
    )
    assert result[date(2026, 12, 24)].id == override.id
    for d in week:
        if d != date(2026, 12, 24):
            assert result[d].id == seed_shift_template.id
```

(This is a smaller-than-ideal pipeline test — the resolver test already covers the core logic. A fuller end-to-end test of the LangGraph pipeline with mocked LLM is welcome if the existing harness makes that cheap; if it's expensive, the resolver-level test is the load-bearing one.)

- [ ] **Step 6: Run, verify PASS**

Run: `pytest tests/test_schedule_pipeline.py --no-header -q` → expect previous pass count + 1 new = same baseline + 1.

Run: `pytest --no-header -q` → expect 0 regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/scheduling/nodes.py backend/scheduling/state.py \
        backend/scheduling/prompts.py backend/scheduling/local_scheduler.py \
        tests/test_schedule_pipeline.py
git commit -m "feat(backend): scheduler picks per-day templates"
```

---

## Task 6: Frontend API client + types

**Files:**
- Create: `frontend/src/api/specialHours.ts`
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Add TS types**

In `frontend/src/types/index.ts`, append at the end:

```ts
// ── Special Hours ──

export interface SpecialHoursDay {
  id: string;
  company_id: string;
  location_id: string;
  date: string;          // YYYY-MM-DD
  open_time: string;     // HH:MM:SS
  close_time: string;    // HH:MM:SS
  label: string | null;
  shift_template_id: string | null;
  created_at: string;
}
```

- [ ] **Step 2: Create `frontend/src/api/specialHours.ts`**

```ts
import { apiFetch } from "./client";
import type { SpecialHoursDay } from "../types";

export interface CreateSpecialHoursDayArgs {
  location_id: string;
  date: string;
  open_time: string;
  close_time: string;
  label?: string | null;
  draft_template_id?: string | null;
}

export interface UpdateSpecialHoursDayArgs {
  date?: string;
  open_time?: string;
  close_time?: string;
  label?: string | null;
}

export function listSpecialHours(params?: {
  location_id?: string;
  from_date?: string;
  to_date?: string;
}): Promise<SpecialHoursDay[]> {
  const qs = new URLSearchParams();
  if (params?.location_id) qs.set("location_id", params.location_id);
  if (params?.from_date) qs.set("from_date", params.from_date);
  if (params?.to_date) qs.set("to_date", params.to_date);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<SpecialHoursDay[]>(`/special-hours/${suffix}`);
}

export function createSpecialHoursDay(
  args: CreateSpecialHoursDayArgs,
): Promise<SpecialHoursDay> {
  return apiFetch<SpecialHoursDay>("/special-hours/", {
    method: "POST",
    body: JSON.stringify(args),
  });
}

export function updateSpecialHoursDay(
  id: string,
  args: UpdateSpecialHoursDayArgs,
): Promise<SpecialHoursDay> {
  return apiFetch<SpecialHoursDay>(`/special-hours/${id}`, {
    method: "PUT",
    body: JSON.stringify(args),
  });
}

export function deleteSpecialHoursDay(id: string): Promise<void> {
  return apiFetch<void>(`/special-hours/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: TS build smoke check**

Run: `cd frontend && ./node_modules/.bin/tsc --noEmit` → expect 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/specialHours.ts frontend/src/types/index.ts
git commit -m "feat(billing-fe): specialHours API wrappers + types"
```

---

## Task 7: `/manager/special-hours` page + create/edit modal

**Files:**
- Create: `frontend/src/pages/manager/SpecialHours.tsx`
- Create: `frontend/src/components/shared/SpecialHoursModal.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/i18n/en.ts` + 18 other locales

- [ ] **Step 1: Build `SpecialHoursModal.tsx`**

Read `frontend/src/pages/manager/Team.tsx` first for the modal style + form pattern used in the team-and-locking PR. Mirror it.

The modal accepts:

```ts
interface Props {
  onClose: () => void;
  onSaved: (row: SpecialHoursDay) => void;
  editing: SpecialHoursDay | null;
  locations: Location[];           // pre-loaded by parent
  templatesByLocation: Record<string, ShiftTemplate[]>;  // filtered to recurring (specific_date IS NULL)
}
```

Form fields (validation client-side + server-side):
- Date (required, `<input type="date">`)
- Location (required, single-select dropdown)
- Open (required, `<input type="time">`)
- Close (required, `<input type="time">`, must be > Open)
- Label (optional)
- Starting draft template (required on create; **hidden on edit**) — single-select dropdown of `templatesByLocation[chosenLocationId]`

Submit:
- Create: `createSpecialHoursDay({...})`. Handle 400 `no_recurring_template` and 409 `duplicate` with inline error messages from `t.specialHours.*` keys.
- Edit: `updateSpecialHoursDay(editing.id, { date, open_time, close_time, label })`.

Use `t.specialHours.draftTemplateLabel`, `t.specialHours.draftTemplateHelp`, `t.specialHours.openLabel`, etc.

- [ ] **Step 2: Build `SpecialHours.tsx` page**

Read `frontend/src/pages/manager/Employees.tsx` for the page layout (filter bar + table + Add button + theme tokens). Mirror.

Component state:
- `entries: SpecialHoursDay[]`
- `locations: Location[]`
- `templates: ShiftTemplate[]` (all templates for the company)
- `locationFilter: string` (default "all")
- `dateRange: { from: string; to: string } | null` (optional — start without it; can be added later)
- `editing: SpecialHoursDay | null` (drives the modal)
- `duplicating: SpecialHoursDay | null` (drives the Duplicate modal — Task 8 adds it)

Effects:
- On mount: `Promise.all([listSpecialHours(), listLocations(), listShiftTemplates()])` to populate state.
- Re-fetch entries on filter change.

Render:
- H1 + description.
- Top bar: Location dropdown + "+ Add special hours" button.
- Table: Date · Location · Hours · Label · Template · Actions.
- Empty state when zero entries.

Per-row actions:
- **Edit** → opens modal with `editing` set.
- **Duplicate** → sets `duplicating` (modal lands in Task 8).
- **Delete** → `confirm(t.specialHours.deleteConfirm)` → `deleteSpecialHoursDay(id)` → refresh.

- [ ] **Step 3: Register the route in `App.tsx`**

```tsx
import SpecialHours from "./pages/manager/SpecialHours";
// ...
<Route path="special-hours" element={<SpecialHours />} />
```

Place the new `<Route>` inside the `/manager` block, alphabetically near `regions` / `roles` / `shift-templates`.

- [ ] **Step 4: Sidebar entry**

In `frontend/src/components/layout/Sidebar.tsx`, in `baseManagerLinks` insert between the `locations` and `roles` entries:

```ts
{ to: "/manager/special-hours", labelKey: "specialHours" },
```

- [ ] **Step 5: i18n in `en.ts`**

Add the full `specialHours: { ... }` block from §4.5 of the spec to `frontend/src/i18n/en.ts`, plus `nav.specialHours: "Special Hours"`.

- [ ] **Step 6: i18n in 18 other locales**

Use a small script (mirror the pattern from PR #32 / PR #31) to insert the same English copy into all 18 non-English locale files. Anchor on the existing `nav:` block + a stable existing block as the insertion point.

```bash
python3 <<'PY'
import re, pathlib

LOCALES = "ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh".split()
NEW_BLOCK = '''
  // ── Special Hours ──
  specialHours: {
    title: "Special Hours",
    description: "Days with non-standard operating hours — the scheduler will use the linked template instead of the regular weekly one.",
    addButton: "Add special hours",
    columnDate: "Date",
    columnLocation: "Location",
    columnHours: "Hours",
    columnLabel: "Label",
    columnTemplate: "Template",
    columnActions: "Actions",
    labelPlaceholder: "e.g. Christmas Eve, Thanksgiving",
    openLabel: "Open",
    closeLabel: "Close",
    draftTemplateLabel: "Starting draft template",
    draftTemplateHelp: "We'll clone this template for the special day. You can edit the clone afterwards.",
    duplicateTitle: "Duplicate to other locations",
    duplicateHelp: "Pick the locations to copy this special hours entry to. Each will get its own cloned template.",
    deleteConfirm: "Delete this special hours entry? The cloned template will also be removed.",
    noEntries: "No special hours configured yet.",
    noRecurringTemplate: "{location} has no recurring template — create one before adding special hours.",
    closeAfterOpen: "Close time must be after open time.",
    scheduleBadge: "Special hours",
  },
'''

NAV_INSERT = '    specialHours: "Special Hours",\n'

for loc in LOCALES:
    p = pathlib.Path(f"frontend/src/i18n/{loc}.ts")
    src = p.read_text()
    if "specialHours: {" in src:
        print(f"{loc}: already has block, skipping"); continue
    # Insert nav key before the nav block's closing `},`. Anchor on an existing
    # nav key that's near the end (`team:` exists in all locales after the team
    # PR — but if not present, fall back to dataPrivacy).
    nav_anchor = re.search(r'(    team: "[^"]*",\n)(  \},)', src) \
              or re.search(r'(    dataPrivacy: "[^"]*",\n)(  \},)', src)
    if not nav_anchor:
        print(f"{loc}: nav anchor not found"); continue
    src = src[:nav_anchor.end(1)] + NAV_INSERT + src[nav_anchor.end(1):]

    # Insert top-level block before a stable anchor — `// ── Company ──`.
    if "// ── Company ──" not in src:
        print(f"{loc}: Company anchor not found"); continue
    src = src.replace("// ── Company ──", NEW_BLOCK.strip() + "\n\n  // ── Company ──", 1)
    p.write_text(src)
    print(f"{loc}: inserted")
PY
```

- [ ] **Step 7: TS build smoke check**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/manager/SpecialHours.tsx \
        frontend/src/components/shared/SpecialHoursModal.tsx \
        frontend/src/App.tsx frontend/src/components/layout/Sidebar.tsx \
        frontend/src/types/index.ts frontend/src/i18n/
git commit -m "feat(billing-fe): /manager/special-hours page + create/edit modal"
```

---

## Task 8: Duplicate-to-other-locations modal

**Files:**
- Create: `frontend/src/components/shared/DuplicateSpecialHoursModal.tsx`
- Modify: `frontend/src/pages/manager/SpecialHours.tsx`

- [ ] **Step 1: Build `DuplicateSpecialHoursModal.tsx`**

```tsx
import { useState } from "react";
import { createSpecialHoursDay } from "../../api/specialHours";
import type { Location, SpecialHoursDay } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, border } from "../../theme";

interface Props {
  source: SpecialHoursDay;
  locations: Location[];        // all locations EXCEPT source.location_id
  onClose: () => void;
  onCompleted: (created: SpecialHoursDay[], errors: { location_id: string; message: string }[]) => void;
}

export default function DuplicateSpecialHoursModal({
  source, locations, onClose, onCompleted,
}: Props) {
  const { t } = useLanguage();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    const created: SpecialHoursDay[] = [];
    const errors: { location_id: string; message: string }[] = [];
    await Promise.all(
      Array.from(selected).map(async (loc_id) => {
        try {
          const row = await createSpecialHoursDay({
            location_id: loc_id,
            date: source.date,
            open_time: source.open_time,
            close_time: source.close_time,
            label: source.label,
            // No draft_template_id — server picks the location's recurring.
          });
          created.push(row);
        } catch (err: unknown) {
          const message = err instanceof Error ? err.message : "Failed";
          errors.push({ location_id: loc_id, message });
        }
      })
    );
    setSubmitting(false);
    onCompleted(created, errors);
    onClose();
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h2 className={`text-lg font-semibold ${text.body}`}>
            {t.specialHours.duplicateTitle}
          </h2>
          <button onClick={onClose} className={`${text.muted} text-xl leading-none`}>&times;</button>
        </div>
        <div className="px-6 py-4 space-y-3">
          <p className={`text-sm ${text.muted}`}>{t.specialHours.duplicateHelp}</p>
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {locations.map((loc) => (
              <label key={loc.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={selected.has(loc.id)}
                  onChange={() => toggle(loc.id)}
                />
                <span className={text.body}>{loc.name}</span>
              </label>
            ))}
          </div>
        </div>
        <div className={`flex justify-end gap-3 px-6 py-4 border-t ${border.default}`}>
          <button onClick={onClose} className="glass-btn-secondary px-4 py-2 text-sm" disabled={submitting}>
            {t.common.cancel}
          </button>
          <button
            onClick={handleConfirm}
            disabled={submitting || selected.size === 0}
            className="glass-btn-primary px-4 py-2 text-sm disabled:opacity-50"
          >
            {submitting ? t.common.loading : `${t.common.confirm} (${selected.size})`}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire it into `SpecialHours.tsx`**

Render the modal when `duplicating !== null`. `onCompleted` should append created rows to the page's entries state and (if there are errors) surface a non-blocking inline message:

```tsx
{duplicating && (
  <DuplicateSpecialHoursModal
    source={duplicating}
    locations={locations.filter((l) => l.id !== duplicating.location_id)}
    onClose={() => setDuplicating(null)}
    onCompleted={(created, errors) => {
      setEntries((prev) => [...prev, ...created].sort(byDate));
      if (errors.length > 0) {
        setNotice(
          errors.map((e) => {
            const loc = locations.find((l) => l.id === e.location_id);
            return t.specialHours.noRecurringTemplate.replace("{location}", loc?.name ?? e.location_id);
          }).join(" "),
        );
      }
    }}
  />
)}
```

- [ ] **Step 3: TS build smoke check**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/shared/DuplicateSpecialHoursModal.tsx \
        frontend/src/pages/manager/SpecialHours.tsx
git commit -m "feat(billing-fe): duplicate-to-other-locations modal"
```

---

## Task 9: Schedule-page badge

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx`

- [ ] **Step 1: Fetch special hours alongside the week's data**

Read the existing Schedule.tsx. Find where it loads data after the week is selected. Add a parallel call to `listSpecialHours({ from_date, to_date })`:

```ts
const [specialHours, setSpecialHours] = useState<SpecialHoursDay[]>([]);

useEffect(() => {
  if (!weekStart) return;
  const from_date = weekStart;
  const to_date = addDays(weekStart, 6);  // existing helper, or inline computation
  listSpecialHours({ from_date, to_date }).then(setSpecialHours).catch(() => setSpecialHours([]));
}, [weekStart]);
```

- [ ] **Step 2: Render the badge**

Where each per-location card renders its per-day header (typically a row of `Mon Tue Wed ...` or per-date cells), look up `specialHours.find(s => s.location_id === card.location_id && s.date === thisDate)`. When present, render a small inline badge:

```tsx
{match && (
  <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-900">
    ★ {match.label ?? t.specialHours.scheduleBadge} · {fmtHM(match.open_time)}–{fmtHM(match.close_time)}
  </span>
)}
```

`fmtHM` strips the seconds and renders `HH:MM`. Add a small helper inline.

- [ ] **Step 3: TS build smoke check**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit
```
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx
git commit -m "feat(billing-fe): special-hours badge on /manager/schedule"
```

---

## Task 10: Final verification + push + PR

- [ ] **Step 1: Full backend test suite**

Run: `pytest --no-header -q`
Expected: previous baseline + 4 (models) + 3 (clone service) + 8 (API) + 5 (resolver) + 1 (pipeline) = +21 new tests.

- [ ] **Step 2: Frontend type check + production build**

```bash
cd frontend && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build
```
Expected: clean.

- [ ] **Step 3: Manual smoke**

- [ ] /manager/special-hours appears in sidebar between Locations and Roles.
- [ ] + Add special hours → fill form (pick a recurring template as draft) → save → row appears in the table, cloned ShiftTemplate appears on /manager/shift-templates with name like "Weekly — Christmas Eve".
- [ ] Edit the entry, change open from 09:00 to 10:00 → the cloned template's role times also update to 10:00:00.
- [ ] Duplicate to another location with a recurring template → both rows appear.
- [ ] Duplicate to a location without a recurring template → inline error: "X has no recurring template — create one before adding special hours."
- [ ] On /manager/schedule, the week containing the special-hours date shows a ★ badge on the affected day.
- [ ] Click Generate → backend uses the override for that day, the recurring template for others. Inspect the resulting shifts.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin feat-special-hours-days
```

- [ ] **Step 5: Open the PR**

```bash
gh pr create --title "Special hours days + per-day shift-template overrides" --body "$(cat <<'EOF'
## Summary
- New /manager/special-hours page lets managers define per-location, per-date operating hours.
- On entry, the system clones a chosen recurring ShiftTemplate into a one-day variant (specific_date set, role times overridden to the special open/close).
- The scheduler resolves templates per day: override wins on its date, the recurring template applies on every other day.
- Row-level Duplicate-to-other-locations action mints copies (with cloned templates) for sibling locations.
- Schedule page shows a ★ badge on days with special hours so the manager sees the override is in effect.
- Google Business Profile integration is out of scope for this PR (deferred per the spec).

## Migration
0025_add_special_hours_days_and_shift_template_specific_date — adds `shift_templates.specific_date` (nullable) + `special_hours_days` table. No backfill required.

## Test plan
- [x] 21 new backend tests pass; full suite green
- [x] Frontend `tsc --noEmit` + `vite build` clean
- [ ] After deploy: walk through the manual-smoke checklist in the plan's Task 10 Step 3

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Note the PR URL in your final report.**

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-05-23-special-hours-days-and-per-day-templates-design.md`):
- ✅ §1.1 ShiftTemplate.specific_date column — Task 1
- ✅ §1.2 SpecialHoursDay table — Task 1
- ✅ §1.3 Migration 0025 — Task 1
- ✅ §2 Endpoints + clone flow — Tasks 2, 3
- ✅ §3 Template resolver + scheduler wiring — Tasks 4, 5
- ✅ §4.1 New page + sidebar entry — Task 7
- ✅ §4.2 Modal — Task 7
- ✅ §4.3 Duplicate modal — Task 8
- ✅ §4.4 Schedule-page badge — Task 9
- ✅ §4.5 i18n in 19 locales — Task 7

**Type/name consistency:**
- `clone_template_for_date(db, *, source, target_date, open_time, close_time, label)` — Tasks 2, 3 ✓
- `resolve_templates_for_week(db, *, location_id, week_dates, selected_template_ids)` — Tasks 4, 5 ✓
- `LocationMissingTemplate` exception — Tasks 4, 5 ✓
- `SpecialHoursDay.shift_template_id` nullable — Tasks 1, 3 ✓
- Frontend `SpecialHoursDay` interface matches `SpecialHoursDayResponse` schema — Tasks 6, 7 ✓

**No placeholders.** Every code block is concrete. Where a step modifies a large existing file (scheduler nodes, prompts, local_scheduler), the engineer is told to read the file first because the diff is too large to inline safely.

**Risk callouts:**
- The scheduler-pipeline change (Task 5) is the highest-risk single task. It touches multiple files in the scheduling/ directory. Tests at the resolver level (Task 4) catch the core logic; pipeline tests catch wiring. If `local_scheduler.py` has more `weekly_schedule[dow]` call sites than expected, the implementer should escalate rather than mass-replace.
- SQLite's lack of true partial-index support means the migration's `postgresql_where` clause is Postgres-only. SQLite tests don't exercise the index but the schema-load works because the partial-index clause is silently dropped — confirm by running the test suite.
- Frontend tests don't exist yet (no infra). Manual smoke in Task 10 is the verification gate before the PR opens.
