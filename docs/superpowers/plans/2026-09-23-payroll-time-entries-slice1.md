# Payroll Time Entries, Slice 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the provider-agnostic payroll core for issue #78 — attendance-verified `TimeEntry` rows, a manager exception queue with attestation, approval, a CSV export with an audit row and one-way `exported_at` idempotency, a retention sweep, and an attestation-rate block on the existing check-in report.

**Architecture:** Two new tables (`time_entries`, `payroll_exports`) in Alembic migration `0036`. Pure functions (`paid_minutes`, `pay_date_for`) carry the wall-clock and midnight-crossing rules and are unit-tested with no DB. Session-bound services in `backend/services/time_entries.py` and `backend/services/payroll_export.py` do all derivation, attestation, approval and rendering; a thin router `backend/routers/payroll.py` translates HTTP to them. The frontend gets one manager page, one API wrapper, one pure formatter module, and new i18n keys in all 19 locales.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x (Async), Alembic, PostgreSQL (SQLite+aiosqlite in tests), pytest + pytest-asyncio + httpx; React 18 + TypeScript + Vite + Tailwind, vitest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-23-payroll-time-entries-slice1-design.md` — binding. Read it alongside this plan.

## Global Constraints

Copied verbatim from the spec and `CLAUDE.md`. Every task's requirements implicitly include this section.

- **Paid hours are the scheduled hours.** `Shift.start_time` → `Shift.end_time`. The #63 check-in is a **gate, not a stopwatch**: it decides *whether* the shift is payable, never *how many* hours it pays.
- **Lateness is surfaced, never docked.** No code path subtracts it from pay.
- **A missed scan does not strand a shift.** An approved shift in the pay range with no matching check-in lands in a manager exception queue.
- **Attestation carries a real audit trail.** `source = manager_attested`, the attesting user, the timestamp, an optional reason.
- **Nothing exports unapproved.**
- **Paid plans only**, via `assert_paid_plan`.
- **Attestation rate is reporting, not blocking.** Nothing reads `rate` to block an attestation, refuse an export, or cap anything.
- **The whole shift's hours land on the location-local calendar date the shift started.** No shift is ever split across two pay dates. `pay_date = start_time.astimezone(ZoneInfo(location.timezone)).date()` — never from `Shift.date`, never from the UTC instant.
- **We do not re-derive the shift↔check-in match.** Join on `EmployeeCheckIn.shift_id == Shift.id AND EmployeeCheckIn.status IN ('matched', 'duplicate')`, taking the **earliest `checked_in_at`**.
- **`time_entries` are not swept.** Their `exported_at` outlives the export log deliberately.
- **Roles are never hardcoded.** No role name string literal may appear outside `seed.py`. Every role reference must come from the `roles` table.
- **"Today" is always UTC** — `datetime.now(timezone.utc).date()`, never `date.today()`, in application code *and* in tests. `tests/test_utc_today.py` enforces this by AST sweep.
- All timestamps must carry timezone offsets (derive from `location.timezone` via `zoneinfo.ZoneInfo`).
- **Use logical, not physical, direction utilities in Tailwind** — `text-start` not `text-left`, `ms-2` not `ml-2`, `border-s` not `border-l`, `start-0` not `left-0`. `frontend/src/utils/logicalDirection.test.ts` enforces this.
- Use type hints extensively in all Python code. Prefer functional components and hooks in React.
- Parse defensively — never raise unhandled exceptions out of a derivation loop.
- **Do not install new dependencies.**
- **Every new i18n key must be added to all 19 locale files with a real translation** — not English placeholders. `TranslationKeys` is `typeof import("./en").default`, so a missing key fails `npm run build`.

### How to run things (this worktree)

- **Backend tests**, from the worktree root: `../../../backend/.venv/bin/python -m pytest tests/<file> -q`
- **Frontend tests**: `cd frontend && npm test` (runs under `TZ=Asia/Tokyo`)
- **Frontend build / typecheck**: `cd frontend && npm run build`
- `frontend/node_modules` is a **symlink** into the main checkout. **Never run `npm install`.**
- Never use bare `git stash` — the stash stack is shared across worktrees.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/models/time_entry.py` (create) | `TimeEntry` ORM model, source constants, constraints, indexes |
| `backend/models/payroll_export.py` (create) | `PayrollExport` audit-row ORM model |
| `backend/models/__init__.py` (modify) | Register both models and add to `__all__` |
| `backend/alembic/versions/0036_add_time_entries_and_payroll_exports.py` (create) | Migration `0036`, `down_revision = "0035"` |
| `backend/services/time_entries.py` (create) | Pure `paid_minutes` / `pay_date_for`; derivation, listing, exceptions, attestation, approval, attestation rates |
| `backend/services/payroll_export.py` (create) | `CSV_HEADER`, `PayrollCsvRow`, pure `render_csv`, `export_approved` |
| `backend/schemas/payroll.py` (create) | Request/response Pydantic models for the payroll router |
| `backend/routers/payroll.py` (create) | `/payroll` endpoints: derive, entries, exceptions, attest, approve, export |
| `backend/main.py` (modify) | Import and register `payroll.router` after `check_ins.router` |
| `backend/config.py` (modify) | `RETENTION_PAYROLL_EXPORT_LOGS_DAYS: int = 365` |
| `backend/services/data_retention.py` (modify) | Step 7 nulls `time_entries.check_in_id`; new step 9 sweeps `payroll_exports` |
| `backend/schemas/check_in.py` (modify) | `AttestationRateRow`; `attestation` on `CheckInReportResponse` |
| `backend/routers/check_ins.py` (modify) | Populate `attestation` on `GET /check-ins/report` |
| `frontend/src/types/index.ts` (modify) | Payroll types beside the check-in block; `attestation` on `CheckInReport` |
| `frontend/src/api/payroll.ts` (create) | Typed fetch wrappers, including the blob download |
| `frontend/src/utils/payrollHours.ts` (create) | Pure `formatPaidHours` / `formatLateness` |
| `frontend/src/i18n/{19 files}.ts` (modify) | `nav.payroll`, the `payroll` block, two `checkIn` keys |
| `frontend/src/pages/manager/Payroll.tsx` (create) | The manager page |
| `frontend/src/pages/manager/CheckInReport.tsx` (modify) | The attestation-rate panel, beside the punctuality report |
| `frontend/src/App.tsx` (modify) | `<Route path="payroll" element={<Payroll />} />` |
| `frontend/src/components/layout/Sidebar.tsx` (modify) | Nav entry in `groupCheckIn` |
| `tests/test_time_entry_model.py` … `tests/test_payroll_end_to_end.py` (create) | One file per concern, per the `test_check_in_*.py` split |

---

### Task 1: Models and migration 0036

**Files:**
- Create: `backend/models/time_entry.py`
- Create: `backend/models/payroll_export.py`
- Create: `backend/alembic/versions/0036_add_time_entries_and_payroll_exports.py`
- Modify: `backend/models/__init__.py`
- Test: `tests/test_time_entry_model.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Existing models `Company`, `Location`, `Employee`, `Role`, `Shift`, `ShiftSchedule`, `User`, `EmployeeCheckIn`, `OwnershipGroup`; `backend.utils.id_gen.generate_short_id`; `backend.database.Base`.
- Produces:
  - `backend.models.time_entry.TimeEntry` — columns `id, company_id, location_id, employee_id, shift_id, role_id, role_name, pay_date, start_time, end_time, paid_minutes, source, check_in_id, checked_in_at, lateness_minutes, attested_by_user_id, attested_at, attestation_reason, approved_at, approved_by_user_id, exported_at, payroll_export_id, created_at`
  - `backend.models.time_entry.TIME_ENTRY_CHECKED_IN: str = "checked_in"`
  - `backend.models.time_entry.TIME_ENTRY_MANAGER_ATTESTED: str = "manager_attested"`
  - `backend.models.payroll_export.PayrollExport` — columns `id, company_id, ownership_group_id, exported_by_user_id, format, location_id, range_start, range_end, entry_count, paid_minutes_total, created_at`
  - Both re-exported from `backend.models`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_entry_model.py`:

```python
"""The database, not the application, is what keeps payroll honest.

Every constraint here exists because application logic can be forgotten by a
future caller and a constraint cannot — the same posture as
uq_employee_check_ins_location_date_counter.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    Location,
    Region,
    Role,
    Shift,
    ShiftSchedule,
    TimeEntry,
    User,
)
from backend.models.time_entry import (
    TIME_ENTRY_CHECKED_IN,
    TIME_ENTRY_MANAGER_ATTESTED,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


async def _seed(db: AsyncSession) -> SimpleNamespace:
    """A company with one approved 09:00-17:00 UTC shift yesterday.

    Shift timestamps are built with tzinfo=timezone.utc directly, never from
    an offset-bearing ISO string: SQLite strips tzinfo on round-trip and would
    silently shift an offset-bearing value (see _as_utc in
    backend/services/check_in.py).
    """
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()

    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone="America/New_York"))
    # Role name comes from the roles table, never a literal in feature code.
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id, full_name="Dana Okafor",
                    email=f"{employee_id}@example.com", location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.flush()

    schedule_id, shift_id = _id(), _id()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    start = yesterday.replace(hour=9, minute=0, second=0, microsecond=0)
    db.add(ShiftSchedule(id=schedule_id, company_id=company_id,
                         location_id=location_id, week_start_date=start.date(),
                         status="approved"))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=company_id, shift_schedule_id=schedule_id,
                 location_id=location_id, employee_id=employee_id, role_id=role_id,
                 role_name=role.name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=8)))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, employee_id=employee_id,
        role_id=role_id, role_name=role.name, user_id=user_id, shift_id=shift_id,
        pay_date=start.date(), start=start, end=start + timedelta(hours=8),
    )


def _entry(s: SimpleNamespace, **overrides) -> TimeEntry:
    fields = dict(
        id=_id(), company_id=s.company_id, location_id=s.location_id,
        employee_id=s.employee_id, shift_id=s.shift_id, role_id=s.role_id,
        role_name=s.role_name, pay_date=s.pay_date, start_time=s.start,
        end_time=s.end, paid_minutes=480, source=TIME_ENTRY_CHECKED_IN,
    )
    fields.update(overrides)
    return TimeEntry(**fields)


async def test_one_entry_per_shift(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s))
    await db_session.commit()

    db_session.add(_entry(s))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_unknown_source_is_rejected(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s, source="guessed"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_attested_entry_needs_an_attester(db_session: AsyncSession):
    """An attested row without an attester is an audit trail with a hole."""
    s = await _seed(db_session)
    db_session.add(_entry(s, source=TIME_ENTRY_MANAGER_ATTESTED,
                          attested_by_user_id=None, attested_at=None))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_an_attested_entry_with_an_attester_is_accepted(
    db_session: AsyncSession
):
    s = await _seed(db_session)
    db_session.add(_entry(s, source=TIME_ENTRY_MANAGER_ATTESTED,
                          attested_by_user_id=s.user_id,
                          attested_at=datetime.now(timezone.utc)))
    await db_session.commit()


async def test_zero_paid_minutes_is_rejected(db_session: AsyncSession):
    s = await _seed(db_session)
    db_session.add(_entry(s, paid_minutes=0))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entry_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'TimeEntry' from 'backend.models'`

- [ ] **Step 3: Create `backend/models/time_entry.py`**

```python
"""A payable shift: scheduled hours, gated by an arrival or a manager's word.

Derived from Shift x EmployeeCheckIn, never hand-authored. Paid hours are the
SCHEDULED hours (#78) — the check-in decides WHETHER a shift is payable, never
HOW MANY hours it pays.

checked_in_at and lateness_minutes are denormalised rather than read through
check_in_id because check-ins are swept at RETENTION_CHECKINS_DAYS and a pay
record must still be able to say what it was based on afterwards. role_name is
a snapshot for the same reason: a role renamed in March must not silently
rewrite January's payroll export.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# The gating scan matched this shift.
TIME_ENTRY_CHECKED_IN = "checked_in"
# No scan; a manager confirmed on the employee's behalf. Phones die.
TIME_ENTRY_MANAGER_ATTESTED = "manager_attested"


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        # One entry per shift, enforced by the database. This is what makes
        # derivation idempotent and what stops an attestation racing a
        # derivation into two payable rows for the same shift.
        UniqueConstraint("shift_id", name="uq_time_entries_shift"),
        CheckConstraint(
            "source IN ('checked_in', 'manager_attested')",
            name="time_entries_source_check",
        ),
        CheckConstraint(
            "source <> 'manager_attested' OR "
            "(attested_by_user_id IS NOT NULL AND attested_at IS NOT NULL)",
            name="time_entries_attested_check",
        ),
        CheckConstraint(
            "paid_minutes > 0", name="time_entries_paid_minutes_check"
        ),
        Index("ix_time_entries_company_pay_date", "company_id", "pay_date"),
        Index("ix_time_entries_company_location_pay_date",
              "company_id", "location_id", "pay_date"),
        Index("ix_time_entries_company_employee_pay_date",
              "company_id", "employee_id", "pay_date"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    shift_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("shifts.id"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("roles.id"), nullable=False
    )
    # Snapshot of Shift.role_name, itself sourced from the roles table.
    role_name: Mapped[str] = mapped_column(String, nullable=False)
    # The location-local calendar date the shift STARTED. Whole and unsplit.
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    paid_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nulled by the check-in retention sweep; checked_in_at survives it.
    check_in_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("employee_check_ins.id"), nullable=True
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Signed, negative early. NULL for attested entries: inventing a lateness
    # of zero would put a fact in the export that nobody observed.
    lateness_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attested_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
    attested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attestation_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
    # One-way idempotency marker, mirroring Shift.exported_at. Outlives the
    # payroll_exports row it points at, deliberately.
    exported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payroll_export_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("payroll_exports.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
```

- [ ] **Step 4: Create `backend/models/payroll_export.py`**

```python
"""Per-export audit row: who pulled which hours, when, covering what.

Shaped like integration_imports (#44) and gdpr_export_log (#45). This slice
has no cooldown — a CSV download costs nothing and hits no third party — so
the table is audit only, and the quota shape is available later without a
schema change.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class PayrollExport(Base):
    __tablename__ = "payroll_exports"
    __table_args__ = (
        CheckConstraint("format IN ('csv')", name="payroll_exports_format_check"),
        Index("ix_payroll_exports_company_created", "company_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    # NULL only for the seed/dev company with no ownership group, which
    # get_plan_state treats as unlimited and which therefore passes
    # assert_paid_plan.
    ownership_group_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=True
    )
    exported_by_user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL = every location in range.
    location_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=True
    )
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_minutes_total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
```

- [ ] **Step 5: Register both models in `backend/models/__init__.py`**

Add after the `GdprExportLog` import line:

```python
from backend.models.payroll_export import PayrollExport
from backend.models.time_entry import TimeEntry
```

Add to `__all__` after `"GdprExportLog",`:

```python
    "PayrollExport",
    "TimeEntry",
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entry_model.py -q`
Expected: PASS — 5 passed

- [ ] **Step 7: Write the migration**

Create `backend/alembic/versions/0036_add_time_entries_and_payroll_exports.py`:

```python
"""add time_entries and payroll_exports

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-23 00:00:00.000000

Payroll slice 1 (#78). payroll_exports is created first because
time_entries.payroll_export_id references it.

No backfill: every entry is derived on demand from approved shifts and
existing check-ins, so history becomes available the moment a manager picks
a past range — bounded by RETENTION_CHECKINS_DAYS.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payroll_exports",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("ownership_group_id", sa.String(length=8), nullable=True),
        sa.Column("exported_by_user_id", sa.String(length=8), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=True),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("paid_minutes_total", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["ownership_group_id"], ["ownership_groups.id"]),
        sa.ForeignKeyConstraint(["exported_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("format IN ('csv')",
                           name="payroll_exports_format_check"),
    )
    op.create_index("ix_payroll_exports_company_id", "payroll_exports",
                    ["company_id"])
    op.create_index("ix_payroll_exports_company_created", "payroll_exports",
                    ["company_id", "created_at"])

    op.create_table(
        "time_entries",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("shift_id", sa.String(length=8), nullable=False),
        sa.Column("role_id", sa.String(length=8), nullable=False),
        sa.Column("role_name", sa.String(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_minutes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("check_in_id", sa.String(length=8), nullable=True),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lateness_minutes", sa.Integer(), nullable=True),
        sa.Column("attested_by_user_id", sa.String(length=8), nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attestation_reason", sa.String(length=500), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=8), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payroll_export_id", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["check_in_id"], ["employee_check_ins.id"]),
        sa.ForeignKeyConstraint(["attested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["payroll_export_id"], ["payroll_exports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shift_id", name="uq_time_entries_shift"),
        sa.CheckConstraint("source IN ('checked_in', 'manager_attested')",
                           name="time_entries_source_check"),
        sa.CheckConstraint(
            "source <> 'manager_attested' OR (attested_by_user_id IS NOT NULL "
            "AND attested_at IS NOT NULL)",
            name="time_entries_attested_check",
        ),
        sa.CheckConstraint("paid_minutes > 0",
                           name="time_entries_paid_minutes_check"),
    )
    op.create_index("ix_time_entries_company_id", "time_entries", ["company_id"])
    op.create_index("ix_time_entries_company_pay_date", "time_entries",
                    ["company_id", "pay_date"])
    op.create_index("ix_time_entries_company_location_pay_date", "time_entries",
                    ["company_id", "location_id", "pay_date"])
    op.create_index("ix_time_entries_company_employee_pay_date", "time_entries",
                    ["company_id", "employee_id", "pay_date"])


def downgrade() -> None:
    op.drop_index("ix_time_entries_company_employee_pay_date",
                  table_name="time_entries")
    op.drop_index("ix_time_entries_company_location_pay_date",
                  table_name="time_entries")
    op.drop_index("ix_time_entries_company_pay_date", table_name="time_entries")
    op.drop_index("ix_time_entries_company_id", table_name="time_entries")
    op.drop_table("time_entries")
    op.drop_index("ix_payroll_exports_company_created",
                  table_name="payroll_exports")
    op.drop_index("ix_payroll_exports_company_id", table_name="payroll_exports")
    op.drop_table("payroll_exports")
```

- [ ] **Step 8: Verify the migration chain is linear**

Run: `../../../backend/.venv/bin/python -c "import re,pathlib;d={};[d.setdefault(re.search(chr(39)+'?revision: str = \"(\d+)\"',p.read_text()).group(1), re.search('down_revision: Union\[str, None\] = \"(\d+)\"', p.read_text()).group(1)) for p in pathlib.Path('backend/alembic/versions').glob('0*.py')];print(sorted(d.items())[-3:])"`
Expected: prints `[('0034', '0033'), ('0035', '0034'), ('0036', '0035')]`

- [ ] **Step 9: Run the whole backend suite to confirm nothing regressed**

Run: `../../../backend/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — the pre-existing count plus 5

- [ ] **Step 10: Commit**

```bash
git add backend/models/time_entry.py backend/models/payroll_export.py \
        backend/models/__init__.py \
        backend/alembic/versions/0036_add_time_entries_and_payroll_exports.py \
        tests/test_time_entry_model.py
git commit -m "feat(payroll): time_entries and payroll_exports models + migration 0036 (#78)"
```

---

### Task 2: The two pure rules — paid minutes and pay date

**Files:**
- Create: `backend/services/time_entries.py`
- Test: `tests/test_time_entries_service.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `backend.services.time_entries.paid_minutes(start: datetime, end: datetime) -> int` — raises `ValueError` on equal wall-clock faces
  - `backend.services.time_entries.pay_date_for(timezone_name: str, start: datetime) -> date`

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_entries_service.py`:

```python
"""The two rules payroll turns on, tested without a database.

These call the functions DIRECTLY on datetimes built with ZoneInfo tzinfo.
They never go through a SQLite round-trip, which strips tzinfo — so they test
the rule rather than the driver.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.services.time_entries import paid_minutes, pay_date_for

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
TOKYO = ZoneInfo("Asia/Tokyo")


# --- paid_minutes: a wall-clock contract, not an elapsed-instant measure ---

def test_an_ordinary_day_is_eight_hours():
    start = datetime(2026, 9, 15, 9, 0, tzinfo=NY)
    end = datetime(2026, 9, 15, 17, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_a_midnight_crossing_written_with_the_same_date():
    """The generator may write the end date as the start date; the faces are
    what count."""
    start = datetime(2026, 9, 15, 22, 0, tzinfo=NY)
    end = datetime(2026, 9, 15, 6, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_a_midnight_crossing_written_with_the_next_date():
    start = datetime(2026, 9, 15, 22, 0, tzinfo=NY)
    end = datetime(2026, 9, 16, 6, 0, tzinfo=NY)
    assert paid_minutes(start, end) == 480


def test_spring_forward_still_pays_eight_hours():
    """Clocks go forward at 02:00 on 2026-03-08 in America/New_York, so the
    elapsed instant is seven hours. Scheduled hours are a wall-clock contract
    between an employer and an employee, not an elapsed-instant measurement.
    This assertion is what makes a future 'fix' to instant arithmetic fail
    loudly instead of quietly under-paying a night shift once a year."""
    start = datetime(2026, 3, 7, 22, 0, tzinfo=NY)
    end = datetime(2026, 3, 8, 6, 0, tzinfo=NY)
    assert (end - start).total_seconds() / 3600 == 7  # the instant truth
    assert paid_minutes(start, end) == 480            # the contract we pay


def test_equal_faces_raise_rather_than_paying_a_full_day():
    """_shift_duration_hours returns 24 here. Silently paying someone for a
    full day because two timestamps matched is the worst available failure."""
    start = datetime(2026, 9, 15, 9, 0, tzinfo=NY)
    with pytest.raises(ValueError):
        paid_minutes(start, start)


# --- pay_date_for: the midnight-crossing rule --------------------------------

def test_a_late_start_west_of_utc_pays_on_the_local_start_date():
    """The UTC instant is 2026-01-05T03:00Z. A naive .date() on a UTC value
    returns the 5th — this is the test that fails if anyone drops the
    astimezone."""
    start = datetime(2026, 1, 4, 22, 0, tzinfo=NY)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 1, 5)
    assert pay_date_for("America/New_York", start) == date(2026, 1, 4)


def test_a_second_timezone_west_of_utc():
    start = datetime(2026, 3, 1, 23, 0, tzinfo=LA)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 3, 2)
    assert pay_date_for("America/Los_Angeles", start) == date(2026, 3, 1)


def test_east_of_utc_too():
    """Tokyo rolls over nine hours before UTC: an 08:00 local start on the
    11th is still the 10th in UTC."""
    start = datetime(2026, 5, 11, 8, 0, tzinfo=TOKYO)
    assert start.astimezone(ZoneInfo("UTC")).date() == date(2026, 5, 10)
    assert pay_date_for("Asia/Tokyo", start) == date(2026, 5, 11)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entries_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.time_entries'`

- [ ] **Step 3: Create `backend/services/time_entries.py` with the two pure rules**

```python
"""Deriving payable hours from approved shifts and the arrivals that gate them.

The pure functions come first and take primitives rather than ORM rows, so
their tests never go through a SQLite round-trip (which strips tzinfo) and
therefore test the rule rather than the driver.
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def paid_minutes(start: datetime, end: datetime) -> int:
    """Scheduled length in minutes, read off the WALL-CLOCK FACES.

    tzinfo is dropped deliberately and `end <= start` is treated as crossing
    midnight by adding 24 hours — the same convention as
    _shift_duration_hours in backend/scheduling/local_scheduler.py. A
    22:00 -> 06:00 shift is 480 minutes whether or not the generator wrote the
    end date as the next day, and whether or not a DST transition falls inside
    it. Scheduled hours are a wall-clock contract between an employer and an
    employee, not an elapsed-instant measurement, and a payroll file reporting
    7 hours for a "22:00-06:00" shift because the clocks went forward is the
    kind of surprise that generates a support ticket per location per year.

    Differs from _shift_duration_hours in one place: EQUAL FACES RAISE. No
    shift template produces a 24-hour shift, the schedule update handlers
    already reject start_time == end_time with a 422, and silently paying
    someone for a full day because two timestamps matched is the worst
    available failure mode. Callers treat this as a malformed shift.
    """
    start_face = start.hour * 60 + start.minute
    end_face = end.hour * 60 + end.minute
    if end_face == start_face:
        raise ValueError(
            f"shift start and end share a wall-clock face ({start_face // 60:02d}:"
            f"{start_face % 60:02d}); refusing to pay it as 24 hours"
        )
    if end_face < start_face:
        end_face += 24 * 60
    return end_face - start_face


def pay_date_for(timezone_name: str, start: datetime) -> date:
    """The location-local calendar date the shift STARTED.

    The whole shift's hours land here; no shift is ever split across two pay
    dates. Derived from the location's timezone, never from Shift.date (which
    is LLM-supplied and can drift) and never from the UTC instant.

    A 22:00 Sunday -> 06:00 Monday shift is paid entirely on Sunday. This
    agrees with EmployeeCheckIn.local_date (the local date of the scan, which
    happens at the start of the shift) and with the one day a human would name
    if you asked them when that shift was.
    """
    return start.astimezone(ZoneInfo(timezone_name)).date()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entries_service.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Confirm the UTC-today sweep still passes**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_utc_today.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/time_entries.py tests/test_time_entries_service.py
git commit -m "feat(payroll): paid_minutes and pay_date_for, the two pure rules (#78)"
```

---

### Task 3: Derivation, listing and the exception queue

**Files:**
- Modify: `backend/services/time_entries.py` (append below `pay_date_for`)
- Test: `tests/test_time_entries_derive.py`

**Interfaces:**
- Consumes: `paid_minutes(start: datetime, end: datetime) -> int`, `pay_date_for(timezone_name: str, start: datetime) -> date` (Task 2); `TimeEntry`, `TIME_ENTRY_CHECKED_IN` (Task 1).
- Produces (all in `backend.services.time_entries`):
  - `@dataclass(frozen=True) DeriveResult(created: int, existing: int, exception_count: int)`
  - `@dataclass(frozen=True) TimeEntryRow(id: str, shift_id: str, employee_id: str, employee_name: str, location_id: str, location_name: str, role_id: str, role_name: str, pay_date: date, start_time: datetime, end_time: datetime, paid_minutes: int, source: str, checked_in_at: datetime | None, lateness_minutes: int | None, attested_by_name: str | None, attested_at: datetime | None, attestation_reason: str | None, approved_at: datetime | None, exported_at: datetime | None)`
  - `@dataclass(frozen=True) ExceptionRow(shift_id: str, employee_id: str, employee_name: str, location_id: str, location_name: str, role_id: str, role_name: str, pay_date: date, start_time: datetime, end_time: datetime, paid_minutes: int)`
  - `async derive_time_entries(db: AsyncSession, company_id: str, range_start: date, range_end: date, location_id: str | None = None) -> DeriveResult`
  - `async list_time_entries(db: AsyncSession, company_id: str, range_start: date, range_end: date, location_id: str | None = None, approved: bool | None = None) -> list[TimeEntryRow]`
  - `async list_exceptions(db: AsyncSession, company_id: str, range_start: date, range_end: date, location_id: str | None = None) -> list[ExceptionRow]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_entries_derive.py`:

```python
"""Derivation: approved shift + the arrival that gates it -> one payable row.

Shift fixtures are built with tzinfo=timezone.utc directly, exactly as
tests/test_check_in_service.py does, and never from an offset-bearing ISO
string — SQLite strips tzinfo on round-trip and would silently shift them.
Assertions here are about counts, statuses and constraints; the timezone rule
itself is asserted directly in tests/test_time_entries_service.py.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Role, Shift,
    ShiftSchedule, TimeEntry, User,
)
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE, CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.models.time_entry import TIME_ENTRY_CHECKED_IN
from backend.services.time_entries import (
    derive_time_entries, list_exceptions, list_time_entries,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

# A fixed reference point in the past so nothing here depends on the hour the
# suite happens to run. "Today" is UTC, per CLAUDE.md.
TODAY = datetime.now(timezone.utc).date()


async def _tenant(db: AsyncSession, tz: str = "America/New_York") -> SimpleNamespace:
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone=tz))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id,
                    full_name="Dana Okafor", email=f"{employee_id}@example.com",
                    location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, role_id=role_id,
        role_name=role.name, employee_id=employee_id, user_id=user_id,
    )


async def _shift(
    db: AsyncSession, t: SimpleNamespace, start: datetime,
    hours: int = 8, status: str = "approved",
) -> str:
    schedule_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=schedule_id, company_id=t.company_id,
                         location_id=t.location_id,
                         week_start_date=start.date(), status=status))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=t.company_id,
                 shift_schedule_id=schedule_id, location_id=t.location_id,
                 employee_id=t.employee_id, role_id=t.role_id,
                 role_name=t.role_name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=hours)))
    await db.commit()
    return shift_id


async def _check_in(
    db: AsyncSession, t: SimpleNamespace, shift_id: str | None,
    at: datetime, status: str = CHECK_IN_MATCHED, minutes: int | None = -4,
    counter: int = 0,
) -> str:
    row_id = _id()
    db.add(EmployeeCheckIn(
        id=row_id, company_id=t.company_id, location_id=t.location_id,
        employee_id=t.employee_id, shift_id=shift_id, checked_in_at=at,
        local_date=at.date(), counter=counter, status=status,
        minutes_from_start=minutes,
    ))
    await db.commit()
    return row_id


def _yesterday_at(hour: int) -> datetime:
    """A UTC instant yesterday. Yesterday, not today, so the shift has always
    started by the time the test runs."""
    return datetime.combine(
        TODAY - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ).replace(hour=hour)


async def test_a_matched_check_in_produces_one_entry(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    check_in_id = await _check_in(db_session, t, shift_id,
                                  start - timedelta(minutes=4))

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 1
    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.source == TIME_ENTRY_CHECKED_IN
    assert entry.shift_id == shift_id
    assert entry.check_in_id == check_in_id
    assert entry.lateness_minutes == -4
    assert entry.paid_minutes == 480
    assert entry.role_name == t.role_name
    assert entry.approved_at is None


async def test_a_duplicate_check_in_is_still_evidence_of_arrival(
    db_session: AsyncSession
):
    """`duplicate` answers a punctuality question, not an attendance one. On a
    split-shift day the second shift's genuine arrival is recorded as
    duplicate; refusing it would push a real shift into the exception queue."""
    t = await _tenant(db_session)
    start = _yesterday_at(17)
    shift_id = await _shift(db_session, t, start, hours=4)
    await _check_in(db_session, t, shift_id, start, status=CHECK_IN_DUPLICATE,
                    minutes=0)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 1


async def test_the_earliest_scan_owns_the_lateness_figure(
    db_session: AsyncSession
):
    """A matched row always wins over a later duplicate for the same shift, so
    the 17:00 re-scan record_check_in keeps out of the punctuality figure never
    becomes the lateness figure here either."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start - timedelta(minutes=4),
                    status=CHECK_IN_MATCHED, minutes=-4, counter=0)
    await _check_in(db_session, t, shift_id, start + timedelta(hours=8),
                    status=CHECK_IN_DUPLICATE, minutes=480, counter=1)

    await derive_time_entries(db_session, t.company_id,
                              TODAY - timedelta(days=7), TODAY)

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.lateness_minutes == -4


@pytest.mark.parametrize("status", [CHECK_IN_NO_SHIFT, CHECK_IN_WRONG_LOCATION])
async def test_an_unmatched_check_in_gates_nothing(
    db_session: AsyncSession, status: str
):
    """_match_shift leaves shift_id NULL on those rows, so the join cannot see
    them and the shift is an exception instead."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    await _shift(db_session, t, start)
    await _check_in(db_session, t, None, start, status=status, minutes=None)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0
    assert result.exception_count == 1


async def test_a_draft_schedule_pays_nothing(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start, status="draft")
    await _check_in(db_session, t, shift_id, start)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0
    assert result.existing == 0
    assert result.exception_count == 0


async def test_a_future_shift_is_neither_an_entry_nor_an_exception(
    db_session: AsyncSession
):
    t = await _tenant(db_session)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    await _shift(db_session, t, start)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY, TODAY + timedelta(days=7)
    )

    assert result.created == 0
    assert result.exception_count == 0
    assert await list_exceptions(
        db_session, t.company_id, TODAY, TODAY + timedelta(days=7)
    ) == []


async def test_deriving_twice_creates_nothing_the_second_time(
    db_session: AsyncSession
):
    """The page calls derive on every load; it has to be free the second
    time."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start)
    window = (TODAY - timedelta(days=7), TODAY)

    first = await derive_time_entries(db_session, t.company_id, *window)
    second = await derive_time_entries(db_session, t.company_id, *window)

    assert first.created == 1 and first.existing == 0
    assert second.created == 0 and second.existing == 1
    assert len((await db_session.execute(select(TimeEntry))).scalars().all()) == 1


async def test_another_companys_shift_is_invisible(db_session: AsyncSession):
    mine = await _tenant(db_session)
    theirs = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, theirs, start)
    await _check_in(db_session, theirs, shift_id, start)

    result = await derive_time_entries(
        db_session, mine.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0


async def test_a_shift_with_no_check_in_lands_in_the_exception_queue(
    db_session: AsyncSession
):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)

    rows = await list_exceptions(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert [r.shift_id for r in rows] == [shift_id]
    assert rows[0].employee_name == "Dana Okafor"
    assert rows[0].paid_minutes == 480
    assert rows[0].role_name == t.role_name


async def test_a_midnight_crossing_shift_pays_on_its_start_date(
    db_session: AsyncSession
):
    """22:00 local Sunday -> 06:00 Monday is paid ENTIRELY on Sunday: included
    in full by a range ending Sunday, excluded entirely by one starting
    Monday. The location is America/New_York, so 22:00 local is 02:00 UTC the
    NEXT day — which is exactly the case a naive .date() gets wrong."""
    t = await _tenant(db_session)
    # 02:00 UTC on `local_start_date + 1` == 22:00 the previous evening in NY
    # during EST. Pick a fixed winter date in the past so the offset is -05:00
    # regardless of when the suite runs.
    local_start = date(2026, 1, 4)          # a Sunday
    start = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)   # 22:00 EST Jan 4
    shift_id = await _shift(db_session, t, start, hours=8)
    await _check_in(db_session, t, shift_id, start)

    included = await derive_time_entries(
        db_session, t.company_id, local_start, local_start
    )
    assert included.created == 1

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.pay_date == local_start

    rows = await list_time_entries(
        db_session, t.company_id, local_start + timedelta(days=1),
        local_start + timedelta(days=7),
    )
    assert rows == []


async def test_listing_filters_by_approval_state(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start)
    window = (TODAY - timedelta(days=7), TODAY)
    await derive_time_entries(db_session, t.company_id, *window)

    assert len(await list_time_entries(
        db_session, t.company_id, *window, approved=False)) == 1
    assert await list_time_entries(
        db_session, t.company_id, *window, approved=True) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entries_derive.py -q`
Expected: FAIL — `ImportError: cannot import name 'derive_time_entries' from 'backend.services.time_entries'`

- [ ] **Step 3: Extend the module imports**

Replace the import block at the top of `backend/services/time_entries.py` with:

```python
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models import (
    Employee,
    EmployeeCheckIn,
    Location,
    Shift,
    ShiftSchedule,
    TimeEntry,
    User,
)
from backend.models.employee_check_in import CHECK_IN_DUPLICATE, CHECK_IN_MATCHED
from backend.models.time_entry import TIME_ENTRY_CHECKED_IN

logger = logging.getLogger(__name__)

# Locations sit at most +/-14h from UTC, so a range widened by a day either
# side is guaranteed to contain every shift whose LOCAL pay_date falls inside
# it. The exact membership test is done in Python, where the timezone is known.
_RANGE_SLACK = timedelta(days=1)
```

- [ ] **Step 4: Append the result dataclasses and the shared candidate loader**

Append to `backend/services/time_entries.py`:

```python
@dataclass(frozen=True)
class DeriveResult:
    created: int
    existing: int
    exception_count: int


@dataclass(frozen=True)
class TimeEntryRow:
    id: str
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None
    attested_by_name: str | None
    attested_at: datetime | None
    attestation_reason: str | None
    approved_at: datetime | None
    exported_at: datetime | None


@dataclass(frozen=True)
class ExceptionRow:
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int


@dataclass(frozen=True)
class _Candidate:
    shift: Shift
    location: Location
    employee_name: str
    pay_date: date
    paid_minutes: int


def _as_utc(dt: datetime) -> datetime:
    """Normalize a (possibly naive) datetime from SQLite to UTC-aware.

    DateTime(timezone=True) is honored by Postgres but ignored by SQLite,
    which strips tzinfo on round-trip. We always store UTC, so re-attaching it
    when missing is correct. Same pattern as _as_utc in
    backend/services/check_in.py — and the same warning applies: never build a
    shift fixture from an offset-bearing ISO string under SQLite.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _candidate_shifts(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None,
) -> list[_Candidate]:
    """Approved, already-started shifts whose derived pay_date is in range.

    Filtered on start_time rather than Shift.date: Shift.date is LLM-supplied
    and can drift, while start_time is what both the check-in matcher and the
    pay date are derived from.

    A shift whose wall-clock faces are equal is skipped with a warning rather
    than raising: it is malformed, and the paid_minutes > 0 check constraint
    must never be the thing a manager discovers.
    """
    now = datetime.now(timezone.utc)
    window_start = datetime.combine(
        range_start, time.min, tzinfo=timezone.utc
    ) - _RANGE_SLACK
    window_end = datetime.combine(
        range_end, time.max, tzinfo=timezone.utc
    ) + _RANGE_SLACK

    query = (
        select(Shift, Location, Employee.full_name)
        .join(ShiftSchedule, ShiftSchedule.id == Shift.shift_schedule_id)
        .join(Location, Location.id == Shift.location_id)
        .join(Employee, Employee.id == Shift.employee_id)
        .where(
            Shift.company_id == company_id,
            ShiftSchedule.status == "approved",
            Shift.start_time >= window_start,
            Shift.start_time <= window_end,
        )
    )
    if location_id:
        query = query.where(Shift.location_id == location_id)

    candidates: list[_Candidate] = []
    for shift, location, employee_name in (await db.execute(query)).all():
        start = _as_utc(shift.start_time)
        if start >= now:
            # Not worked yet: neither an entry nor an exception.
            continue
        pay_date = pay_date_for(location.timezone, start)
        if not (range_start <= pay_date <= range_end):
            continue
        try:
            minutes = paid_minutes(shift.start_time, shift.end_time)
        except ValueError:
            logger.warning(
                "payroll.malformed_shift shift_id=%s company_id=%s start=%s end=%s",
                shift.id, company_id, shift.start_time, shift.end_time,
            )
            continue
        candidates.append(_Candidate(
            shift=shift, location=location, employee_name=employee_name,
            pay_date=pay_date, paid_minutes=minutes,
        ))
    return candidates


async def _earliest_check_ins(
    db: AsyncSession, shift_ids: list[str]
) -> dict[str, EmployeeCheckIn]:
    """The gating scan per shift: the EARLIEST matched-or-duplicate arrival.

    We do not re-derive the match. _match_shift already ran at scan time and
    persisted its answer on EmployeeCheckIn.shift_id; re-deriving would be a
    second implementation that can disagree with the first, and would silently
    produce a different answer for any shift edited after the scan.
    """
    rows = (await db.execute(
        select(EmployeeCheckIn)
        .where(
            EmployeeCheckIn.shift_id.in_(shift_ids),
            EmployeeCheckIn.status.in_([CHECK_IN_MATCHED, CHECK_IN_DUPLICATE]),
        )
        .order_by(EmployeeCheckIn.checked_in_at.asc())
    )).scalars().all()
    earliest: dict[str, EmployeeCheckIn] = {}
    for row in rows:
        if row.shift_id is not None:
            earliest.setdefault(row.shift_id, row)
    return earliest
```

- [ ] **Step 5: Append `derive_time_entries`, `list_time_entries` and `list_exceptions`**

```python
async def derive_time_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    _retry: bool = True,
) -> DeriveResult:
    """Create the TimeEntry rows for a range. Idempotent.

    Loads the candidate shifts and their locations once, maps each to its
    check-in, and inserts in one commit. An IntegrityError on
    uq_time_entries_shift means a concurrent derive got there first, so the
    call is retried once — which re-reads the now-present shift ids and
    therefore excludes them. A second failure returns what it managed, because
    a duplicate-key collision here means the row exists, which is the outcome
    the caller wanted.
    """
    candidates = await _candidate_shifts(
        db, company_id, range_start, range_end, location_id
    )
    if not candidates:
        return DeriveResult(created=0, existing=0, exception_count=0)

    shift_ids = [c.shift.id for c in candidates]
    existing_ids = set((await db.execute(
        select(TimeEntry.shift_id).where(TimeEntry.shift_id.in_(shift_ids))
    )).scalars().all())
    check_ins = await _earliest_check_ins(db, shift_ids)

    created = 0
    existing = 0
    exception_count = 0
    for c in candidates:
        if c.shift.id in existing_ids:
            existing += 1
            continue
        check_in = check_ins.get(c.shift.id)
        if check_in is None:
            exception_count += 1
            continue
        db.add(TimeEntry(
            company_id=company_id,
            location_id=c.shift.location_id,
            employee_id=c.shift.employee_id,
            shift_id=c.shift.id,
            role_id=c.shift.role_id,
            role_name=c.shift.role_name,
            pay_date=c.pay_date,
            start_time=c.shift.start_time,
            end_time=c.shift.end_time,
            paid_minutes=c.paid_minutes,
            source=TIME_ENTRY_CHECKED_IN,
            check_in_id=check_in.id,
            checked_in_at=check_in.checked_in_at,
            lateness_minutes=check_in.minutes_from_start,
        ))
        created += 1

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if _retry:
            return await derive_time_entries(
                db, company_id, range_start, range_end, location_id,
                _retry=False,
            )
        logger.warning(
            "payroll.derive_conflict company_id=%s range=%s..%s",
            company_id, range_start, range_end,
        )
        return DeriveResult(
            created=0, existing=existing + created,
            exception_count=exception_count,
        )

    return DeriveResult(
        created=created, existing=existing, exception_count=exception_count
    )


async def list_time_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    approved: bool | None = None,
) -> list[TimeEntryRow]:
    """Read-only. Writes nothing, derives nothing."""
    attester = aliased(User)
    query = (
        select(TimeEntry, Employee.full_name, Location.name, attester.full_name)
        .join(Employee, Employee.id == TimeEntry.employee_id)
        .join(Location, Location.id == TimeEntry.location_id)
        .outerjoin(attester, attester.id == TimeEntry.attested_by_user_id)
        .where(
            TimeEntry.company_id == company_id,
            TimeEntry.pay_date >= range_start,
            TimeEntry.pay_date <= range_end,
        )
        .order_by(TimeEntry.pay_date, Employee.full_name)
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if approved is True:
        query = query.where(TimeEntry.approved_at.isnot(None))
    elif approved is False:
        query = query.where(TimeEntry.approved_at.is_(None))

    return [
        TimeEntryRow(
            id=entry.id,
            shift_id=entry.shift_id,
            employee_id=entry.employee_id,
            employee_name=employee_name,
            location_id=entry.location_id,
            location_name=location_name,
            role_id=entry.role_id,
            role_name=entry.role_name,
            pay_date=entry.pay_date,
            start_time=entry.start_time,
            end_time=entry.end_time,
            paid_minutes=entry.paid_minutes,
            source=entry.source,
            checked_in_at=entry.checked_in_at,
            lateness_minutes=entry.lateness_minutes,
            attested_by_name=attested_by_name,
            attested_at=entry.attested_at,
            attestation_reason=entry.attestation_reason,
            approved_at=entry.approved_at,
            exported_at=entry.exported_at,
        )
        for entry, employee_name, location_name, attested_by_name
        in (await db.execute(query)).all()
    ]


async def list_exceptions(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
) -> list[ExceptionRow]:
    """Approved, started, in-range shifts with no check-in and no entry.

    Attesting a shift removes it from this list on the next load, because it
    then has an entry.
    """
    candidates = await _candidate_shifts(
        db, company_id, range_start, range_end, location_id
    )
    if not candidates:
        return []

    shift_ids = [c.shift.id for c in candidates]
    entried = set((await db.execute(
        select(TimeEntry.shift_id).where(TimeEntry.shift_id.in_(shift_ids))
    )).scalars().all())
    check_ins = await _earliest_check_ins(db, shift_ids)

    rows = [
        ExceptionRow(
            shift_id=c.shift.id,
            employee_id=c.shift.employee_id,
            employee_name=c.employee_name,
            location_id=c.shift.location_id,
            location_name=c.location.name,
            role_id=c.shift.role_id,
            role_name=c.shift.role_name,
            pay_date=c.pay_date,
            start_time=c.shift.start_time,
            end_time=c.shift.end_time,
            paid_minutes=c.paid_minutes,
        )
        for c in candidates
        if c.shift.id not in entried and c.shift.id not in check_ins
    ]
    rows.sort(key=lambda r: (r.pay_date, r.employee_name))
    return rows
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_time_entries_derive.py -q`
Expected: PASS — 12 passed

- [ ] **Step 7: Commit**

```bash
git add backend/services/time_entries.py tests/test_time_entries_derive.py
git commit -m "feat(payroll): derive time entries, list them, and the exception queue (#78)"
```

---

### Task 4: The payroll router — derive, entries, exceptions

**Files:**
- Create: `backend/schemas/payroll.py`
- Create: `backend/routers/payroll.py`
- Modify: `backend/main.py` (router import block, and `include_router` after `check_ins.router`)
- Test: `tests/test_payroll_api.py`

**Interfaces:**
- Consumes: `derive_time_entries`, `list_time_entries`, `list_exceptions`, `DeriveResult`, `TimeEntryRow`, `ExceptionRow` (Task 3); `require_manager`, `get_db` from `backend.dependencies`; `assert_paid_plan` from `backend.services.plan`.
- Produces (in `backend.schemas.payroll`):
  - `MAX_RANGE_DAYS: int = 62`
  - `PayrollRangeRequest(range_start: date, range_end: date, location_id: str | None = None)`
  - `PayrollDeriveResponse(created: int, existing: int, exception_count: int)`
  - `TimeEntryRowSchema` — one field per `TimeEntryRow` dataclass field, same names and types
  - `PayrollEntriesResponse(rows: list[TimeEntryRowSchema], total_entries: int, total_paid_minutes: int, approved_entries: int)`
  - `PayrollExceptionRowSchema` — one field per `ExceptionRow` field
  - `PayrollExceptionsResponse(rows: list[PayrollExceptionRowSchema], total: int)`
- Produces (in `backend.routers.payroll`):
  - `router: APIRouter` with `prefix="/payroll"`
  - `_validate_range(range_start: date, range_end: date) -> None` — raises `400 invalid_range`
  - `POST /payroll/entries/derive`, `GET /payroll/entries`, `GET /payroll/exceptions`

- [ ] **Step 1: Write the failing test**

Create `tests/test_payroll_api.py`:

```python
"""The payroll endpoints: gating, range validation, and the read paths.

Paid-plan and manager gating are asserted on EVERY endpoint, because what
gates the feature is the tenant's plan and the caller's role, not the shape of
the request.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Role, Shift,
    ShiftSchedule, User,
)
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio

TODAY = datetime.now(timezone.utc).date()
WEEK_AGO = TODAY - timedelta(days=7)


async def _tenant(db: AsyncSession, *, paid: bool) -> SimpleNamespace:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G",
                          stripe_subscription_id="sub_x" if paid else None))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}",
                   ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, role_id, employee_id = _id(), _id(), _id()
    manager_id, employee_user_id = _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone="America/New_York"))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(User(id=manager_id, company_id=company_id,
                email=f"{manager_id}@example.com", hashed_password="x",
                full_name="Mo Manager", user_role="manager"))
    db.add(User(id=employee_user_id, company_id=company_id,
                email=f"{employee_user_id}@example.com", hashed_password="x",
                full_name="E", user_role="employee"))
    db.add(Employee(id=employee_id, company_id=company_id,
                    full_name="Dana Okafor", email=f"{employee_id}@example.com",
                    location_ids=[location_id], user_id=employee_user_id))
    await db.commit()
    return SimpleNamespace(
        og_id=og_id, company_id=company_id, location_id=location_id,
        role_id=role_id, role_name=role.name, employee_id=employee_id,
        manager_id=manager_id,
        manager_headers={"Authorization":
                         f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
        employee_headers={"Authorization":
                          f"Bearer {_make_token(employee_user_id, company_id, 'employee')}"},
    )


async def _worked_shift(
    db: AsyncSession, t: SimpleNamespace, *, days_ago: int = 1,
    hour: int = 9, hours: int = 8, checked_in: bool = True,
    status: str = "approved",
) -> str:
    """An approved shift that has already started, optionally with its scan."""
    start = datetime.combine(
        TODAY - timedelta(days=days_ago), datetime.min.time(),
        tzinfo=timezone.utc,
    ).replace(hour=hour)
    schedule_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=schedule_id, company_id=t.company_id,
                         location_id=t.location_id,
                         week_start_date=start.date(), status=status))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=t.company_id,
                 shift_schedule_id=schedule_id, location_id=t.location_id,
                 employee_id=t.employee_id, role_id=t.role_id,
                 role_name=t.role_name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=hours)))
    if checked_in:
        db.add(EmployeeCheckIn(
            id=_id(), company_id=t.company_id, location_id=t.location_id,
            employee_id=t.employee_id, shift_id=shift_id,
            checked_in_at=start - timedelta(minutes=4), local_date=start.date(),
            counter=0, status=CHECK_IN_MATCHED, minutes_from_start=-4,
        ))
    await db.commit()
    return shift_id


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=False)


def _range_body(t: SimpleNamespace, **extra) -> dict:
    body = {"range_start": WEEK_AGO.isoformat(), "range_end": TODAY.isoformat(),
            "location_id": None}
    body.update(extra)
    return body


# --- plan gating ------------------------------------------------------------

async def test_every_endpoint_is_paid_only(client: AsyncClient, free: SimpleNamespace):
    calls = [
        client.post("/api/v1/payroll/entries/derive", json=_range_body(free),
                    headers=free.manager_headers),
        client.get(f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
                   headers=free.manager_headers),
        client.get(f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
                   headers=free.manager_headers),
        client.post("/api/v1/payroll/attest", json={"shift_id": _id()},
                    headers=free.manager_headers),
        client.post("/api/v1/payroll/approve", json=_range_body(free),
                    headers=free.manager_headers),
        client.post("/api/v1/payroll/export",
                    json=_range_body(free, include_exported=False),
                    headers=free.manager_headers),
    ]
    for call in calls:
        resp = await call
        assert resp.status_code == 402, resp.text
        assert resp.json()["detail"]["code"] == "payroll_requires_paid_plan"


async def test_every_endpoint_is_manager_only(
    client: AsyncClient, paid: SimpleNamespace
):
    calls = [
        client.post("/api/v1/payroll/entries/derive", json=_range_body(paid),
                    headers=paid.employee_headers),
        client.get(f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
                   headers=paid.employee_headers),
        client.get(f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
                   headers=paid.employee_headers),
        client.post("/api/v1/payroll/attest", json={"shift_id": _id()},
                    headers=paid.employee_headers),
        client.post("/api/v1/payroll/approve", json=_range_body(paid),
                    headers=paid.employee_headers),
        client.post("/api/v1/payroll/export",
                    json=_range_body(paid, include_exported=False),
                    headers=paid.employee_headers),
    ]
    for call in calls:
        resp = await call
        assert resp.status_code == 403, resp.text


# --- range validation -------------------------------------------------------

async def test_a_backwards_range_is_rejected(
    client: AsyncClient, paid: SimpleNamespace
):
    resp = await client.post(
        "/api/v1/payroll/entries/derive",
        json=_range_body(paid, range_start=TODAY.isoformat(),
                         range_end=WEEK_AGO.isoformat()),
        headers=paid.manager_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_range"


async def test_a_ninety_day_span_is_rejected(
    client: AsyncClient, paid: SimpleNamespace
):
    """62 days is two monthly pay periods — far beyond any real cadence, and
    it bounds every query on the page."""
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={TODAY - timedelta(days=90)}"
        f"&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_range"


async def test_a_sixty_two_day_span_is_accepted(
    client: AsyncClient, paid: SimpleNamespace
):
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={TODAY - timedelta(days=61)}"
        f"&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text


# --- the read paths ---------------------------------------------------------

async def test_derive_then_list(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    shift_id = await _worked_shift(db_session, paid)

    derived = await client.post("/api/v1/payroll/entries/derive",
                                json=_range_body(paid),
                                headers=paid.manager_headers)
    assert derived.status_code == 200, derived.text
    assert derived.json() == {"created": 1, "existing": 0, "exception_count": 0}

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    body = listed.json()
    assert body["total_entries"] == 1
    assert body["total_paid_minutes"] == 480
    assert body["approved_entries"] == 0
    row = body["rows"][0]
    assert row["shift_id"] == shift_id
    assert row["employee_name"] == "Dana Okafor"
    assert row["location_name"] == "Flatbush Ave"
    assert row["source"] == "checked_in"
    assert row["lateness_minutes"] == -4
    assert row["attested_by_name"] is None


async def test_the_exception_queue_lists_a_shift_with_no_scan(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    resp = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["shift_id"] == shift_id
    assert body["rows"][0]["paid_minutes"] == 480


async def test_another_companys_entries_are_invisible(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    other = await _tenant(db_session, paid=True)
    await _worked_shift(db_session, other)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(other),
                      headers=other.manager_headers)

    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.json()["rows"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q`
Expected: FAIL — every request returns 404 (`assert 404 == 402`), because `/api/v1/payroll/...` is not routed.

- [ ] **Step 3: Create `backend/schemas/payroll.py`**

```python
"""Request and response shapes for the payroll router.

Timestamps are serialised exactly as stored, offset intact. Nothing on this
path calls .astimezone() on a shift timestamp — the frontend reads the
wall-clock face off the string (utils/shiftTime.ts), per #92.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

# Two monthly pay periods. Bounds every query on the page and is far beyond
# any real payroll cadence.
MAX_RANGE_DAYS = 62


class PayrollRangeRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None


class PayrollDeriveResponse(BaseModel):
    created: int
    existing: int
    exception_count: int


class TimeEntryRowSchema(BaseModel):
    id: str
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None
    attested_by_name: str | None
    attested_at: datetime | None
    attestation_reason: str | None
    approved_at: datetime | None
    exported_at: datetime | None


class PayrollEntriesResponse(BaseModel):
    rows: list[TimeEntryRowSchema]
    total_entries: int
    total_paid_minutes: int
    approved_entries: int


class PayrollExceptionRowSchema(BaseModel):
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int


class PayrollExceptionsResponse(BaseModel):
    rows: list[PayrollExceptionRowSchema]
    total: int


class PayrollAttestRequest(BaseModel):
    shift_id: str
    reason: str | None = Field(default=None, max_length=500)


class PayrollApproveRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None
    entry_ids: list[str] | None = None


class PayrollApproveResponse(BaseModel):
    approved: int
    already_approved: int


class PayrollExportRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None
    include_exported: bool = False
```

- [ ] **Step 4: Create `backend/routers/payroll.py` with the three read/derive endpoints**

```python
"""Payable hours: derivation, the exception queue, attestation, approval, CSV.

Paid-only in every direction via assert_paid_plan, manager-only via
require_manager, and every query filtered by the caller's company_id.

The router is a thin translation layer: the decision table lives in
backend/services/time_entries.py and backend/services/payroll_export.py.
"""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import User
from backend.schemas.payroll import (
    MAX_RANGE_DAYS,
    PayrollDeriveResponse,
    PayrollEntriesResponse,
    PayrollExceptionRowSchema,
    PayrollExceptionsResponse,
    PayrollRangeRequest,
    TimeEntryRowSchema,
)
from backend.services.plan import assert_paid_plan
from backend.services.time_entries import (
    derive_time_entries,
    list_exceptions,
    list_time_entries,
)

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _validate_range(range_start: date, range_end: date) -> None:
    """Inclusive, forwards, and at most MAX_RANGE_DAYS long.

    Checked in the router rather than in a pydantic validator so the GET query
    parameters and the POST bodies are held to exactly the same rule, and so
    the refusal is a 400 with a code rather than a 422 with a pydantic blob.
    """
    if (
        range_end < range_start
        or (range_end - range_start).days + 1 > MAX_RANGE_DAYS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_range",
                "message": (
                    f"Pick a range that runs forwards and covers at most "
                    f"{MAX_RANGE_DAYS} days."
                ),
            },
        )


@router.post("/entries/derive", response_model=PayrollDeriveResponse)
async def derive_entries(
    body: PayrollRangeRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollDeriveResponse:
    """Create the payable rows for a range. Idempotent — the page calls it on
    every load. A POST rather than a GET-with-side-effects because it writes.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    result = await derive_time_entries(
        db, company_id, body.range_start, body.range_end, body.location_id
    )
    return PayrollDeriveResponse(**asdict(result))


@router.get("/entries", response_model=PayrollEntriesResponse)
async def get_entries(
    range_start: date = Query(...),
    range_end: date = Query(...),
    location_id: str | None = Query(None),
    approved: bool | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollEntriesResponse:
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(range_start, range_end)

    rows = await list_time_entries(
        db, company_id, range_start, range_end, location_id, approved
    )
    return PayrollEntriesResponse(
        rows=[TimeEntryRowSchema(**asdict(r)) for r in rows],
        total_entries=len(rows),
        total_paid_minutes=sum(r.paid_minutes for r in rows),
        approved_entries=sum(1 for r in rows if r.approved_at is not None),
    )


@router.get("/exceptions", response_model=PayrollExceptionsResponse)
async def get_exceptions(
    range_start: date = Query(...),
    range_end: date = Query(...),
    location_id: str | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollExceptionsResponse:
    """Approved shifts nobody scanned for. Phones die; nobody misses a
    paycheque over a QR scan."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(range_start, range_end)

    rows = await list_exceptions(
        db, company_id, range_start, range_end, location_id
    )
    return PayrollExceptionsResponse(
        rows=[PayrollExceptionRowSchema(**asdict(r)) for r in rows],
        total=len(rows),
    )
```

- [ ] **Step 5: Register the router in `backend/main.py`**

In the `from backend.routers import (...)` block, add `payroll,` in alphabetical position (between `ownership_group,` and `regions,`).

Immediately after the `app.include_router(check_ins.router, prefix=api_prefix)` line, add:

```python
    app.include_router(payroll.router, prefix=api_prefix)
```

- [ ] **Step 6: Run the test — the gating and read tests now pass, attest/approve/export still 404**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q -k "rejected or accepted or derive_then_list or exception_queue or another_companys"`
Expected: PASS — 6 passed

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q -k "paid_only or manager_only"`
Expected: FAIL — 2 failed; the `attest`, `approve` and `export` entries in each list still 404. Those pass in Tasks 5, 6 and 7.

- [ ] **Step 7: Commit**

```bash
git add backend/schemas/payroll.py backend/routers/payroll.py backend/main.py \
        tests/test_payroll_api.py
git commit -m "feat(payroll): /payroll router with derive, entries and exceptions (#78)"
```

---

### Task 5: Manager attestation

**Files:**
- Modify: `backend/services/time_entries.py` (append `attest_shift`)
- Modify: `backend/routers/payroll.py` (add `POST /payroll/attest`)
- Test: `tests/test_payroll_attest.py`

**Interfaces:**
- Consumes: `paid_minutes`, `pay_date_for`, `_as_utc` (Tasks 2–3); `TimeEntry`, `TIME_ENTRY_MANAGER_ATTESTED` (Task 1); `list_time_entries` for the response row; `PayrollAttestRequest`, `TimeEntryRowSchema` (Task 4).
- Produces:
  - `async attest_shift(db: AsyncSession, company_id: str, shift_id: str, user: User, reason: str | None) -> TimeEntry` — raises `HTTPException` with `detail={"code": ..., "message": ...}` for `shift_not_found` (404), `shift_not_approved` (409), `shift_not_started` (409), `entry_exists` (409), `malformed_shift` (422)
  - `POST /payroll/attest` → `201` with a `TimeEntryRowSchema` body

**Spec note:** the spec lists four attest errors and does not say what happens when a shift's wall-clock faces are equal (`paid_minutes` raises). Resolved as `422 malformed_shift`, matching the existing 422 the schedule update handlers already return for `start_time == end_time`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_payroll_attest.py`:

```python
"""Attestation: a pay-affecting action one person takes on another's behalf.

Every path here leaves an audit trail — source, attester, timestamp, reason —
because that is what makes it safe to pay someone who never scanned.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import TimeEntry
from tests.conftest import _id
from tests.test_payroll_api import TODAY, WEEK_AGO, _tenant, _worked_shift

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_attesting_creates_an_audited_entry(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    resp = await client.post(
        "/api/v1/payroll/attest",
        json={"shift_id": shift_id, "reason": "Phone battery died"},
        headers=paid.manager_headers,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "manager_attested"
    assert body["attested_by_name"] == "Mo Manager"
    assert body["attestation_reason"] == "Phone battery died"
    assert body["attested_at"] is not None
    assert body["paid_minutes"] == 480

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.attested_by_user_id == paid.manager_id


async def test_an_attested_entry_reports_no_scan_and_no_lateness(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """There is no scan to report, and inventing a lateness of zero would put
    a fact in the export that nobody observed."""
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    body = (await client.post("/api/v1/payroll/attest",
                              json={"shift_id": shift_id},
                              headers=paid.manager_headers)).json()

    assert body["checked_in_at"] is None
    assert body["lateness_minutes"] is None


async def test_attesting_removes_the_shift_from_the_exception_queue(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    await client.post("/api/v1/payroll/attest", json={"shift_id": shift_id},
                      headers=paid.manager_headers)

    resp = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.json()["rows"] == []


async def test_a_shift_that_has_not_started_cannot_be_pre_attested(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, days_ago=-3,
                                   checked_in=False)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_not_started"


async def test_a_shift_that_already_has_an_entry_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=True)
    await client.post("/api/v1/payroll/entries/derive",
                      json={"range_start": WEEK_AGO.isoformat(),
                            "range_end": TODAY.isoformat(),
                            "location_id": None},
                      headers=paid.manager_headers)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "entry_exists"


async def test_attesting_twice_is_refused_the_second_time(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    body = {"shift_id": shift_id}
    first = await client.post("/api/v1/payroll/attest", json=body,
                              headers=paid.manager_headers)
    second = await client.post("/api/v1/payroll/attest", json=body,
                               headers=paid.manager_headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "entry_exists"


async def test_a_draft_schedule_shift_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False,
                                   status="draft")

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_not_approved"


async def test_another_companys_shift_is_not_found(
    client: AsyncClient, db_session: AsyncSession, paid
):
    other = await _tenant(db_session, paid=True)
    shift_id = await _worked_shift(db_session, other, checked_in=False)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "shift_not_found"


async def test_a_shift_that_does_not_exist_is_not_found(
    client: AsyncClient, paid
):
    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": _id()},
                             headers=paid.manager_headers)

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "shift_not_found"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_attest.py -q`
Expected: FAIL — 9 failed, every request returning 404 with a plain-string `detail` (`TypeError: string indices must be integers` / `assert 404 == 201`), because `/payroll/attest` is not routed.

- [ ] **Step 3: Append `attest_shift` to `backend/services/time_entries.py`**

Extend the `fastapi` import at the top of the module (add a new line below the existing imports):

```python
from fastapi import HTTPException, status
```

Then append:

```python
def _reject(code: str, message: str, http_status: int) -> HTTPException:
    """The detail shape CheckInRejected is translated into by
    backend/routers/check_ins.py, so every refusal in this feature reads the
    same way to the frontend."""
    return HTTPException(
        status_code=http_status, detail={"code": code, "message": message}
    )


async def attest_shift(
    db: AsyncSession,
    company_id: str,
    shift_id: str,
    user: User,
    reason: str | None,
) -> TimeEntry:
    """Record that a manager confirmed an unscanned shift was worked.

    entry_exists is raised both by the check below and by catching the
    IntegrityError on uq_time_entries_shift, so a check-then-insert race
    resolves the same way whichever side loses — the pattern record_check_in
    uses for the counter constraint.
    """
    row = (await db.execute(
        select(Shift, Location, ShiftSchedule.status)
        .join(ShiftSchedule, ShiftSchedule.id == Shift.shift_schedule_id)
        .join(Location, Location.id == Shift.location_id)
        .where(Shift.id == shift_id, Shift.company_id == company_id)
    )).first()
    if row is None:
        raise _reject("shift_not_found", "That shift no longer exists.", 404)
    shift, location, schedule_status = row

    if schedule_status != "approved":
        raise _reject(
            "shift_not_approved",
            "Only shifts on an approved schedule can be confirmed.",
            status.HTTP_409_CONFLICT,
        )

    start = _as_utc(shift.start_time)
    if start >= datetime.now(timezone.utc):
        raise _reject(
            "shift_not_started",
            "That shift has not started yet.",
            status.HTTP_409_CONFLICT,
        )

    already = (await db.execute(
        select(TimeEntry.id).where(TimeEntry.shift_id == shift.id).limit(1)
    )).scalar_one_or_none()
    if already is not None:
        raise _reject(
            "entry_exists",
            "That shift already has payable hours recorded.",
            status.HTTP_409_CONFLICT,
        )

    try:
        minutes = paid_minutes(shift.start_time, shift.end_time)
    except ValueError:
        logger.warning(
            "payroll.malformed_shift shift_id=%s company_id=%s", shift.id,
            company_id,
        )
        raise _reject(
            "malformed_shift",
            "That shift's start and end times are the same; fix the shift "
            "before confirming it.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    entry = TimeEntry(
        company_id=company_id,
        location_id=shift.location_id,
        employee_id=shift.employee_id,
        shift_id=shift.id,
        role_id=shift.role_id,
        role_name=shift.role_name,
        pay_date=pay_date_for(location.timezone, start),
        start_time=shift.start_time,
        end_time=shift.end_time,
        paid_minutes=minutes,
        source=TIME_ENTRY_MANAGER_ATTESTED,
        check_in_id=None,
        checked_in_at=None,
        lateness_minutes=None,
        attested_by_user_id=user.id,
        attested_at=datetime.now(timezone.utc),
        attestation_reason=reason,
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _reject(
            "entry_exists",
            "That shift already has payable hours recorded.",
            status.HTTP_409_CONFLICT,
        )
    await db.refresh(entry)
    return entry
```

Add `TIME_ENTRY_MANAGER_ATTESTED` to the existing `from backend.models.time_entry import ...` line so it reads:

```python
from backend.models.time_entry import (
    TIME_ENTRY_CHECKED_IN,
    TIME_ENTRY_MANAGER_ATTESTED,
)
```

- [ ] **Step 4: Add the endpoint to `backend/routers/payroll.py`**

Extend the schema import with `PayrollAttestRequest`, and the service import with `attest_shift`. Then append:

```python
@router.post("/attest", response_model=TimeEntryRowSchema,
             status_code=status.HTTP_201_CREATED)
async def attest(
    body: PayrollAttestRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> TimeEntryRowSchema:
    """Confirm an unscanned shift was worked. Phones die; nobody misses a
    paycheque over a QR scan."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")

    entry = await attest_shift(
        db, company_id, body.shift_id, current_user, body.reason
    )
    rows = await list_time_entries(
        db, company_id, entry.pay_date, entry.pay_date
    )
    row = next(r for r in rows if r.id == entry.id)
    return TimeEntryRowSchema(**asdict(row))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_attest.py -q`
Expected: PASS — 9 passed

- [ ] **Step 6: Commit**

```bash
git add backend/services/time_entries.py backend/routers/payroll.py \
        tests/test_payroll_attest.py
git commit -m "feat(payroll): manager attestation with a full audit trail (#78)"
```

---

### Task 6: Approval

**Files:**
- Modify: `backend/services/time_entries.py` (append `ApproveResult`, `approve_entries`)
- Modify: `backend/routers/payroll.py` (add `POST /payroll/approve`)
- Modify: `tests/test_payroll_api.py` (append the approval tests)

**Interfaces:**
- Consumes: `TimeEntry` (Task 1); `PayrollApproveRequest`, `PayrollApproveResponse` (Task 4).
- Produces:
  - `@dataclass(frozen=True) ApproveResult(approved: int, already_approved: int)`
  - `async approve_entries(db: AsyncSession, company_id: str, range_start: date, range_end: date, user: User, location_id: str | None = None, entry_ids: list[str] | None = None) -> ApproveResult` — raises `400 invalid_entry_ids`
  - `POST /payroll/approve` → `200 PayrollApproveResponse`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payroll_api.py`:

```python
# --- approval ---------------------------------------------------------------

async def _derive(client: AsyncClient, t: SimpleNamespace) -> None:
    resp = await client.post("/api/v1/payroll/entries/derive",
                             json=_range_body(t), headers=t.manager_headers)
    assert resp.status_code == 200, resp.text


async def _entry_ids(client: AsyncClient, t: SimpleNamespace) -> list[str]:
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=t.manager_headers,
    )
    return [r["id"] for r in resp.json()["rows"]]


async def test_approving_a_range_stamps_every_unapproved_entry(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _worked_shift(db_session, paid, days_ago=2)
    await _derive(client, paid)

    resp = await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"approved": 2, "already_approved": 0}

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert listed.json()["approved_entries"] == 2


async def test_approving_twice_does_not_move_the_timestamp(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """The approval timestamp is an audit fact about when a human said yes,
    and a second click must not rewrite it."""
    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                      headers=paid.manager_headers)
    first_stamp = (await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )).json()["rows"][0]["approved_at"]

    second = await client.post("/api/v1/payroll/approve",
                               json=_range_body(paid),
                               headers=paid.manager_headers)

    assert second.json() == {"approved": 0, "already_approved": 1}
    after = (await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )).json()["rows"][0]["approved_at"]
    assert after == first_stamp


async def test_entry_ids_approves_only_the_named_subset(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _worked_shift(db_session, paid, days_ago=2)
    await _derive(client, paid)
    ids = await _entry_ids(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(paid, entry_ids=[ids[0]]),
        headers=paid.manager_headers,
    )

    assert resp.json() == {"approved": 1, "already_approved": 0}


async def test_entry_ids_naming_another_companys_entry_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    other = await _tenant(db_session, paid=True)
    await _worked_shift(db_session, other)
    await _derive(client, other)
    stranger_ids = await _entry_ids(client, other)
    await _worked_shift(db_session, paid)
    await _derive(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(paid, entry_ids=stranger_ids),
        headers=paid.manager_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_entry_ids"


async def test_entry_ids_outside_the_range_are_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _derive(client, paid)
    ids = await _entry_ids(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(
            paid,
            range_start=(TODAY - timedelta(days=30)).isoformat(),
            range_end=(TODAY - timedelta(days=20)).isoformat(),
            entry_ids=ids,
        ),
        headers=paid.manager_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_entry_ids"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q -k "approv or entry_ids"`
Expected: FAIL — 5 failed; `/payroll/approve` is not routed, so every call returns 404.

- [ ] **Step 3: Append `ApproveResult` and `approve_entries` to `backend/services/time_entries.py`**

Put the dataclass next to the other result dataclasses, and the function at the end of the module:

```python
@dataclass(frozen=True)
class ApproveResult:
    approved: int
    already_approved: int
```

```python
async def approve_entries(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    user: User,
    location_id: str | None = None,
    entry_ids: list[str] | None = None,
) -> ApproveResult:
    """Say yes to a set of payable hours. Nothing exports unapproved.

    Already-approved entries are counted and left untouched, not re-stamped:
    the approval timestamp is an audit fact about when a human said yes.
    """
    query = select(TimeEntry).where(
        TimeEntry.company_id == company_id,
        TimeEntry.pay_date >= range_start,
        TimeEntry.pay_date <= range_end,
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if entry_ids:
        query = query.where(TimeEntry.id.in_(entry_ids))

    rows = list((await db.execute(query)).scalars().all())

    if entry_ids and len(rows) != len(set(entry_ids)):
        # An id that named another company's entry, or one outside the range,
        # simply did not come back. Refusing the whole call is what keeps a
        # partial approval from looking like a complete one. An EMPTY list is
        # treated as "everything in range", exactly like null — the spec makes
        # a non-empty list the trigger for subset mode.
        raise _reject(
            "invalid_entry_ids",
            "Some of those entries are not in this range or not yours. "
            "Nothing was approved.",
            status.HTTP_400_BAD_REQUEST,
        )

    now = datetime.now(timezone.utc)
    approved = 0
    already_approved = 0
    for row in rows:
        if row.approved_at is not None:
            already_approved += 1
            continue
        row.approved_at = now
        row.approved_by_user_id = user.id
        approved += 1

    await db.commit()
    return ApproveResult(approved=approved, already_approved=already_approved)
```

- [ ] **Step 4: Add the endpoint to `backend/routers/payroll.py`**

Extend the schema import with `PayrollApproveRequest, PayrollApproveResponse` and the service import with `approve_entries`. Then append:

```python
@router.post("/approve", response_model=PayrollApproveResponse)
async def approve(
    body: PayrollApproveRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollApproveResponse:
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    result = await approve_entries(
        db, company_id, body.range_start, body.range_end, current_user,
        body.location_id, body.entry_ids,
    )
    return PayrollApproveResponse(**asdict(result))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q -k "approv or entry_ids"`
Expected: PASS — 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/services/time_entries.py backend/routers/payroll.py \
        tests/test_payroll_api.py
git commit -m "feat(payroll): approve payable hours before anything exports (#78)"
```

---

### Task 7: CSV rendering, the export, and its audit row

**Files:**
- Create: `backend/services/payroll_export.py`
- Modify: `backend/routers/payroll.py` (add `POST /payroll/export`)
- Test: `tests/test_payroll_export_csv.py` (pure)
- Modify: `tests/test_payroll_api.py` (append the export tests)

**Interfaces:**
- Consumes: `TimeEntry` (Task 1); `PayrollExport` (Task 1); `_reject` (Task 5); `PayrollExportRequest` (Task 4); `get_ownership_group_id` from `backend.services.billing`.
- Produces (in `backend.services.payroll_export`):
  - `CSV_HEADER: list[str]` — `["employee_id", "employee_name", "pay_date", "location_id", "location_name", "role_id", "role_name", "start_time", "end_time", "paid_hours", "source", "checked_in_at", "lateness_minutes"]`
  - `@dataclass(frozen=True) PayrollCsvRow(employee_id: str, employee_name: str, pay_date: date, location_id: str, location_name: str, role_id: str, role_name: str, start_time: datetime, end_time: datetime, paid_minutes: int, source: str, checked_in_at: datetime | None, lateness_minutes: int | None)`
  - `render_csv(rows: list[PayrollCsvRow]) -> str` — pure
  - `async export_approved(db: AsyncSession, company_id: str, user: User, range_start: date, range_end: date, location_id: str | None = None, include_exported: bool = False) -> tuple[str, PayrollExport]` — raises `409 nothing_to_export`
  - `POST /payroll/export` → `200` `text/csv` with `Content-Disposition`

**Naming note:** the dataclass field carrying the paid duration is `paid_minutes` (the domain value, per the spec) while the CSV column it renders into is `paid_hours`. Naming the field `paid_hours` while it holds minutes would be a lie; the conversion lives in `render_csv`, where it is tested.

- [ ] **Step 1: Write the failing pure test**

Create `tests/test_payroll_export_csv.py`:

```python
"""render_csv, on its own. No session, no HTTP.

The formatting rules live in one function precisely so they can be asserted
in one place — a payroll bureau reads this file, and a column that lies is
worse than a column that is missing.
"""

import csv
import io
from datetime import date, datetime, timedelta, timezone

from backend.services.payroll_export import CSV_HEADER, PayrollCsvRow, render_csv

NY = timezone(timedelta(hours=-4))


def _row(**overrides) -> PayrollCsvRow:
    fields = dict(
        employee_id="m1m2m3m4",
        employee_name="Dana Okafor",
        pay_date=date(2026, 9, 15),
        location_id="ab12cd34",
        location_name="Flatbush Ave",
        role_id="r1r2r3r4",
        role_name="Role A",
        start_time=datetime(2026, 9, 15, 22, 0, tzinfo=NY),
        end_time=datetime(2026, 9, 16, 6, 0, tzinfo=NY),
        paid_minutes=480,
        source="checked_in",
        checked_in_at=datetime(2026, 9, 15, 21, 56, tzinfo=NY),
        lateness_minutes=-4,
    )
    fields.update(overrides)
    return PayrollCsvRow(**fields)


def _parse(content: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.lstrip("﻿"))))


def test_the_header_matches_exactly_and_in_order():
    assert _parse(render_csv([]))[0] == CSV_HEADER


def test_the_output_starts_with_a_bom():
    """We ship in 19 locales including Arabic, Bengali, Tamil and Telugu.
    Excel on Windows renders a BOM-less UTF-8 CSV as mojibake, which a payroll
    clerk reads as our bug."""
    assert render_csv([_row()]).startswith("﻿")


def test_the_line_terminator_is_crlf():
    """RFC 4180, which is what Excel and every payroll import template
    expect."""
    content = render_csv([_row()])
    assert content.endswith("\r\n")
    assert "\r\n" in content.rstrip("\r\n")


def test_paid_hours_renders_to_two_decimal_places():
    assert _parse(render_csv([_row()]))[1][CSV_HEADER.index("paid_hours")] == "8.00"
    assert _parse(render_csv([_row(paid_minutes=450)]))[1][
        CSV_HEADER.index("paid_hours")] == "7.50"


def test_timestamps_keep_the_offset_they_were_stored_with():
    row = _parse(render_csv([_row()]))[1]
    assert row[CSV_HEADER.index("start_time")] == "2026-09-15T22:00:00-04:00"
    assert row[CSV_HEADER.index("end_time")] == "2026-09-16T06:00:00-04:00"
    assert row[CSV_HEADER.index("pay_date")] == "2026-09-15"


def test_an_attested_row_reports_nothing_rather_than_zero():
    """Empty, not "0": we did not observe an on-time arrival, we observed
    nothing. And not "None", which is a Python repr leaking into a pay file."""
    row = _parse(render_csv([
        _row(source="manager_attested", checked_in_at=None,
             lateness_minutes=None)
    ]))[1]
    assert row[CSV_HEADER.index("checked_in_at")] == ""
    assert row[CSV_HEADER.index("lateness_minutes")] == ""
    assert row[CSV_HEADER.index("source")] == "manager_attested"


def test_a_negative_lateness_renders_signed():
    row = _parse(render_csv([_row(lateness_minutes=-4)]))[1]
    assert row[CSV_HEADER.index("lateness_minutes")] == "-4"


def test_a_name_with_a_comma_round_trips():
    row = _parse(render_csv([_row(employee_name="Okafor, Dana")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "Okafor, Dana"


def test_a_devanagari_name_round_trips():
    row = _parse(render_csv([_row(employee_name="दाना ओकाफोर")]))[1]
    assert row[CSV_HEADER.index("employee_name")] == "दाना ओकाफोर"


def test_the_attestation_reason_appears_nowhere():
    """It is an internal audit note about one employee, written by their
    manager, and the CSV leaves the building."""
    assert "attestation_reason" not in CSV_HEADER
    assert "reason" not in render_csv([_row(source="manager_attested",
                                            checked_in_at=None,
                                            lateness_minutes=None)])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_export_csv.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.payroll_export'`

- [ ] **Step 3: Create `backend/services/payroll_export.py`**

```python
"""Rendering approved hours as CSV, and recording that it happened.

CSV is the one "provider" every payroll system accepts today: it needs no
partnership, and it validates that our hours are correct before any live write
can embarrass us. The exporter is a plain function, not a PayrollProvider
protocol — see "Future Adapter Seam" in the spec.
"""

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, PayrollExport, TimeEntry, User
from backend.services.billing import get_ownership_group_id

logger = logging.getLogger(__name__)

CSV_HEADER = [
    "employee_id", "employee_name", "pay_date",
    "location_id", "location_name", "role_id", "role_name",
    "start_time", "end_time", "paid_hours",
    "source", "checked_in_at", "lateness_minutes",
]


@dataclass(frozen=True)
class PayrollCsvRow:
    """One exported line, carrying domain values rather than pre-formatted
    text — the formatting rules live in render_csv so they are testable in one
    place. `paid_minutes` renders into the `paid_hours` column."""

    employee_id: str
    employee_name: str
    pay_date: date
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None


def render_csv(rows: list[PayrollCsvRow]) -> str:
    """Pure. RFC 4180 line endings and a UTF-8 BOM.

    The BOM is not decoration: we ship in 19 locales including Arabic,
    Bengali, Tamil and Telugu, employee names are entered in those scripts,
    and Excel on Windows renders a BOM-less UTF-8 CSV as mojibake — which a
    payroll clerk will read as our bug.

    Hours rather than minutes because that is what every payroll import
    template takes. checked_in_at and lateness_minutes are EMPTY, not "0", for
    an attested row: we did not observe an on-time arrival, we observed
    nothing.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([
            row.employee_id,
            row.employee_name,
            row.pay_date.isoformat(),
            row.location_id,
            row.location_name,
            row.role_id,
            row.role_name,
            row.start_time.isoformat(),
            row.end_time.isoformat(),
            f"{row.paid_minutes / 60:.2f}",
            row.source,
            row.checked_in_at.isoformat() if row.checked_in_at else "",
            "" if row.lateness_minutes is None else str(row.lateness_minutes),
        ])
    return "﻿" + buf.getvalue()


async def export_approved(
    db: AsyncSession,
    company_id: str,
    user: User,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    include_exported: bool = False,
) -> tuple[str, PayrollExport]:
    """Select, render, stamp and log — in one commit.

    One commit so the audit row and the stamps cannot disagree.

    exported_at and payroll_export_id are stamped only where they are NULL.
    include_exported=true exists for re-downloading a file a manager lost; it
    never re-stamps, because deleting an audit log must never un-export a pay
    period and a re-download is not a second export.
    """
    query = (
        select(TimeEntry, Employee.full_name, Location.name)
        .join(Employee, Employee.id == TimeEntry.employee_id)
        .join(Location, Location.id == TimeEntry.location_id)
        .where(
            TimeEntry.company_id == company_id,
            TimeEntry.pay_date >= range_start,
            TimeEntry.pay_date <= range_end,
            TimeEntry.approved_at.isnot(None),
        )
        .order_by(TimeEntry.pay_date, Employee.full_name)
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if not include_exported:
        query = query.where(TimeEntry.exported_at.is_(None))

    found = (await db.execute(query)).all()
    if not found:
        skipped = await _count_already_exported(
            db, company_id, range_start, range_end, location_id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "nothing_to_export",
                "message": (
                    f"No approved, unexported hours in this range. "
                    f"{skipped} approved entr"
                    f"{'y was' if skipped == 1 else 'ies were'} already "
                    f"exported."
                ),
                "already_exported": skipped,
            },
        )

    now = datetime.now(timezone.utc)
    export = PayrollExport(
        company_id=company_id,
        # None for the OG-less dev company — hence the nullable column.
        ownership_group_id=await get_ownership_group_id(db, company_id),
        exported_by_user_id=user.id,
        format="csv",
        location_id=location_id,
        range_start=range_start,
        range_end=range_end,
        entry_count=len(found),
        paid_minutes_total=sum(entry.paid_minutes for entry, _, _ in found),
    )
    db.add(export)
    await db.flush()

    csv_rows: list[PayrollCsvRow] = []
    for entry, employee_name, location_name in found:
        csv_rows.append(PayrollCsvRow(
            employee_id=entry.employee_id,
            employee_name=employee_name,
            pay_date=entry.pay_date,
            location_id=entry.location_id,
            location_name=location_name,
            role_id=entry.role_id,
            role_name=entry.role_name,
            start_time=entry.start_time,
            end_time=entry.end_time,
            paid_minutes=entry.paid_minutes,
            source=entry.source,
            checked_in_at=entry.checked_in_at,
            lateness_minutes=entry.lateness_minutes,
        ))
        if entry.exported_at is None:
            entry.exported_at = now
            entry.payroll_export_id = export.id

    content = render_csv(csv_rows)
    await db.commit()
    await db.refresh(export)
    logger.info(
        "payroll.export company_id=%s export_id=%s entries=%d minutes=%d",
        company_id, export.id, export.entry_count, export.paid_minutes_total,
    )
    return content, export


async def _count_already_exported(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None,
) -> int:
    """So "nothing happened" is never mysterious."""
    query = select(func.count(TimeEntry.id)).where(
        TimeEntry.company_id == company_id,
        TimeEntry.pay_date >= range_start,
        TimeEntry.pay_date <= range_end,
        TimeEntry.approved_at.isnot(None),
        TimeEntry.exported_at.isnot(None),
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    return (await db.execute(query)).scalar_one()
```

- [ ] **Step 4: Run the pure test to verify it passes**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_export_csv.py -q`
Expected: PASS — 10 passed

- [ ] **Step 5: Write the failing endpoint tests**

Append to `tests/test_payroll_api.py`:

```python
# --- export -----------------------------------------------------------------

async def _approve_all(client: AsyncClient, t: SimpleNamespace) -> None:
    resp = await client.post("/api/v1/payroll/approve", json=_range_body(t),
                             headers=t.manager_headers)
    assert resp.status_code == 200, resp.text


async def test_export_with_nothing_approved_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid)
    await _derive(client, paid)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "nothing_to_export"


async def test_approve_then_export_returns_csv_and_writes_one_audit_row(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    from sqlalchemy import select as _select

    from backend.models import PayrollExport, TimeEntry

    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in resp.headers["content-disposition"]
    assert "employee_name" in resp.text

    exports = (await db_session.execute(_select(PayrollExport))).scalars().all()
    assert len(exports) == 1
    assert exports[0].entry_count == 1
    assert exports[0].paid_minutes_total == 480
    assert exports[0].format == "csv"
    assert exports[0].exported_by_user_id == paid.manager_id

    entry = (await db_session.execute(_select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    assert entry.exported_at is not None
    assert entry.payroll_export_id == exports[0].id


async def test_a_second_export_of_the_same_range_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "nothing_to_export"
    assert resp.json()["detail"]["already_exported"] == 1


async def test_include_exported_redownloads_without_restamping(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """A re-download is a thing that happened and the log should say so — but
    it must not move exported_at, which is what stops a pay period being
    exported twice."""
    from sqlalchemy import select as _select

    from backend.models import PayrollExport, TimeEntry

    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)
    entry = (await db_session.execute(_select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    first_stamp = entry.exported_at
    first_export_id = entry.payroll_export_id

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=True),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    assert "Dana Okafor" in resp.text
    await db_session.refresh(entry)
    assert entry.exported_at == first_stamp
    assert entry.payroll_export_id == first_export_id
    exports = (await db_session.execute(_select(PayrollExport))).scalars().all()
    assert len(exports) == 2
```

- [ ] **Step 6: Run the endpoint tests to verify they fail**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q -k export`
Expected: FAIL — 4 failed; `/payroll/export` is not routed (404).

- [ ] **Step 7: Add the endpoint to `backend/routers/payroll.py`**

Add these imports:

```python
from fastapi.responses import Response
from sqlalchemy import select

from backend.models import Company
from backend.schemas.payroll import PayrollExportRequest
from backend.services.payroll_export import export_approved
```

Then append:

```python
@router.post("/export")
async def export_csv(
    body: PayrollExportRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download approved hours as CSV.

    A POST rather than a GET despite being a download: it mutates exported_at
    and writes an audit row, and a browser prefetch of a GET must not silently
    consume a pay period. It also keeps the download on the authenticated
    fetch path — get_current_user reads the Authorization header only, so a
    plain <a href> download would arrive unauthenticated.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    content, _export = await export_approved(
        db, company_id, current_user, body.range_start, body.range_end,
        body.location_id, body.include_exported,
    )

    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one()
    filename = (
        f"payroll_{slug}_{body.range_start.isoformat()}_"
        f"{body.range_end.isoformat()}.csv"
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 8: Run the whole payroll API file**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_api.py -q`
Expected: PASS — every test in the file, including the `paid_only` and `manager_only` sweeps that were partially failing since Task 4.

- [ ] **Step 9: Commit**

```bash
git add backend/services/payroll_export.py backend/routers/payroll.py \
        tests/test_payroll_export_csv.py tests/test_payroll_api.py
git commit -m "feat(payroll): CSV export with an audit row and one-way exported_at (#78)"
```

---

### Task 8: Retention — sweep the export log, never the pay record

**Files:**
- Modify: `backend/config.py` (retention block, after `RETENTION_SIGNUP_SIGNALS_DAYS`)
- Modify: `backend/services/data_retention.py` (step 7 gains a line; new step 9)
- Test: `tests/test_payroll_retention.py`

**Interfaces:**
- Consumes: `TimeEntry`, `PayrollExport` (Task 1).
- Produces:
  - `settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS: int = 365`
  - `run_data_retention(db)` summary gains the key `"payroll_export_logs_deleted"`
  - Step 7 nulls `time_entries.check_in_id` for check-ins it is about to delete

- [ ] **Step 1: Write the failing test**

Create `tests/test_payroll_retention.py`:

```python
"""Retention cuts the audit log, never the pay record.

Deleting an audit log must never un-export a pay period and make it eligible
for a second push, and sweeping a check-in must never erase what a pay record
was based on. Both directions are asserted here.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, PayrollExport, Region, Role,
    Shift, ShiftSchedule, TimeEntry, User,
)
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.models.time_entry import TIME_ENTRY_CHECKED_IN
from backend.services.data_retention import run_data_retention
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

TODAY = datetime.now(timezone.utc).date()


async def _seed(
    db: AsyncSession, *, export_age_days: int, check_in_age_days: int
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()

    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="L", timezone="UTC"))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com",
                    location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.flush()

    schedule_id, shift_id = _id(), _id()
    start = now - timedelta(days=check_in_age_days)
    db.add(ShiftSchedule(id=schedule_id, company_id=company_id,
                         location_id=location_id, week_start_date=start.date(),
                         status="approved"))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=company_id,
                 shift_schedule_id=schedule_id, location_id=location_id,
                 employee_id=employee_id, role_id=role_id, role_name=role.name,
                 date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=8)))
    check_in_id = _id()
    db.add(EmployeeCheckIn(
        id=check_in_id, company_id=company_id, location_id=location_id,
        employee_id=employee_id, shift_id=shift_id, checked_in_at=start,
        local_date=start.date(), counter=0, status=CHECK_IN_MATCHED,
        minutes_from_start=-4,
    ))
    export_id = _id()
    db.add(PayrollExport(
        id=export_id, company_id=company_id, ownership_group_id=None,
        exported_by_user_id=user_id, format="csv", location_id=None,
        range_start=start.date(), range_end=start.date(), entry_count=1,
        paid_minutes_total=480,
        created_at=now - timedelta(days=export_age_days),
    ))
    await db.flush()
    entry_id = _id()
    db.add(TimeEntry(
        id=entry_id, company_id=company_id, location_id=location_id,
        employee_id=employee_id, shift_id=shift_id, role_id=role_id,
        role_name=role.name, pay_date=start.date(), start_time=start,
        end_time=start + timedelta(hours=8), paid_minutes=480,
        source=TIME_ENTRY_CHECKED_IN, check_in_id=check_in_id,
        checked_in_at=start, lateness_minutes=-4,
        exported_at=now - timedelta(days=export_age_days),
        payroll_export_id=export_id,
    ))
    await db.commit()
    return SimpleNamespace(entry_id=entry_id, export_id=export_id,
                           check_in_id=check_in_id)


async def test_an_old_export_log_row_is_deleted_and_counted(
    db_session: AsyncSession
):
    s = await _seed(
        db_session,
        export_age_days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS + 10,
        check_in_age_days=1,
    )

    summary = await run_data_retention(db_session)

    assert summary["payroll_export_logs_deleted"] == 1
    assert (await db_session.execute(
        select(func.count()).select_from(PayrollExport)
    )).scalar_one() == 0


async def test_the_entries_keep_exported_at_and_lose_only_the_link(
    db_session: AsyncSession
):
    """exported_at survives. Deleting an audit log must never un-export a pay
    period and make it eligible for a second push."""
    s = await _seed(
        db_session,
        export_age_days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS + 10,
        check_in_age_days=1,
    )

    await run_data_retention(db_session)

    entry = await db_session.get(TimeEntry, s.entry_id)
    await db_session.refresh(entry)
    assert entry is not None
    assert entry.exported_at is not None
    assert entry.payroll_export_id is None


async def test_a_recent_export_log_row_survives(db_session: AsyncSession):
    await _seed(db_session, export_age_days=1, check_in_age_days=1)

    summary = await run_data_retention(db_session)

    assert summary["payroll_export_logs_deleted"] == 0
    assert (await db_session.execute(
        select(func.count()).select_from(PayrollExport)
    )).scalar_one() == 1


async def test_sweeping_a_check_in_leaves_the_pay_record_able_to_explain_itself(
    db_session: AsyncSession
):
    """checked_in_at and lateness_minutes are denormalised precisely so the
    pay record outlives the scan. Only the link is cleared."""
    s = await _seed(
        db_session, export_age_days=1,
        check_in_age_days=settings.RETENTION_CHECKINS_DAYS + 10,
    )

    summary = await run_data_retention(db_session)

    assert summary["old_check_ins_deleted"] == 1
    entry = await db_session.get(TimeEntry, s.entry_id)
    await db_session.refresh(entry)
    assert entry.check_in_id is None
    assert entry.checked_in_at is not None
    assert entry.lateness_minutes == -4


async def test_the_sweep_reports_zero_rather_than_omitting_the_key(
    db_session: AsyncSession
):
    """Callers read the summary by key; a missing key is a KeyError, not a
    zero."""
    summary = await run_data_retention(db_session)
    assert summary["payroll_export_logs_deleted"] == 0


async def test_the_export_log_window_is_a_year():
    """Matching RETENTION_REVOKED_CONSENTS_DAYS: an export is a pay-affecting
    action and "who pulled that file, when, covering which dates" is an
    annual-cycle audit question."""
    assert settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS == 365
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_retention.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'RETENTION_PAYROLL_EXPORT_LOGS_DAYS'`

- [ ] **Step 3: Add the setting to `backend/config.py`**

Immediately after the `RETENTION_SIGNUP_SIGNALS_DAYS: int = 180` line:

```python
    # Payroll export audit rows (#78). A year, matching
    # RETENTION_REVOKED_CONSENTS_DAYS: an export is a pay-affecting action and
    # the question "who pulled that file, when, covering which dates" is one a
    # customer asks during an audit, which is an annual cycle. Deleting the log
    # does NOT clear time_entries.exported_at — that marker is what stops a pay
    # period being exported twice and it outlives the log deliberately.
    RETENTION_PAYROLL_EXPORT_LOGS_DAYS: int = 365
```

- [ ] **Step 4: Extend `backend/services/data_retention.py`**

Add `PayrollExport` and `TimeEntry` to the `from backend.models import (...)` block.

Replace the body of step 7 with:

```python
    # 7. Check-ins older than the retained window. Issue #63 specifies six
    #    months of history; without this the table grows without bound and
    #    the figure is decoration.
    cutoff_check_ins = now - timedelta(days=settings.RETENTION_CHECKINS_DAYS)
    expiring_check_in_ids = list((await db.execute(
        select(EmployeeCheckIn.id).where(
            EmployeeCheckIn.checked_in_at < cutoff_check_ins
        )
    )).scalars().all())
    if expiring_check_in_ids:
        # A pay record must still be able to say what it was based on after
        # the scan is gone (#78). Only the LINK is cleared: checked_in_at and
        # lateness_minutes are denormalised precisely so they outlive it.
        await db.execute(
            update(TimeEntry)
            .where(TimeEntry.check_in_id.in_(expiring_check_in_ids))
            .values(check_in_id=None)
        )
    result = await db.execute(
        delete(EmployeeCheckIn).where(
            EmployeeCheckIn.checked_in_at < cutoff_check_ins
        )
    )
    summary["old_check_ins_deleted"] = result.rowcount
```

Insert a new step 9 immediately before `await db.commit()`:

```python
    # 9. Payroll export audit rows past their window (#78). The entries they
    #    describe are NOT deleted — exported_at is the idempotency marker and
    #    outliving the log is the point. Only the link is cleared, and the
    #    UPDATE must run before the DELETE or the FK blocks it.
    #
    #    time_entries themselves are never swept: they are pay records, and no
    #    retention period for them was decided in #78.
    cutoff_payroll_logs = now - timedelta(
        days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS
    )
    expiring_export_ids = list((await db.execute(
        select(PayrollExport.id).where(
            PayrollExport.created_at < cutoff_payroll_logs
        )
    )).scalars().all())
    if expiring_export_ids:
        await db.execute(
            update(TimeEntry)
            .where(TimeEntry.payroll_export_id.in_(expiring_export_ids))
            .values(payroll_export_id=None)
        )
    result = await db.execute(
        delete(PayrollExport).where(
            PayrollExport.created_at < cutoff_payroll_logs
        )
    )
    summary["payroll_export_logs_deleted"] = result.rowcount
```

- [ ] **Step 5: Run the new test and the existing check-in retention test**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_retention.py tests/test_check_in_retention.py -q`
Expected: PASS — 6 + 4 passed

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/services/data_retention.py \
        tests/test_payroll_retention.py
git commit -m "feat(payroll): sweep the export log, never the pay record (#78)"
```

---

### Task 9: Attestation rate on the check-in report

**Files:**
- Modify: `backend/services/time_entries.py` (append `AttestationRate`, `attestation_rates`)
- Modify: `backend/schemas/check_in.py` (`AttestationRateRow`; `attestation` field)
- Modify: `backend/routers/check_ins.py` (`get_check_in_report`)
- Test: `tests/test_check_in_report_attestation.py`

**Interfaces:**
- Consumes: `TimeEntry`, `TIME_ENTRY_MANAGER_ATTESTED` (Task 1); `Location`.
- Produces:
  - `@dataclass(frozen=True) AttestationRate(location_id: str, location_name: str, entries: int, attested: int, rate: float)`
  - `async attestation_rates(db: AsyncSession, company_id: str, since: date) -> list[AttestationRate]` — ordered by `rate` descending
  - `backend.schemas.check_in.AttestationRateRow(location_id: str, location_name: str, entries: int, attested: int, rate: float)`
  - `CheckInReportResponse` gains `attestation: list[AttestationRateRow]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_check_in_report_attestation.py`:

```python
"""The attestation rate is REPORTING, not blocking.

A location attesting most of its shifts has either a broken QR flow or
something worth a conversation; withholding pay is not the response to either.
The last test in this file is the one that fails if somebody later wires
enforcement onto a reporting number.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_payroll_api import _range_body, _tenant, _worked_shift

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_the_report_carries_a_row_per_location_with_the_right_rate(
    client: AsyncClient, db_session: AsyncSession, paid
):
    scanned = await _worked_shift(db_session, paid, days_ago=1, checked_in=True)
    missed = await _worked_shift(db_session, paid, days_ago=2, checked_in=False)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/attest", json={"shift_id": missed},
                      headers=paid.manager_headers)

    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    attestation = resp.json()["attestation"]
    row = next(r for r in attestation if r["location_id"] == paid.location_id)
    assert row["location_name"] == "Flatbush Ave"
    assert row["entries"] == 2
    assert row["attested"] == 1
    assert row["rate"] == 0.5


async def test_a_location_with_no_entries_reports_zero_not_a_crash(
    client: AsyncClient, paid
):
    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid.manager_headers)

    row = next(r for r in resp.json()["attestation"]
               if r["location_id"] == paid.location_id)
    assert row["entries"] == 0
    assert row["attested"] == 0
    assert row["rate"] == 0.0


async def test_a_hundred_percent_attestation_rate_blocks_nothing(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """Attest, approve and export all still succeed. Same posture as the
    weekly abuse report and ownership_groups.signup_*."""
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    attested = await client.post("/api/v1/payroll/attest",
                                 json={"shift_id": shift_id},
                                 headers=paid.manager_headers)
    assert attested.status_code == 201, attested.text

    report = await client.get("/api/v1/check-ins/report",
                              headers=paid.manager_headers)
    row = next(r for r in report.json()["attestation"]
               if r["location_id"] == paid.location_id)
    assert row["rate"] == 1.0

    approved = await client.post("/api/v1/payroll/approve",
                                 json=_range_body(paid),
                                 headers=paid.manager_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] == 1

    exported = await client.post(
        "/api/v1/payroll/export",
        json=_range_body(paid, include_exported=False),
        headers=paid.manager_headers,
    )
    assert exported.status_code == 200, exported.text
    assert "manager_attested" in exported.text


async def test_the_report_is_still_manager_only_and_paid_only(
    client: AsyncClient, db_session: AsyncSession, paid
):
    free = await _tenant(db_session, paid=False)

    assert (await client.get("/api/v1/check-ins/report",
                             headers=paid.employee_headers)).status_code == 403
    assert (await client.get("/api/v1/check-ins/report",
                             headers=free.manager_headers)).status_code == 402
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_check_in_report_attestation.py -q`
Expected: FAIL — `KeyError: 'attestation'`

- [ ] **Step 3: Append `attestation_rates` to `backend/services/time_entries.py`**

Add `and_`, `case` and `func` to the `sqlalchemy` import so it reads:

```python
from sqlalchemy import and_, case, func, select
```

Then append:

```python
@dataclass(frozen=True)
class AttestationRate:
    """A plain dataclass local to this service; the router maps it to the
    AttestationRateRow schema, keeping schema imports out of services the way
    backend/routers/check_ins.py already does for the report rows."""

    location_id: str
    location_name: str
    entries: int
    attested: int
    rate: float


async def attestation_rates(
    db: AsyncSession, company_id: str, since: date
) -> list[AttestationRate]:
    """Share of payable hours confirmed by a manager instead of a check-in.

    REPORTING ONLY. Nothing reads `rate` to block an attestation, refuse an
    export, or cap anything.

    Outer-joined from Location so a location with zero entries reports 0.0
    rather than vanishing from the list — a missing row reads as "fine" when
    it actually means "no data".
    """
    attested_expr = func.sum(
        case((TimeEntry.source == TIME_ENTRY_MANAGER_ATTESTED, 1), else_=0)
    )
    rows = (await db.execute(
        select(Location.id, Location.name, func.count(TimeEntry.id), attested_expr)
        .outerjoin(
            TimeEntry,
            and_(
                TimeEntry.location_id == Location.id,
                TimeEntry.company_id == company_id,
                TimeEntry.pay_date >= since,
            ),
        )
        .where(Location.company_id == company_id)
        .group_by(Location.id, Location.name)
    )).all()

    rates = [
        AttestationRate(
            location_id=location_id,
            location_name=location_name,
            entries=entries,
            attested=int(attested or 0),
            rate=(int(attested or 0) / entries) if entries else 0.0,
        )
        for location_id, location_name, entries, attested in rows
    ]
    # Descending, so the locations worth looking at sort to the top.
    rates.sort(key=lambda r: r.rate, reverse=True)
    return rates
```

- [ ] **Step 4: Extend `backend/schemas/check_in.py`**

Append:

```python
class AttestationRateRow(BaseModel):
    location_id: str
    location_name: str
    entries: int
    attested: int
    rate: float  # attested / entries, 0.0 when entries == 0
```

and change `CheckInReportResponse` to:

```python
class CheckInReportResponse(BaseModel):
    rows: list[CheckInReportRow]
    retention_days: int
    attestation: list[AttestationRateRow]
```

(`AttestationRateRow` must be declared above `CheckInReportResponse`.)

- [ ] **Step 5: Populate it in `backend/routers/check_ins.py`**

Add to the schema import: `AttestationRateRow`. Add:

```python
from backend.services.time_entries import attestation_rates
```

Replace the `return CheckInReportResponse(...)` at the end of `get_check_in_report` with:

```python
    # Attestation over the SAME retained window the punctuality rows use.
    # Reporting only — nothing downstream reads `rate` to block anything.
    rates = await attestation_rates(db, company_id, cutoff.date())
    return CheckInReportResponse(
        rows=rows,
        retention_days=settings.RETENTION_CHECKINS_DAYS,
        attestation=[
            AttestationRateRow(
                location_id=r.location_id,
                location_name=r.location_name,
                entries=r.entries,
                attested=r.attested,
                rate=r.rate,
            )
            for r in rates
        ],
    )
```

- [ ] **Step 6: Run the new test plus the existing check-in API tests**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_check_in_report_attestation.py tests/test_check_in_api.py -q`
Expected: PASS — 4 + the existing check-in API tests

- [ ] **Step 7: Run the whole backend suite**

Run: `../../../backend/.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/services/time_entries.py backend/schemas/check_in.py \
        backend/routers/check_ins.py tests/test_check_in_report_attestation.py
git commit -m "feat(payroll): report the attestation rate per location on /check-ins/report (#78)"
```

---

### Task 10: Frontend data layer — types, API wrapper, formatters, and i18n in all 19 locales

**Files:**
- Modify: `frontend/src/types/index.ts` (after the Check-In block)
- Create: `frontend/src/api/payroll.ts`
- Create: `frontend/src/utils/payrollHours.ts`
- Create: `frontend/src/utils/payrollHours.test.ts`
- Modify: all 19 of `frontend/src/i18n/{en,zh,hi,ar,fr,es,pt,bn,ru,ur,id,de,pcm,te,tr,ta,vi,ja,mr}.ts`

**Interfaces:**
- Consumes: the backend response shapes produced in Tasks 4–7 and 9.
- Produces (types, in `frontend/src/types/index.ts`):
  - `TimeEntrySource = "checked_in" | "manager_attested"`
  - `TimeEntryRow`, `PayrollExceptionRow`, `PayrollEntriesResponse`, `PayrollExceptionsResponse`, `PayrollDeriveResult`, `PayrollApproveResult`, `AttestationRateRow`
  - `CheckInReport` gains `attestation: AttestationRateRow[]`
- Produces (in `frontend/src/api/payroll.ts`):
  - `PayrollRange = { rangeStart: string; rangeEnd: string }`
  - `deriveTimeEntries(range: PayrollRange, locationId?: string): Promise<PayrollDeriveResult>`
  - `listTimeEntries(range: PayrollRange, locationId?: string, approved?: boolean): Promise<PayrollEntriesResponse>`
  - `listPayrollExceptions(range: PayrollRange, locationId?: string): Promise<PayrollExceptionsResponse>`
  - `attestShift(shiftId: string, reason?: string): Promise<TimeEntryRow>`
  - `approveTimeEntries(range: PayrollRange, locationId?: string, entryIds?: string[]): Promise<PayrollApproveResult>`
  - `downloadPayrollCsv(range: PayrollRange, locationId?: string, includeExported?: boolean): Promise<{ blob: Blob; filename: string }>`
- Produces (in `frontend/src/utils/payrollHours.ts`):
  - `formatPaidHours(minutes: number): string`
  - `LatenessLabels = { early: string; late: string; onTime: string }`
  - `formatLateness(minutes: number | null, labels: LatenessLabels): string`
- Produces (i18n): `nav.payroll`, the whole `payroll` block, and `checkIn.attestationTitle` / `checkIn.attestationDesc`, in every locale.

**Spec note:** the spec writes `formatLateness(minutes: number | null): string`. It takes a `LatenessLabels` argument here because the three strings it returns are localized into 19 languages; baking English into a util would bypass `TranslationKeys` entirely. The function stays pure and the test below is unchanged in substance.

- [ ] **Step 1: Write the failing formatter test**

Create `frontend/src/utils/payrollHours.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { formatLateness, formatPaidHours } from "./payrollHours";

// The same three strings the payroll block ships, so the test reads like the
// page does.
const LABELS = {
  early: "{minutes} min early",
  late: "{minutes} min late",
  onTime: "On time",
};

describe("formatPaidHours", () => {
  it("renders a full shift as hours to two places", () => {
    expect(formatPaidHours(480)).toBe("8.00");
  });

  it("renders a half hour as .50, not .5", () => {
    expect(formatPaidHours(450)).toBe("7.50");
  });

  it("renders zero without collapsing to an empty string", () => {
    expect(formatPaidHours(0)).toBe("0.00");
  });
});

describe("formatLateness", () => {
  it("renders nothing at all when no arrival was observed", () => {
    // Not "0": an attested entry has no scan, and reporting an on-time
    // arrival nobody saw would put a fact in front of a manager that is not
    // true.
    expect(formatLateness(null, LABELS)).toBe("");
  });

  it("renders on time for exactly zero", () => {
    expect(formatLateness(0, LABELS)).toBe("On time");
  });

  it("renders a negative number through the early branch, unsigned", () => {
    expect(formatLateness(-4, LABELS)).toBe("4 min early");
  });

  it("renders a positive number through the late branch", () => {
    expect(formatLateness(7, LABELS)).toBe("7 min late");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm test -- payrollHours`
Expected: FAIL — `Failed to resolve import "./payrollHours"`

- [ ] **Step 3: Create `frontend/src/utils/payrollHours.ts`**

```ts
/** The two pure formatters the payroll page needs.
 *
 *  Kept out of the component so they can be unit-tested, per the house
 *  pattern (preferenceText.test.ts, shiftTime.test.ts). Neither constructs a
 *  `Date` — nothing here touches a shift timestamp.
 */

/** Minutes as payroll hours, two decimal places: 480 -> "8.00".
 *
 *  Hours rather than minutes because that is what the CSV column and every
 *  payroll import template use, and the two must agree on screen and in the
 *  file. */
export function formatPaidHours(minutes: number): string {
  return (minutes / 60).toFixed(2);
}

export interface LatenessLabels {
  /** Carries a {minutes} placeholder. */
  early: string;
  /** Carries a {minutes} placeholder. */
  late: string;
  onTime: string;
}

/** Signed minutes-from-start as a sentence.
 *
 *  `null` renders as nothing at all: an attested entry has no scan, and
 *  reporting "on time" for an arrival nobody observed would state a fact we
 *  do not have. The labels are passed in because this string is localized
 *  into 19 languages. */
export function formatLateness(
  minutes: number | null,
  labels: LatenessLabels
): string {
  if (minutes === null) return "";
  if (minutes === 0) return labels.onTime;
  if (minutes < 0) return labels.early.replace("{minutes}", String(-minutes));
  return labels.late.replace("{minutes}", String(minutes));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm test -- payrollHours`
Expected: PASS — 7 passed

- [ ] **Step 5: Add the types to `frontend/src/types/index.ts`**

Append immediately after the `CheckInReport` interface, and add `attestation` to `CheckInReport` itself:

```ts
export interface CheckInReport {
  rows: CheckInReportRow[];
  retention_days: number;
  /** Reporting only — nothing on the backend reads `rate` to block anything. */
  attestation: AttestationRateRow[];
}

// ── Payroll (#78) ──

export interface AttestationRateRow {
  location_id: string;
  location_name: string;
  entries: number;
  attested: number;
  /** attested / entries, 0.0 when entries == 0. */
  rate: number;
}

export type TimeEntrySource = "checked_in" | "manager_attested";

export interface TimeEntryRow {
  id: string;
  shift_id: string;
  employee_id: string;
  employee_name: string;
  location_id: string;
  location_name: string;
  role_id: string;
  role_name: string;
  /** "YYYY-MM-DD", the location-local date the shift STARTED. */
  pay_date: string;
  /** Carries the location's offset. Read it with utils/shiftTime.ts — never
   *  `new Date(...)`, which re-projects into the viewer's timezone (#92). */
  start_time: string;
  end_time: string;
  paid_minutes: number;
  source: TimeEntrySource;
  checked_in_at: string | null;
  /** Signed: negative early, positive late. Null for an attested entry. */
  lateness_minutes: number | null;
  attested_by_name: string | null;
  attested_at: string | null;
  attestation_reason: string | null;
  approved_at: string | null;
  exported_at: string | null;
}

export interface PayrollExceptionRow {
  shift_id: string;
  employee_id: string;
  employee_name: string;
  location_id: string;
  location_name: string;
  role_id: string;
  role_name: string;
  pay_date: string;
  start_time: string;
  end_time: string;
  paid_minutes: number;
}

export interface PayrollEntriesResponse {
  rows: TimeEntryRow[];
  total_entries: number;
  total_paid_minutes: number;
  approved_entries: number;
}

export interface PayrollExceptionsResponse {
  rows: PayrollExceptionRow[];
  total: number;
}

export interface PayrollDeriveResult {
  created: number;
  existing: number;
  exception_count: number;
}

export interface PayrollApproveResult {
  approved: number;
  already_approved: number;
}
```

- [ ] **Step 6: Create `frontend/src/api/payroll.ts`**

```ts
import { ApiError, apiFetch } from "./client";
import type {
  PayrollApproveResult,
  PayrollDeriveResult,
  PayrollEntriesResponse,
  PayrollExceptionsResponse,
  TimeEntryRow,
} from "../types";

/** Inclusive, "YYYY-MM-DD" both ends. At most 62 days, which the backend
 *  enforces with a 400 invalid_range. */
export interface PayrollRange {
  rangeStart: string;
  rangeEnd: string;
}

function rangeBody(
  range: PayrollRange,
  locationId?: string
): Record<string, unknown> {
  return {
    range_start: range.rangeStart,
    range_end: range.rangeEnd,
    location_id: locationId ?? null,
  };
}

function rangeQuery(range: PayrollRange, locationId?: string): string {
  const q = new URLSearchParams({
    range_start: range.rangeStart,
    range_end: range.rangeEnd,
  });
  if (locationId) q.set("location_id", locationId);
  return q.toString();
}

/** Idempotent. The page calls this before reading, on every range change. */
export function deriveTimeEntries(
  range: PayrollRange,
  locationId?: string
): Promise<PayrollDeriveResult> {
  return apiFetch<PayrollDeriveResult>("/payroll/entries/derive", {
    method: "POST",
    body: JSON.stringify(rangeBody(range, locationId)),
  });
}

export function listTimeEntries(
  range: PayrollRange,
  locationId?: string,
  approved?: boolean
): Promise<PayrollEntriesResponse> {
  let q = rangeQuery(range, locationId);
  if (approved !== undefined) q += `&approved=${approved}`;
  return apiFetch<PayrollEntriesResponse>(`/payroll/entries?${q}`);
}

export function listPayrollExceptions(
  range: PayrollRange,
  locationId?: string
): Promise<PayrollExceptionsResponse> {
  return apiFetch<PayrollExceptionsResponse>(
    `/payroll/exceptions?${rangeQuery(range, locationId)}`
  );
}

export function attestShift(
  shiftId: string,
  reason?: string
): Promise<TimeEntryRow> {
  return apiFetch<TimeEntryRow>("/payroll/attest", {
    method: "POST",
    body: JSON.stringify({ shift_id: shiftId, reason: reason || null }),
  });
}

export function approveTimeEntries(
  range: PayrollRange,
  locationId?: string,
  entryIds?: string[]
): Promise<PayrollApproveResult> {
  return apiFetch<PayrollApproveResult>("/payroll/approve", {
    method: "POST",
    body: JSON.stringify({
      ...rangeBody(range, locationId),
      entry_ids: entryIds && entryIds.length > 0 ? entryIds : null,
    }),
  });
}

/** The one call that cannot go through apiFetch, which ends in res.json().
 *
 *  Uses fetch directly with the same Authorization: Bearer header, reads
 *  res.blob(), parses the filename out of Content-Disposition, and throws
 *  ApiError on a non-OK status so the page's error handling is unchanged.
 *  This is why export is a POST — it keeps the credential in a header instead
 *  of a query string. */
export async function downloadPayrollCsv(
  range: PayrollRange,
  locationId?: string,
  includeExported = false
): Promise<{ blob: Blob; filename: string }> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch("/api/v1/payroll/export", {
    method: "POST",
    headers,
    body: JSON.stringify({
      ...rangeBody(range, locationId),
      include_exported: includeExported,
    }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message || res.statusText;
    throw new ApiError(res.status, message, detail);
  }

  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  return {
    blob: await res.blob(),
    filename: match ? match[1] : "payroll.csv",
  };
}
```

- [ ] **Step 7: Add the English keys, then let the build name every locale that is missing them**

In `frontend/src/i18n/en.ts`, add to the `nav` block immediately after the `checkInReport:` line:

```ts
    payroll: "Payroll",
```

Add these two keys to the `checkIn` block, after `statusDuplicate`:

```ts
    attestationTitle: "Manager-confirmed shifts",
    attestationDesc: "Share of payable hours confirmed by a manager instead of a check-in, per location, over the last {days} days. High is worth a look, not a penalty.",
```

Add a whole new `payroll` block after the `checkIn` block (before the closing `} as const;`):

```ts
  // ── Payroll (#78) ──
  payroll: {
    title: "Payroll Hours",
    desc: "Approved shifts with a check-in become payable hours. Shifts with no check-in wait below for a manager to confirm them.",
    rangeStart: "From",
    rangeEnd: "To",
    location: "Location",
    allLocations: "All locations",
    exceptionsTitle: "Needs confirmation",
    exceptionsDesc: "These shifts were scheduled and approved, but nobody checked in. Confirm the ones that were worked so they get paid.",
    exceptionsEmpty: "Every approved shift in this range has a check-in.",
    attest: "Confirm worked",
    attestReason: "Reason (optional)",
    attestConfirm: "Confirm",
    attestCancel: "Cancel",
    attestFailed: "Could not confirm that shift. Reload and try again.",
    entriesTitle: "Payable hours",
    entriesEmpty: "No payable hours in this range yet.",
    columnEmployee: "Employee",
    columnDate: "Date",
    columnLocation: "Location",
    columnRole: "Role",
    columnStart: "Start",
    columnEnd: "End",
    columnPaidHours: "Paid hours",
    columnSource: "Source",
    columnCheckedInAt: "Checked in",
    columnLateness: "vs. start",
    sourceCheckedIn: "Checked in",
    sourceAttested: "Confirmed by manager",
    attestedBy: "Confirmed by {name} on {date}",
    latenessEarly: "{minutes} min early",
    latenessLate: "{minutes} min late",
    latenessOnTime: "On time",
    approveSelected: "Approve selected",
    approveAll: "Approve all in range",
    approved: "Approved",
    approvedCount: "{count} entries approved.",
    download: "Download CSV",
    downloadDesc: "Approved hours only. Each entry exports once.",
    nothingToExport: "Nothing new to export. Approve some hours first.",
    alreadyExported: "Exported {date}",
    totalHours: "{hours} hours across {count} shifts",
    paidPlanOnly: "Payroll export is a paid-plan feature. Upgrade to enable it.",
    loadFailed: "Could not load payroll hours. Try again.",
  },
```

- [ ] **Step 8: Run the build and watch it fail, once per missing locale**

Run: `cd frontend && npm run build`
Expected: FAIL — `tsc` reports, for each of the other 18 locale files, `Type '{ ... }' is missing the following properties from type 'TranslationKeys': payroll` (and `payroll` / `attestationTitle` / `attestationDesc` where the nested block is partial). `TranslationKeys` is `typeof import("./en").default`, so the build stays red until all 18 carry every key. **This failing build is the enforcement mechanism — do not silence it by widening the type.**

- [ ] **Step 9: Translate the keys into the other 18 locales**

For each file below, add the nav key after its existing `checkInReport:` line, the two `checkIn` keys after its `statusDuplicate:` line, and the `payroll` block after the `checkIn` block. Every string is translated — English placeholders in the other 18 are exactly what this step exists to prevent. `{name}`, `{date}`, `{minutes}`, `{count}`, `{hours}` and `{days}` are substituted at runtime and must survive verbatim.

**`zh.ts`** — nav: `payroll: "薪资",` · checkIn: `attestationTitle: "经理确认的班次",` `attestationDesc: "过去 {days} 天内，各门店由经理确认而非签到确认的应付工时占比。比例偏高值得关注，但不是处罚。",`

```ts
  payroll: {
    title: "薪资工时",
    desc: "已批准且有签到记录的班次会计入应付工时。没有签到的班次会列在下方，等待经理确认。",
    rangeStart: "起始",
    rangeEnd: "截止",
    location: "门店",
    allLocations: "所有门店",
    exceptionsTitle: "待确认",
    exceptionsDesc: "这些班次已排定并获批准，但无人签到。请确认实际出勤的班次，以便发放工资。",
    exceptionsEmpty: "该时间段内每个已批准的班次都有签到记录。",
    attest: "确认已出勤",
    attestReason: "原因（选填）",
    attestConfirm: "确认",
    attestCancel: "取消",
    attestFailed: "无法确认该班次。请刷新后重试。",
    entriesTitle: "应付工时",
    entriesEmpty: "该时间段内暂无应付工时。",
    columnEmployee: "员工",
    columnDate: "日期",
    columnLocation: "门店",
    columnRole: "岗位",
    columnStart: "开始",
    columnEnd: "结束",
    columnPaidHours: "计薪工时",
    columnSource: "来源",
    columnCheckedInAt: "签到时间",
    columnLateness: "相对开始时间",
    sourceCheckedIn: "已签到",
    sourceAttested: "经理确认",
    attestedBy: "由 {name} 于 {date} 确认",
    latenessEarly: "早到 {minutes} 分钟",
    latenessLate: "迟到 {minutes} 分钟",
    latenessOnTime: "准时",
    approveSelected: "批准所选",
    approveAll: "批准该时间段全部",
    approved: "已批准",
    approvedCount: "已批准 {count} 条记录。",
    download: "下载 CSV",
    downloadDesc: "仅包含已批准的工时。每条记录只导出一次。",
    nothingToExport: "没有新内容可导出。请先批准工时。",
    alreadyExported: "已于 {date} 导出",
    totalHours: "{count} 个班次共 {hours} 小时",
    paidPlanOnly: "薪资导出是付费套餐功能。升级后即可使用。",
    loadFailed: "无法加载薪资工时。请重试。",
  },
```

**`hi.ts`** — nav: `payroll: "पेरोल",` · checkIn: `attestationTitle: "मैनेजर द्वारा पुष्ट शिफ्ट",` `attestationDesc: "पिछले {days} दिनों में, प्रति स्थान ऐसे देय घंटों का हिस्सा जिन्हें चेक-इन के बजाय मैनेजर ने पुष्ट किया। ज़्यादा होना देखने लायक है, दंड का कारण नहीं।",`

```ts
  payroll: {
    title: "पेरोल घंटे",
    desc: "चेक-इन वाली स्वीकृत शिफ्ट देय घंटे बन जाती हैं। बिना चेक-इन वाली शिफ्ट नीचे मैनेजर की पुष्टि का इंतज़ार करती हैं।",
    rangeStart: "से",
    rangeEnd: "तक",
    location: "स्थान",
    allLocations: "सभी स्थान",
    exceptionsTitle: "पुष्टि चाहिए",
    exceptionsDesc: "ये शिफ्ट निर्धारित और स्वीकृत थीं, पर किसी ने चेक-इन नहीं किया। जो वास्तव में काम की गईं उन्हें पुष्ट करें ताकि भुगतान हो सके।",
    exceptionsEmpty: "इस अवधि की हर स्वीकृत शिफ्ट में चेक-इन मौजूद है।",
    attest: "काम की पुष्टि करें",
    attestReason: "कारण (वैकल्पिक)",
    attestConfirm: "पुष्टि करें",
    attestCancel: "रद्द करें",
    attestFailed: "उस शिफ्ट की पुष्टि नहीं हो सकी। पेज दोबारा लोड कर के कोशिश करें।",
    entriesTitle: "देय घंटे",
    entriesEmpty: "इस अवधि में अभी कोई देय घंटा नहीं है।",
    columnEmployee: "कर्मचारी",
    columnDate: "तारीख़",
    columnLocation: "स्थान",
    columnRole: "भूमिका",
    columnStart: "शुरू",
    columnEnd: "समाप्त",
    columnPaidHours: "भुगतान योग्य घंटे",
    columnSource: "स्रोत",
    columnCheckedInAt: "चेक-इन",
    columnLateness: "शुरुआत के सापेक्ष",
    sourceCheckedIn: "चेक-इन किया",
    sourceAttested: "मैनेजर द्वारा पुष्ट",
    attestedBy: "{name} द्वारा {date} को पुष्ट",
    latenessEarly: "{minutes} मिनट पहले",
    latenessLate: "{minutes} मिनट देर से",
    latenessOnTime: "समय पर",
    approveSelected: "चयनित स्वीकृत करें",
    approveAll: "इस अवधि की सभी स्वीकृत करें",
    approved: "स्वीकृत",
    approvedCount: "{count} प्रविष्टियाँ स्वीकृत।",
    download: "CSV डाउनलोड करें",
    downloadDesc: "केवल स्वीकृत घंटे। हर प्रविष्टि एक ही बार निर्यात होती है।",
    nothingToExport: "निर्यात के लिए कुछ नया नहीं। पहले कुछ घंटे स्वीकृत करें।",
    alreadyExported: "{date} को निर्यात किया",
    totalHours: "{count} शिफ्ट में कुल {hours} घंटे",
    paidPlanOnly: "पेरोल निर्यात एक सशुल्क प्लान सुविधा है। सक्षम करने के लिए अपग्रेड करें।",
    loadFailed: "पेरोल घंटे लोड नहीं हो सके। दोबारा कोशिश करें।",
  },
```

**`ar.ts`** — nav: `payroll: "الرواتب",` · checkIn: `attestationTitle: "الورديات المؤكَّدة من المدير",` `attestationDesc: "نسبة الساعات المستحقة التي أكّدها مدير بدلاً من تسجيل حضور، لكل موقع، خلال آخر {days} يومًا. الارتفاع يستحق النظر، لا العقاب.",`

```ts
  payroll: {
    title: "ساعات الرواتب",
    desc: "الورديات المعتمدة التي سُجِّل فيها حضور تصبح ساعات مستحقة. أما الورديات بلا تسجيل حضور فتنتظر أدناه تأكيد المدير.",
    rangeStart: "من",
    rangeEnd: "إلى",
    location: "الموقع",
    allLocations: "جميع المواقع",
    exceptionsTitle: "بحاجة إلى تأكيد",
    exceptionsDesc: "هذه الورديات كانت مجدولة ومعتمدة، لكن لم يسجّل أحد حضوره. أكّد ما تم العمل فيه فعلاً كي يُحتسب في الأجر.",
    exceptionsEmpty: "كل وردية معتمدة في هذه الفترة لها تسجيل حضور.",
    attest: "تأكيد العمل",
    attestReason: "السبب (اختياري)",
    attestConfirm: "تأكيد",
    attestCancel: "إلغاء",
    attestFailed: "تعذّر تأكيد تلك الوردية. أعد تحميل الصفحة وحاول مجددًا.",
    entriesTitle: "الساعات المستحقة",
    entriesEmpty: "لا توجد ساعات مستحقة في هذه الفترة بعد.",
    columnEmployee: "الموظف",
    columnDate: "التاريخ",
    columnLocation: "الموقع",
    columnRole: "الدور",
    columnStart: "البداية",
    columnEnd: "النهاية",
    columnPaidHours: "الساعات المدفوعة",
    columnSource: "المصدر",
    columnCheckedInAt: "وقت الحضور",
    columnLateness: "مقارنةً بالبداية",
    sourceCheckedIn: "سجّل حضوره",
    sourceAttested: "مؤكَّد من المدير",
    attestedBy: "أكّدها {name} في {date}",
    latenessEarly: "مبكرًا بـ {minutes} دقيقة",
    latenessLate: "متأخرًا بـ {minutes} دقيقة",
    latenessOnTime: "في الوقت المحدد",
    approveSelected: "اعتماد المحدد",
    approveAll: "اعتماد كل ما في الفترة",
    approved: "معتمَد",
    approvedCount: "تم اعتماد {count} سجلاً.",
    download: "تنزيل CSV",
    downloadDesc: "الساعات المعتمدة فقط. يُصدَّر كل سجل مرة واحدة.",
    nothingToExport: "لا جديد للتصدير. اعتمد بعض الساعات أولاً.",
    alreadyExported: "صُدِّر في {date}",
    totalHours: "{hours} ساعة عبر {count} وردية",
    paidPlanOnly: "تصدير الرواتب ميزة في الخطة المدفوعة. قم بالترقية لتفعيلها.",
    loadFailed: "تعذّر تحميل ساعات الرواتب. حاول مجددًا.",
  },
```

**`fr.ts`** — nav: `payroll: "Paie",` · checkIn: `attestationTitle: "Services confirmés par un responsable",` `attestationDesc: "Part des heures payables confirmées par un responsable plutôt que par un pointage, par établissement, sur les {days} derniers jours. Un taux élevé mérite un coup d'œil, pas une sanction.",`

```ts
  payroll: {
    title: "Heures de paie",
    desc: "Les services approuvés avec un pointage deviennent des heures payables. Ceux sans pointage attendent ci-dessous la confirmation d'un responsable.",
    rangeStart: "Du",
    rangeEnd: "Au",
    location: "Établissement",
    allLocations: "Tous les établissements",
    exceptionsTitle: "À confirmer",
    exceptionsDesc: "Ces services étaient planifiés et approuvés, mais personne n'a pointé. Confirmez ceux qui ont été travaillés pour qu'ils soient payés.",
    exceptionsEmpty: "Chaque service approuvé de cette période a un pointage.",
    attest: "Confirmer le travail",
    attestReason: "Motif (facultatif)",
    attestConfirm: "Confirmer",
    attestCancel: "Annuler",
    attestFailed: "Impossible de confirmer ce service. Rechargez la page et réessayez.",
    entriesTitle: "Heures payables",
    entriesEmpty: "Aucune heure payable sur cette période pour l'instant.",
    columnEmployee: "Employé",
    columnDate: "Date",
    columnLocation: "Établissement",
    columnRole: "Poste",
    columnStart: "Début",
    columnEnd: "Fin",
    columnPaidHours: "Heures payées",
    columnSource: "Source",
    columnCheckedInAt: "Pointage",
    columnLateness: "par rapport au début",
    sourceCheckedIn: "Pointé",
    sourceAttested: "Confirmé par le responsable",
    attestedBy: "Confirmé par {name} le {date}",
    latenessEarly: "{minutes} min en avance",
    latenessLate: "{minutes} min de retard",
    latenessOnTime: "À l'heure",
    approveSelected: "Approuver la sélection",
    approveAll: "Tout approuver sur la période",
    approved: "Approuvé",
    approvedCount: "{count} entrées approuvées.",
    download: "Télécharger le CSV",
    downloadDesc: "Heures approuvées uniquement. Chaque entrée n'est exportée qu'une fois.",
    nothingToExport: "Rien de nouveau à exporter. Approuvez d'abord des heures.",
    alreadyExported: "Exporté le {date}",
    totalHours: "{hours} heures sur {count} services",
    paidPlanOnly: "L'export de paie est réservé aux forfaits payants. Passez à un forfait supérieur pour l'activer.",
    loadFailed: "Impossible de charger les heures de paie. Réessayez.",
  },
```

**`es.ts`** — nav: `payroll: "Nómina",` · checkIn: `attestationTitle: "Turnos confirmados por un responsable",` `attestationDesc: "Proporción de horas pagables confirmadas por un responsable en lugar de por un fichaje, por local, en los últimos {days} días. Un valor alto merece una mirada, no una sanción.",`

```ts
  payroll: {
    title: "Horas de nómina",
    desc: "Los turnos aprobados con fichaje se convierten en horas pagables. Los turnos sin fichaje esperan abajo a que un responsable los confirme.",
    rangeStart: "Desde",
    rangeEnd: "Hasta",
    location: "Local",
    allLocations: "Todos los locales",
    exceptionsTitle: "Necesitan confirmación",
    exceptionsDesc: "Estos turnos estaban planificados y aprobados, pero nadie fichó. Confirma los que se trabajaron para que se paguen.",
    exceptionsEmpty: "Todos los turnos aprobados de este periodo tienen fichaje.",
    attest: "Confirmar trabajado",
    attestReason: "Motivo (opcional)",
    attestConfirm: "Confirmar",
    attestCancel: "Cancelar",
    attestFailed: "No se pudo confirmar ese turno. Recarga la página e inténtalo de nuevo.",
    entriesTitle: "Horas pagables",
    entriesEmpty: "Aún no hay horas pagables en este periodo.",
    columnEmployee: "Empleado",
    columnDate: "Fecha",
    columnLocation: "Local",
    columnRole: "Puesto",
    columnStart: "Inicio",
    columnEnd: "Fin",
    columnPaidHours: "Horas pagadas",
    columnSource: "Origen",
    columnCheckedInAt: "Fichaje",
    columnLateness: "respecto al inicio",
    sourceCheckedIn: "Fichado",
    sourceAttested: "Confirmado por el responsable",
    attestedBy: "Confirmado por {name} el {date}",
    latenessEarly: "{minutes} min antes",
    latenessLate: "{minutes} min tarde",
    latenessOnTime: "A la hora",
    approveSelected: "Aprobar seleccionados",
    approveAll: "Aprobar todo el periodo",
    approved: "Aprobado",
    approvedCount: "{count} registros aprobados.",
    download: "Descargar CSV",
    downloadDesc: "Solo horas aprobadas. Cada registro se exporta una vez.",
    nothingToExport: "No hay nada nuevo que exportar. Aprueba algunas horas primero.",
    alreadyExported: "Exportado el {date}",
    totalHours: "{hours} horas en {count} turnos",
    paidPlanOnly: "La exportación de nómina es una función de pago. Mejora tu plan para activarla.",
    loadFailed: "No se pudieron cargar las horas de nómina. Inténtalo de nuevo.",
  },
```

**`pt.ts`** — nav: `payroll: "Folha de pagamento",` · checkIn: `attestationTitle: "Turnos confirmados pelo gestor",` `attestationDesc: "Proporção de horas pagáveis confirmadas por um gestor em vez de por check-in, por unidade, nos últimos {days} dias. Um valor alto merece atenção, não punição.",`

```ts
  payroll: {
    title: "Horas da folha",
    desc: "Turnos aprovados com check-in viram horas pagáveis. Turnos sem check-in aguardam abaixo a confirmação de um gestor.",
    rangeStart: "De",
    rangeEnd: "Até",
    location: "Unidade",
    allLocations: "Todas as unidades",
    exceptionsTitle: "Precisam de confirmação",
    exceptionsDesc: "Estes turnos foram escalados e aprovados, mas ninguém fez check-in. Confirme os que foram trabalhados para que sejam pagos.",
    exceptionsEmpty: "Todos os turnos aprovados deste período têm check-in.",
    attest: "Confirmar trabalhado",
    attestReason: "Motivo (opcional)",
    attestConfirm: "Confirmar",
    attestCancel: "Cancelar",
    attestFailed: "Não foi possível confirmar esse turno. Recarregue e tente novamente.",
    entriesTitle: "Horas pagáveis",
    entriesEmpty: "Ainda não há horas pagáveis neste período.",
    columnEmployee: "Colaborador",
    columnDate: "Data",
    columnLocation: "Unidade",
    columnRole: "Função",
    columnStart: "Início",
    columnEnd: "Fim",
    columnPaidHours: "Horas pagas",
    columnSource: "Origem",
    columnCheckedInAt: "Check-in",
    columnLateness: "em relação ao início",
    sourceCheckedIn: "Fez check-in",
    sourceAttested: "Confirmado pelo gestor",
    attestedBy: "Confirmado por {name} em {date}",
    latenessEarly: "{minutes} min adiantado",
    latenessLate: "{minutes} min atrasado",
    latenessOnTime: "No horário",
    approveSelected: "Aprovar selecionados",
    approveAll: "Aprovar tudo no período",
    approved: "Aprovado",
    approvedCount: "{count} registros aprovados.",
    download: "Baixar CSV",
    downloadDesc: "Apenas horas aprovadas. Cada registro é exportado uma única vez.",
    nothingToExport: "Nada novo para exportar. Aprove algumas horas primeiro.",
    alreadyExported: "Exportado em {date}",
    totalHours: "{hours} horas em {count} turnos",
    paidPlanOnly: "A exportação da folha é um recurso de plano pago. Faça upgrade para ativar.",
    loadFailed: "Não foi possível carregar as horas da folha. Tente novamente.",
  },
```

**`bn.ts`** — nav: `payroll: "পেরোল",` · checkIn: `attestationTitle: "ম্যানেজার-নিশ্চিত শিফট",` `attestationDesc: "গত {days} দিনে প্রতি লোকেশনে চেক-ইনের বদলে ম্যানেজার যে দেয় ঘণ্টাগুলো নিশ্চিত করেছেন তার অনুপাত। বেশি হলে দেখা দরকার, শাস্তি নয়।",`

```ts
  payroll: {
    title: "পেরোল ঘণ্টা",
    desc: "চেক-ইনসহ অনুমোদিত শিফট দেয় ঘণ্টা হিসেবে গণ্য হয়। চেক-ইন ছাড়া শিফটগুলো নিচে ম্যানেজারের নিশ্চিতকরণের অপেক্ষায় থাকে।",
    rangeStart: "থেকে",
    rangeEnd: "পর্যন্ত",
    location: "লোকেশন",
    allLocations: "সব লোকেশন",
    exceptionsTitle: "নিশ্চিত করা দরকার",
    exceptionsDesc: "এই শিফটগুলো নির্ধারিত ও অনুমোদিত ছিল, কিন্তু কেউ চেক-ইন করেননি। যেগুলোতে আসলে কাজ হয়েছে সেগুলো নিশ্চিত করুন যাতে বেতন দেওয়া যায়।",
    exceptionsEmpty: "এই সময়সীমার প্রতিটি অনুমোদিত শিফটে চেক-ইন আছে।",
    attest: "কাজ হয়েছে নিশ্চিত করুন",
    attestReason: "কারণ (ঐচ্ছিক)",
    attestConfirm: "নিশ্চিত করুন",
    attestCancel: "বাতিল",
    attestFailed: "ওই শিফট নিশ্চিত করা যায়নি। পেজ রিলোড করে আবার চেষ্টা করুন।",
    entriesTitle: "দেয় ঘণ্টা",
    entriesEmpty: "এই সময়সীমায় এখনো কোনো দেয় ঘণ্টা নেই।",
    columnEmployee: "কর্মী",
    columnDate: "তারিখ",
    columnLocation: "লোকেশন",
    columnRole: "ভূমিকা",
    columnStart: "শুরু",
    columnEnd: "শেষ",
    columnPaidHours: "প্রদেয় ঘণ্টা",
    columnSource: "উৎস",
    columnCheckedInAt: "চেক-ইন",
    columnLateness: "শুরুর তুলনায়",
    sourceCheckedIn: "চেক-ইন করেছেন",
    sourceAttested: "ম্যানেজার নিশ্চিত করেছেন",
    attestedBy: "{date} তারিখে {name} নিশ্চিত করেছেন",
    latenessEarly: "{minutes} মিনিট আগে",
    latenessLate: "{minutes} মিনিট দেরিতে",
    latenessOnTime: "সময়মতো",
    approveSelected: "নির্বাচিতগুলো অনুমোদন করুন",
    approveAll: "এই সময়সীমার সব অনুমোদন করুন",
    approved: "অনুমোদিত",
    approvedCount: "{count}টি এন্ট্রি অনুমোদিত।",
    download: "CSV ডাউনলোড করুন",
    downloadDesc: "শুধু অনুমোদিত ঘণ্টা। প্রতিটি এন্ট্রি একবারই এক্সপোর্ট হয়।",
    nothingToExport: "এক্সপোর্ট করার মতো নতুন কিছু নেই। আগে কিছু ঘণ্টা অনুমোদন করুন।",
    alreadyExported: "{date} তারিখে এক্সপোর্ট হয়েছে",
    totalHours: "{count}টি শিফটে মোট {hours} ঘণ্টা",
    paidPlanOnly: "পেরোল এক্সপোর্ট একটি পেইড প্ল্যান সুবিধা। চালু করতে আপগ্রেড করুন।",
    loadFailed: "পেরোল ঘণ্টা লোড করা যায়নি। আবার চেষ্টা করুন।",
  },
```

**`ru.ts`** — nav: `payroll: "Зарплата",` · checkIn: `attestationTitle: "Смены, подтверждённые руководителем",` `attestationDesc: "Доля оплачиваемых часов, подтверждённых руководителем вместо отметки о приходе, по каждой точке за последние {days} дней. Высокое значение стоит посмотреть, но это не повод для санкций.",`

```ts
  payroll: {
    title: "Часы к оплате",
    desc: "Утверждённые смены с отметкой о приходе становятся оплачиваемыми часами. Смены без отметки ждут ниже подтверждения руководителя.",
    rangeStart: "С",
    rangeEnd: "По",
    location: "Точка",
    allLocations: "Все точки",
    exceptionsTitle: "Нужно подтверждение",
    exceptionsDesc: "Эти смены были запланированы и утверждены, но никто не отметился. Подтвердите отработанные, чтобы они были оплачены.",
    exceptionsEmpty: "У каждой утверждённой смены в этом периоде есть отметка о приходе.",
    attest: "Подтвердить работу",
    attestReason: "Причина (необязательно)",
    attestConfirm: "Подтвердить",
    attestCancel: "Отмена",
    attestFailed: "Не удалось подтвердить эту смену. Обновите страницу и попробуйте снова.",
    entriesTitle: "Оплачиваемые часы",
    entriesEmpty: "В этом периоде пока нет оплачиваемых часов.",
    columnEmployee: "Сотрудник",
    columnDate: "Дата",
    columnLocation: "Точка",
    columnRole: "Роль",
    columnStart: "Начало",
    columnEnd: "Окончание",
    columnPaidHours: "Оплачено часов",
    columnSource: "Источник",
    columnCheckedInAt: "Отметка о приходе",
    columnLateness: "относительно начала",
    sourceCheckedIn: "Отметился",
    sourceAttested: "Подтверждено руководителем",
    attestedBy: "Подтверждено: {name}, {date}",
    latenessEarly: "на {minutes} мин раньше",
    latenessLate: "на {minutes} мин позже",
    latenessOnTime: "Вовремя",
    approveSelected: "Утвердить выбранные",
    approveAll: "Утвердить всё за период",
    approved: "Утверждено",
    approvedCount: "Утверждено записей: {count}.",
    download: "Скачать CSV",
    downloadDesc: "Только утверждённые часы. Каждая запись выгружается один раз.",
    nothingToExport: "Нечего выгружать. Сначала утвердите часы.",
    alreadyExported: "Выгружено {date}",
    totalHours: "{hours} ч за {count} смен",
    paidPlanOnly: "Выгрузка для зарплаты доступна на платном тарифе. Перейдите на него, чтобы включить.",
    loadFailed: "Не удалось загрузить часы к оплате. Попробуйте снова.",
  },
```

**`ur.ts`** — nav: `payroll: "پے رول",` · checkIn: `attestationTitle: "مینیجر کی تصدیق شدہ شفٹس",` `attestationDesc: "پچھلے {days} دنوں میں ہر مقام پر ایسے قابلِ ادائیگی گھنٹوں کا تناسب جن کی تصدیق چیک اِن کے بجائے مینیجر نے کی۔ زیادہ ہونا دیکھنے کی بات ہے، سزا کی نہیں۔",`

```ts
  payroll: {
    title: "پے رول گھنٹے",
    desc: "چیک اِن والی منظور شدہ شفٹس قابلِ ادائیگی گھنٹے بن جاتی ہیں۔ بغیر چیک اِن والی شفٹس نیچے مینیجر کی تصدیق کی منتظر رہتی ہیں۔",
    rangeStart: "از",
    rangeEnd: "تا",
    location: "مقام",
    allLocations: "تمام مقامات",
    exceptionsTitle: "تصدیق درکار",
    exceptionsDesc: "یہ شفٹس شیڈول اور منظور تھیں، مگر کسی نے چیک اِن نہیں کیا۔ جو واقعی کی گئیں اُن کی تصدیق کریں تاکہ ادائیگی ہو سکے۔",
    exceptionsEmpty: "اس مدت کی ہر منظور شدہ شفٹ میں چیک اِن موجود ہے۔",
    attest: "کام کی تصدیق کریں",
    attestReason: "وجہ (اختیاری)",
    attestConfirm: "تصدیق کریں",
    attestCancel: "منسوخ",
    attestFailed: "اس شفٹ کی تصدیق نہ ہو سکی۔ صفحہ دوبارہ لوڈ کر کے کوشش کریں۔",
    entriesTitle: "قابلِ ادائیگی گھنٹے",
    entriesEmpty: "اس مدت میں ابھی کوئی قابلِ ادائیگی گھنٹہ نہیں۔",
    columnEmployee: "ملازم",
    columnDate: "تاریخ",
    columnLocation: "مقام",
    columnRole: "کردار",
    columnStart: "آغاز",
    columnEnd: "اختتام",
    columnPaidHours: "ادا شدہ گھنٹے",
    columnSource: "ماخذ",
    columnCheckedInAt: "چیک اِن",
    columnLateness: "آغاز کے مقابلے میں",
    sourceCheckedIn: "چیک اِن ہوا",
    sourceAttested: "مینیجر نے تصدیق کی",
    attestedBy: "{name} نے {date} کو تصدیق کی",
    latenessEarly: "{minutes} منٹ پہلے",
    latenessLate: "{minutes} منٹ دیر سے",
    latenessOnTime: "وقت پر",
    approveSelected: "منتخب کردہ منظور کریں",
    approveAll: "اس مدت کے سب منظور کریں",
    approved: "منظور شدہ",
    approvedCount: "{count} اندراجات منظور ہوئے۔",
    download: "CSV ڈاؤن لوڈ کریں",
    downloadDesc: "صرف منظور شدہ گھنٹے۔ ہر اندراج ایک ہی بار برآمد ہوتا ہے۔",
    nothingToExport: "برآمد کے لیے کچھ نیا نہیں۔ پہلے کچھ گھنٹے منظور کریں۔",
    alreadyExported: "{date} کو برآمد ہوا",
    totalHours: "{count} شفٹس میں کل {hours} گھنٹے",
    paidPlanOnly: "پے رول برآمد ایک معاوضہ پلان کی سہولت ہے۔ فعال کرنے کے لیے اپ گریڈ کریں۔",
    loadFailed: "پے رول گھنٹے لوڈ نہ ہو سکے۔ دوبارہ کوشش کریں۔",
  },
```

**`id.ts`** — nav: `payroll: "Penggajian",` · checkIn: `attestationTitle: "Shift yang dikonfirmasi manajer",` `attestationDesc: "Porsi jam yang dibayar dan dikonfirmasi oleh manajer alih-alih check-in, per lokasi, selama {days} hari terakhir. Angka tinggi layak ditinjau, bukan dihukum.",`

```ts
  payroll: {
    title: "Jam Penggajian",
    desc: "Shift yang disetujui dan ada check-in menjadi jam yang dibayar. Shift tanpa check-in menunggu di bawah untuk dikonfirmasi manajer.",
    rangeStart: "Dari",
    rangeEnd: "Sampai",
    location: "Lokasi",
    allLocations: "Semua lokasi",
    exceptionsTitle: "Perlu konfirmasi",
    exceptionsDesc: "Shift ini sudah dijadwalkan dan disetujui, tetapi tidak ada yang check-in. Konfirmasi yang benar-benar dikerjakan agar dibayar.",
    exceptionsEmpty: "Setiap shift yang disetujui pada rentang ini punya check-in.",
    attest: "Konfirmasi dikerjakan",
    attestReason: "Alasan (opsional)",
    attestConfirm: "Konfirmasi",
    attestCancel: "Batal",
    attestFailed: "Tidak bisa mengonfirmasi shift itu. Muat ulang lalu coba lagi.",
    entriesTitle: "Jam yang dibayar",
    entriesEmpty: "Belum ada jam yang dibayar pada rentang ini.",
    columnEmployee: "Karyawan",
    columnDate: "Tanggal",
    columnLocation: "Lokasi",
    columnRole: "Peran",
    columnStart: "Mulai",
    columnEnd: "Selesai",
    columnPaidHours: "Jam dibayar",
    columnSource: "Sumber",
    columnCheckedInAt: "Check-in",
    columnLateness: "vs. jam mulai",
    sourceCheckedIn: "Check-in",
    sourceAttested: "Dikonfirmasi manajer",
    attestedBy: "Dikonfirmasi oleh {name} pada {date}",
    latenessEarly: "{minutes} mnt lebih awal",
    latenessLate: "{minutes} mnt terlambat",
    latenessOnTime: "Tepat waktu",
    approveSelected: "Setujui yang dipilih",
    approveAll: "Setujui semua pada rentang ini",
    approved: "Disetujui",
    approvedCount: "{count} entri disetujui.",
    download: "Unduh CSV",
    downloadDesc: "Hanya jam yang disetujui. Setiap entri diekspor sekali.",
    nothingToExport: "Tidak ada yang baru untuk diekspor. Setujui dulu sebagian jam.",
    alreadyExported: "Diekspor {date}",
    totalHours: "{hours} jam dari {count} shift",
    paidPlanOnly: "Ekspor penggajian adalah fitur paket berbayar. Tingkatkan untuk mengaktifkannya.",
    loadFailed: "Tidak bisa memuat jam penggajian. Coba lagi.",
  },
```

**`de.ts`** — nav: `payroll: "Lohnabrechnung",` · checkIn: `attestationTitle: "Von Führungskraft bestätigte Schichten",` `attestationDesc: "Anteil der abrechenbaren Stunden, die statt durch ein Einchecken von einer Führungskraft bestätigt wurden, je Standort über die letzten {days} Tage. Ein hoher Wert lohnt einen Blick, keine Sanktion.",`

```ts
  payroll: {
    title: "Abrechnungsstunden",
    desc: "Genehmigte Schichten mit Einchecken werden zu abrechenbaren Stunden. Schichten ohne Einchecken warten unten auf die Bestätigung einer Führungskraft.",
    rangeStart: "Von",
    rangeEnd: "Bis",
    location: "Standort",
    allLocations: "Alle Standorte",
    exceptionsTitle: "Bestätigung nötig",
    exceptionsDesc: "Diese Schichten waren geplant und genehmigt, aber niemand hat eingecheckt. Bestätigen Sie die tatsächlich gearbeiteten, damit sie bezahlt werden.",
    exceptionsEmpty: "Jede genehmigte Schicht in diesem Zeitraum hat ein Einchecken.",
    attest: "Als gearbeitet bestätigen",
    attestReason: "Grund (optional)",
    attestConfirm: "Bestätigen",
    attestCancel: "Abbrechen",
    attestFailed: "Diese Schicht konnte nicht bestätigt werden. Neu laden und erneut versuchen.",
    entriesTitle: "Abrechenbare Stunden",
    entriesEmpty: "In diesem Zeitraum gibt es noch keine abrechenbaren Stunden.",
    columnEmployee: "Mitarbeitende",
    columnDate: "Datum",
    columnLocation: "Standort",
    columnRole: "Rolle",
    columnStart: "Beginn",
    columnEnd: "Ende",
    columnPaidHours: "Bezahlte Stunden",
    columnSource: "Quelle",
    columnCheckedInAt: "Eingecheckt",
    columnLateness: "ggü. Beginn",
    sourceCheckedIn: "Eingecheckt",
    sourceAttested: "Von Führungskraft bestätigt",
    attestedBy: "Bestätigt von {name} am {date}",
    latenessEarly: "{minutes} Min. zu früh",
    latenessLate: "{minutes} Min. zu spät",
    latenessOnTime: "Pünktlich",
    approveSelected: "Auswahl genehmigen",
    approveAll: "Alle im Zeitraum genehmigen",
    approved: "Genehmigt",
    approvedCount: "{count} Einträge genehmigt.",
    download: "CSV herunterladen",
    downloadDesc: "Nur genehmigte Stunden. Jeder Eintrag wird einmal exportiert.",
    nothingToExport: "Nichts Neues zu exportieren. Genehmigen Sie zuerst Stunden.",
    alreadyExported: "Exportiert am {date}",
    totalHours: "{hours} Stunden in {count} Schichten",
    paidPlanOnly: "Der Lohnexport ist eine Funktion kostenpflichtiger Tarife. Upgraden, um ihn zu aktivieren.",
    loadFailed: "Abrechnungsstunden konnten nicht geladen werden. Erneut versuchen.",
  },
```

**`pcm.ts`** — nav: `payroll: "Pay Matter",` · checkIn: `attestationTitle: "Shift wey manager confirm",` `attestationDesc: "How plenty of de hours wey dem go pay na manager confirm am instead of check-in, for each location, for de last {days} days. If e high, e worth look, no be punishment.",`

```ts
  payroll: {
    title: "Hours For Pay",
    desc: "Shift wey dem approve and person check in go turn hours wey dem go pay. Shift wey nobody check in dey wait for down here make manager confirm am.",
    rangeStart: "From",
    rangeEnd: "Go reach",
    location: "Location",
    allLocations: "All de locations",
    exceptionsTitle: "Need confirmation",
    exceptionsDesc: "Dem plan and approve dis shift dem, but nobody check in. Confirm de ones wey dem really work so dem go collect pay.",
    exceptionsEmpty: "Every approved shift for dis period get check-in.",
    attest: "Confirm say dem work",
    attestReason: "Reason (if you get)",
    attestConfirm: "Confirm",
    attestCancel: "Cancel",
    attestFailed: "We no fit confirm dat shift. Reload de page make you try again.",
    entriesTitle: "Hours wey dem go pay",
    entriesEmpty: "No hours wey dem go pay for dis period yet.",
    columnEmployee: "Worker",
    columnDate: "Date",
    columnLocation: "Location",
    columnRole: "Work",
    columnStart: "Start",
    columnEnd: "Finish",
    columnPaidHours: "Hours wey dem pay",
    columnSource: "Where e come from",
    columnCheckedInAt: "Check-in time",
    columnLateness: "against start time",
    sourceCheckedIn: "Dem check in",
    sourceAttested: "Manager confirm am",
    attestedBy: "{name} confirm am for {date}",
    latenessEarly: "{minutes} minutes early",
    latenessLate: "{minutes} minutes late",
    latenessOnTime: "On time",
    approveSelected: "Approve wetin you select",
    approveAll: "Approve everything for dis period",
    approved: "Approved",
    approvedCount: "Dem approve {count} entries.",
    download: "Download CSV",
    downloadDesc: "Na only approved hours. Each entry dey export one time.",
    nothingToExport: "Nothing new to export. Approve some hours first.",
    alreadyExported: "Dem export am {date}",
    totalHours: "{hours} hours across {count} shifts",
    paidPlanOnly: "Pay export na feature for paid plan. Upgrade make e work.",
    loadFailed: "We no fit load de pay hours. Try again.",
  },
```

**`te.ts`** — nav: `payroll: "పేరోల్",` · checkIn: `attestationTitle: "మేనేజర్ ధృవీకరించిన షిఫ్టులు",` `attestationDesc: "గత {days} రోజుల్లో ప్రతి లొకేషన్‌లో చెక్-ఇన్‌కు బదులు మేనేజర్ ధృవీకరించిన చెల్లింపు గంటల వాటా. ఎక్కువైతే చూడాల్సిన విషయం, శిక్ష కాదు.",`

```ts
  payroll: {
    title: "పేరోల్ గంటలు",
    desc: "చెక్-ఇన్ ఉన్న ఆమోదిత షిఫ్టులు చెల్లింపు గంటలుగా మారతాయి. చెక్-ఇన్ లేని షిఫ్టులు మేనేజర్ ధృవీకరణ కోసం కింద వేచి ఉంటాయి.",
    rangeStart: "నుంచి",
    rangeEnd: "వరకు",
    location: "లొకేషన్",
    allLocations: "అన్ని లొకేషన్లు",
    exceptionsTitle: "ధృవీకరణ కావాలి",
    exceptionsDesc: "ఈ షిఫ్టులు షెడ్యూల్ చేసి ఆమోదించినవే, కానీ ఎవరూ చెక్-ఇన్ చేయలేదు. నిజంగా పని చేసినవాటిని ధృవీకరించండి, అప్పుడే చెల్లింపు జరుగుతుంది.",
    exceptionsEmpty: "ఈ వ్యవధిలోని ప్రతి ఆమోదిత షిఫ్టుకూ చెక్-ఇన్ ఉంది.",
    attest: "పని చేశారని ధృవీకరించండి",
    attestReason: "కారణం (ఐచ్ఛికం)",
    attestConfirm: "ధృవీకరించు",
    attestCancel: "రద్దు",
    attestFailed: "ఆ షిఫ్టును ధృవీకరించలేకపోయాం. పేజీ రీలోడ్ చేసి మళ్లీ ప్రయత్నించండి.",
    entriesTitle: "చెల్లింపు గంటలు",
    entriesEmpty: "ఈ వ్యవధిలో ఇంకా చెల్లింపు గంటలు లేవు.",
    columnEmployee: "ఉద్యోగి",
    columnDate: "తేదీ",
    columnLocation: "లొకేషన్",
    columnRole: "పాత్ర",
    columnStart: "ప్రారంభం",
    columnEnd: "ముగింపు",
    columnPaidHours: "చెల్లించిన గంటలు",
    columnSource: "మూలం",
    columnCheckedInAt: "చెక్-ఇన్",
    columnLateness: "ప్రారంభంతో పోలిస్తే",
    sourceCheckedIn: "చెక్-ఇన్ అయ్యారు",
    sourceAttested: "మేనేజర్ ధృవీకరించారు",
    attestedBy: "{date}న {name} ధృవీకరించారు",
    latenessEarly: "{minutes} నిమిషాలు ముందు",
    latenessLate: "{minutes} నిమిషాలు ఆలస్యం",
    latenessOnTime: "సమయానికి",
    approveSelected: "ఎంచుకున్నవి ఆమోదించు",
    approveAll: "ఈ వ్యవధిలోని అన్నీ ఆమోదించు",
    approved: "ఆమోదించారు",
    approvedCount: "{count} ఎంట్రీలు ఆమోదించబడ్డాయి.",
    download: "CSV డౌన్‌లోడ్ చేయి",
    downloadDesc: "ఆమోదిత గంటలు మాత్రమే. ప్రతి ఎంట్రీ ఒక్కసారే ఎగుమతి అవుతుంది.",
    nothingToExport: "ఎగుమతి చేయడానికి కొత్తది ఏమీ లేదు. ముందుగా కొన్ని గంటలు ఆమోదించండి.",
    alreadyExported: "{date}న ఎగుమతి అయ్యింది",
    totalHours: "{count} షిఫ్టుల్లో మొత్తం {hours} గంటలు",
    paidPlanOnly: "పేరోల్ ఎగుమతి చెల్లింపు ప్లాన్ ఫీచర్. ప్రారంభించడానికి అప్‌గ్రేడ్ చేయండి.",
    loadFailed: "పేరోల్ గంటలు లోడ్ కాలేదు. మళ్లీ ప్రయత్నించండి.",
  },
```

**`tr.ts`** — nav: `payroll: "Bordro",` · checkIn: `attestationTitle: "Yönetici onaylı vardiyalar",` `attestationDesc: "Son {days} günde, giriş yerine yönetici tarafından onaylanan ödenebilir saatlerin şubeye göre oranı. Yüksek olması bakmaya değer, ceza gerekçesi değil.",`

```ts
  payroll: {
    title: "Bordro Saatleri",
    desc: "Girişi yapılmış onaylı vardiyalar ödenebilir saate dönüşür. Girişi olmayan vardiyalar aşağıda bir yöneticinin onayını bekler.",
    rangeStart: "Başlangıç",
    rangeEnd: "Bitiş",
    location: "Şube",
    allLocations: "Tüm şubeler",
    exceptionsTitle: "Onay bekliyor",
    exceptionsDesc: "Bu vardiyalar planlandı ve onaylandı, ancak kimse giriş yapmadı. Gerçekten çalışılanları onaylayın ki ödensin.",
    exceptionsEmpty: "Bu aralıktaki her onaylı vardiyanın girişi var.",
    attest: "Çalışıldı olarak onayla",
    attestReason: "Gerekçe (isteğe bağlı)",
    attestConfirm: "Onayla",
    attestCancel: "Vazgeç",
    attestFailed: "Bu vardiya onaylanamadı. Sayfayı yenileyip tekrar deneyin.",
    entriesTitle: "Ödenebilir saatler",
    entriesEmpty: "Bu aralıkta henüz ödenebilir saat yok.",
    columnEmployee: "Çalışan",
    columnDate: "Tarih",
    columnLocation: "Şube",
    columnRole: "Görev",
    columnStart: "Başlangıç",
    columnEnd: "Bitiş",
    columnPaidHours: "Ödenen saat",
    columnSource: "Kaynak",
    columnCheckedInAt: "Giriş",
    columnLateness: "başlangıca göre",
    sourceCheckedIn: "Giriş yaptı",
    sourceAttested: "Yönetici onayladı",
    attestedBy: "{date} tarihinde {name} onayladı",
    latenessEarly: "{minutes} dk erken",
    latenessLate: "{minutes} dk geç",
    latenessOnTime: "Zamanında",
    approveSelected: "Seçilenleri onayla",
    approveAll: "Aralıktaki tümünü onayla",
    approved: "Onaylandı",
    approvedCount: "{count} kayıt onaylandı.",
    download: "CSV indir",
    downloadDesc: "Yalnızca onaylı saatler. Her kayıt bir kez dışa aktarılır.",
    nothingToExport: "Dışa aktarılacak yeni bir şey yok. Önce birkaç saat onaylayın.",
    alreadyExported: "{date} tarihinde dışa aktarıldı",
    totalHours: "{count} vardiyada toplam {hours} saat",
    paidPlanOnly: "Bordro dışa aktarma ücretli plan özelliğidir. Etkinleştirmek için yükseltin.",
    loadFailed: "Bordro saatleri yüklenemedi. Tekrar deneyin.",
  },
```

**`ta.ts`** — nav: `payroll: "ஊதியப் பட்டியல்",` · checkIn: `attestationTitle: "மேலாளர் உறுதிப்படுத்திய ஷிஃப்ட்கள்",` `attestationDesc: "கடந்த {days} நாட்களில், ஒவ்வொரு இடத்திலும் செக்-இன் இல்லாமல் மேலாளர் உறுதிப்படுத்திய ஊதியத்திற்குரிய மணிநேரங்களின் விகிதம். அதிகமாக இருந்தால் கவனிக்க வேண்டியது, தண்டனைக்கு அல்ல.",`

```ts
  payroll: {
    title: "ஊதிய மணிநேரங்கள்",
    desc: "செக்-இன் உள்ள அங்கீகரிக்கப்பட்ட ஷிஃப்ட்கள் ஊதியத்திற்குரிய மணிநேரங்களாகும். செக்-இன் இல்லாத ஷிஃப்ட்கள் கீழே மேலாளர் உறுதிப்படுத்த காத்திருக்கும்.",
    rangeStart: "முதல்",
    rangeEnd: "வரை",
    location: "இடம்",
    allLocations: "அனைத்து இடங்களும்",
    exceptionsTitle: "உறுதிப்படுத்தல் தேவை",
    exceptionsDesc: "இந்த ஷிஃப்ட்கள் திட்டமிடப்பட்டு அங்கீகரிக்கப்பட்டவை, ஆனால் யாரும் செக்-இன் செய்யவில்லை. உண்மையில் பணி செய்யப்பட்டவற்றை உறுதிப்படுத்தினால் ஊதியம் வழங்கப்படும்.",
    exceptionsEmpty: "இந்தக் காலத்தின் ஒவ்வொரு அங்கீகரிக்கப்பட்ட ஷிஃப்டிலும் செக்-இன் உள்ளது.",
    attest: "பணி செய்ததை உறுதிப்படுத்து",
    attestReason: "காரணம் (விருப்பத்தேர்வு)",
    attestConfirm: "உறுதிப்படுத்து",
    attestCancel: "ரத்து",
    attestFailed: "அந்த ஷிஃப்டை உறுதிப்படுத்த முடியவில்லை. பக்கத்தை மீண்டும் ஏற்றி முயற்சிக்கவும்.",
    entriesTitle: "ஊதியத்திற்குரிய மணிநேரங்கள்",
    entriesEmpty: "இந்தக் காலத்தில் இன்னும் ஊதியத்திற்குரிய மணிநேரங்கள் இல்லை.",
    columnEmployee: "பணியாளர்",
    columnDate: "தேதி",
    columnLocation: "இடம்",
    columnRole: "பணி",
    columnStart: "தொடக்கம்",
    columnEnd: "முடிவு",
    columnPaidHours: "ஊதிய மணிநேரம்",
    columnSource: "மூலம்",
    columnCheckedInAt: "செக்-இன்",
    columnLateness: "தொடக்கத்துடன் ஒப்பிடும்போது",
    sourceCheckedIn: "செக்-இன் செய்தார்",
    sourceAttested: "மேலாளர் உறுதிப்படுத்தினார்",
    attestedBy: "{date} அன்று {name} உறுதிப்படுத்தினார்",
    latenessEarly: "{minutes} நிமிடம் முன்னதாக",
    latenessLate: "{minutes} நிமிடம் தாமதம்",
    latenessOnTime: "சரியான நேரத்தில்",
    approveSelected: "தேர்ந்தெடுத்தவற்றை அங்கீகரி",
    approveAll: "இந்தக் காலத்தின் அனைத்தையும் அங்கீகரி",
    approved: "அங்கீகரிக்கப்பட்டது",
    approvedCount: "{count} பதிவுகள் அங்கீகரிக்கப்பட்டன.",
    download: "CSV பதிவிறக்கு",
    downloadDesc: "அங்கீகரிக்கப்பட்ட மணிநேரங்கள் மட்டும். ஒவ்வொரு பதிவும் ஒருமுறை மட்டுமே ஏற்றுமதியாகும்.",
    nothingToExport: "ஏற்றுமதி செய்ய புதிதாக எதுவும் இல்லை. முதலில் சில மணிநேரங்களை அங்கீகரிக்கவும்.",
    alreadyExported: "{date} அன்று ஏற்றுமதி செய்யப்பட்டது",
    totalHours: "{count} ஷிஃப்ட்களில் மொத்தம் {hours} மணிநேரம்",
    paidPlanOnly: "ஊதிய ஏற்றுமதி கட்டணத் திட்ட வசதி. இயக்க மேம்படுத்தவும்.",
    loadFailed: "ஊதிய மணிநேரங்களை ஏற்ற முடியவில்லை. மீண்டும் முயற்சிக்கவும்.",
  },
```

**`vi.ts`** — nav: `payroll: "Bảng lương",` · checkIn: `attestationTitle: "Ca được quản lý xác nhận",` `attestationDesc: "Tỷ lệ giờ được trả công do quản lý xác nhận thay vì check-in, theo từng địa điểm, trong {days} ngày qua. Tỷ lệ cao đáng để xem lại, không phải để phạt.",`

```ts
  payroll: {
    title: "Giờ công tính lương",
    desc: "Ca đã duyệt và có check-in sẽ trở thành giờ được trả công. Ca không có check-in chờ quản lý xác nhận ở bên dưới.",
    rangeStart: "Từ",
    rangeEnd: "Đến",
    location: "Địa điểm",
    allLocations: "Tất cả địa điểm",
    exceptionsTitle: "Cần xác nhận",
    exceptionsDesc: "Những ca này đã được xếp lịch và duyệt, nhưng không ai check-in. Hãy xác nhận những ca thực sự đã làm để được trả công.",
    exceptionsEmpty: "Mọi ca đã duyệt trong khoảng này đều có check-in.",
    attest: "Xác nhận đã làm",
    attestReason: "Lý do (tùy chọn)",
    attestConfirm: "Xác nhận",
    attestCancel: "Hủy",
    attestFailed: "Không thể xác nhận ca đó. Hãy tải lại trang và thử lại.",
    entriesTitle: "Giờ được trả công",
    entriesEmpty: "Chưa có giờ được trả công trong khoảng này.",
    columnEmployee: "Nhân viên",
    columnDate: "Ngày",
    columnLocation: "Địa điểm",
    columnRole: "Vị trí",
    columnStart: "Bắt đầu",
    columnEnd: "Kết thúc",
    columnPaidHours: "Giờ được trả",
    columnSource: "Nguồn",
    columnCheckedInAt: "Check-in",
    columnLateness: "so với giờ bắt đầu",
    sourceCheckedIn: "Đã check-in",
    sourceAttested: "Quản lý xác nhận",
    attestedBy: "{name} xác nhận ngày {date}",
    latenessEarly: "sớm {minutes} phút",
    latenessLate: "trễ {minutes} phút",
    latenessOnTime: "Đúng giờ",
    approveSelected: "Duyệt mục đã chọn",
    approveAll: "Duyệt tất cả trong khoảng",
    approved: "Đã duyệt",
    approvedCount: "Đã duyệt {count} bản ghi.",
    download: "Tải CSV",
    downloadDesc: "Chỉ giờ đã duyệt. Mỗi bản ghi chỉ xuất một lần.",
    nothingToExport: "Không có gì mới để xuất. Hãy duyệt một số giờ trước.",
    alreadyExported: "Đã xuất ngày {date}",
    totalHours: "{hours} giờ trong {count} ca",
    paidPlanOnly: "Xuất bảng lương là tính năng của gói trả phí. Nâng cấp để bật.",
    loadFailed: "Không tải được giờ công tính lương. Vui lòng thử lại.",
  },
```

**`ja.ts`** — nav: `payroll: "給与",` · checkIn: `attestationTitle: "管理者が確認したシフト",` `attestationDesc: "過去{days}日間に、チェックインではなく管理者の確認で支払対象となった時間の割合を店舗別に表示します。高い場合は確認に値しますが、罰則ではありません。",`

```ts
  payroll: {
    title: "給与対象時間",
    desc: "チェックインのある承認済みシフトが支払対象時間になります。チェックインのないシフトは下で管理者の確認を待ちます。",
    rangeStart: "開始日",
    rangeEnd: "終了日",
    location: "店舗",
    allLocations: "すべての店舗",
    exceptionsTitle: "確認が必要",
    exceptionsDesc: "これらのシフトは予定され承認されていますが、誰もチェックインしていません。実際に勤務したものを確認すると支払対象になります。",
    exceptionsEmpty: "この期間の承認済みシフトにはすべてチェックインがあります。",
    attest: "勤務を確認する",
    attestReason: "理由（任意）",
    attestConfirm: "確認",
    attestCancel: "キャンセル",
    attestFailed: "そのシフトを確認できませんでした。再読み込みしてやり直してください。",
    entriesTitle: "支払対象時間",
    entriesEmpty: "この期間にはまだ支払対象時間がありません。",
    columnEmployee: "従業員",
    columnDate: "日付",
    columnLocation: "店舗",
    columnRole: "役割",
    columnStart: "開始",
    columnEnd: "終了",
    columnPaidHours: "支払時間",
    columnSource: "根拠",
    columnCheckedInAt: "チェックイン",
    columnLateness: "開始時刻との差",
    sourceCheckedIn: "チェックイン済み",
    sourceAttested: "管理者が確認",
    attestedBy: "{date}に{name}が確認",
    latenessEarly: "{minutes}分早い",
    latenessLate: "{minutes}分遅い",
    latenessOnTime: "時間どおり",
    approveSelected: "選択分を承認",
    approveAll: "期間内すべてを承認",
    approved: "承認済み",
    approvedCount: "{count}件を承認しました。",
    download: "CSVをダウンロード",
    downloadDesc: "承認済みの時間のみ。各レコードの書き出しは一度だけです。",
    nothingToExport: "書き出す新しいデータがありません。先に時間を承認してください。",
    alreadyExported: "{date}に書き出し済み",
    totalHours: "{count}件のシフトで合計{hours}時間",
    paidPlanOnly: "給与の書き出しは有料プランの機能です。アップグレードすると使えます。",
    loadFailed: "給与対象時間を読み込めませんでした。やり直してください。",
  },
```

**`mr.ts`** — nav: `payroll: "पेरोल",` · checkIn: `attestationTitle: "व्यवस्थापकाने निश्चित केलेल्या शिफ्ट",` `attestationDesc: "गेल्या {days} दिवसांत प्रत्येक ठिकाणी चेक-इनऐवजी व्यवस्थापकाने निश्चित केलेल्या देय तासांचे प्रमाण. जास्त असल्यास पाहण्यासारखे आहे, शिक्षेसाठी नाही.",`

```ts
  payroll: {
    title: "पेरोल तास",
    desc: "चेक-इन असलेल्या मंजूर शिफ्ट देय तास बनतात. चेक-इन नसलेल्या शिफ्ट खाली व्यवस्थापकाच्या निश्चितीची वाट पाहतात.",
    rangeStart: "पासून",
    rangeEnd: "पर्यंत",
    location: "ठिकाण",
    allLocations: "सर्व ठिकाणे",
    exceptionsTitle: "निश्चिती हवी",
    exceptionsDesc: "या शिफ्ट नियोजित आणि मंजूर होत्या, पण कोणीही चेक-इन केले नाही. प्रत्यक्ष काम झालेल्या निश्चित करा म्हणजे मोबदला मिळेल.",
    exceptionsEmpty: "या कालावधीतील प्रत्येक मंजूर शिफ्टला चेक-इन आहे.",
    attest: "काम झाल्याचे निश्चित करा",
    attestReason: "कारण (ऐच्छिक)",
    attestConfirm: "निश्चित करा",
    attestCancel: "रद्द",
    attestFailed: "ती शिफ्ट निश्चित करता आली नाही. पान पुन्हा लोड करून प्रयत्न करा.",
    entriesTitle: "देय तास",
    entriesEmpty: "या कालावधीत अजून देय तास नाहीत.",
    columnEmployee: "कर्मचारी",
    columnDate: "दिनांक",
    columnLocation: "ठिकाण",
    columnRole: "भूमिका",
    columnStart: "सुरुवात",
    columnEnd: "समाप्ती",
    columnPaidHours: "देय तास",
    columnSource: "स्रोत",
    columnCheckedInAt: "चेक-इन",
    columnLateness: "सुरुवातीच्या तुलनेत",
    sourceCheckedIn: "चेक-इन केले",
    sourceAttested: "व्यवस्थापकाने निश्चित केले",
    attestedBy: "{date} रोजी {name} यांनी निश्चित केले",
    latenessEarly: "{minutes} मिनिटे आधी",
    latenessLate: "{minutes} मिनिटे उशिरा",
    latenessOnTime: "वेळेवर",
    approveSelected: "निवडलेले मंजूर करा",
    approveAll: "या कालावधीतील सर्व मंजूर करा",
    approved: "मंजूर",
    approvedCount: "{count} नोंदी मंजूर झाल्या.",
    download: "CSV डाउनलोड करा",
    downloadDesc: "फक्त मंजूर तास. प्रत्येक नोंद एकदाच निर्यात होते.",
    nothingToExport: "निर्यात करण्यासारखे नवीन काही नाही. आधी काही तास मंजूर करा.",
    alreadyExported: "{date} रोजी निर्यात केले",
    totalHours: "{count} शिफ्टमध्ये एकूण {hours} तास",
    paidPlanOnly: "पेरोल निर्यात ही सशुल्क योजनेची सुविधा आहे. सुरू करण्यासाठी अपग्रेड करा.",
    loadFailed: "पेरोल तास लोड करता आले नाहीत. पुन्हा प्रयत्न करा.",
  },
```

- [ ] **Step 10: Run the build again — it must now be green**

Run: `cd frontend && npm run build`
Expected: PASS — `tsc` reports no errors and vite writes `dist/`. If any locale is still named in an error, that file is missing a key from the list above; add it rather than relaxing the type.

- [ ] **Step 11: Verify every locale really carries the block (not just that tsc is quiet)**

`tsc` is the real gate, but it only proves the keys exist — this proves they exist in every file by name, so a copy-paste that skipped one is visible.

Run: `cd frontend && for f in src/i18n/*.ts; do case "$f" in *types.ts) continue;; esac; printf "%s " "$f"; grep -c -e "latenessOnTime" -e "attestationTitle" -e "paidPlanOnly" "$f"; done`
Expected: all 19 locale files listed, each printing `3`. A file printing fewer is missing one of those keys.

- [ ] **Step 12: Run the frontend tests**

Run: `cd frontend && npm test`
Expected: PASS — including `logicalDirection.test.ts` and the new `payrollHours.test.ts`

- [ ] **Step 13: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/payroll.ts \
        frontend/src/utils/payrollHours.ts frontend/src/utils/payrollHours.test.ts \
        frontend/src/i18n/
git commit -m "feat(payroll): frontend types, API wrapper, formatters and i18n in all 19 locales (#78)"
```

---

### Task 11: The manager payroll page

**Files:**
- Create: `frontend/src/pages/manager/Payroll.tsx`
- Modify: `frontend/src/pages/manager/CheckInReport.tsx` (the attestation panel)
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Test: `tests/test_sidebar_routes.py` (existing; it must stay green and now covers the new link)

**Interfaces:**
- Consumes: `deriveTimeEntries`, `listTimeEntries`, `listPayrollExceptions`, `attestShift`, `approveTimeEntries`, `downloadPayrollCsv`, `PayrollRange` (Task 10); `formatPaidHours`, `formatLateness`, `LatenessLabels` (Task 10); `TimeEntryRow`, `PayrollExceptionRow` (Task 10); `formatTime` from `../../utils/shiftTime`; `listLocations` from `../../api/locations`; `ApiError` from `../../api/client`.
- Produces: the route `/manager/payroll` and the sidebar entry `{ to: "/manager/payroll", labelKey: "payroll" }` in `groupCheckIn`.

- [ ] **Step 1: Add the sidebar entry and the route, and watch the existing guard test stay green**

In `frontend/src/components/layout/Sidebar.tsx`, inside the `groupCheckIn` children array, after the `checkInReport` entry:

```ts
      { to: "/manager/payroll", labelKey: "payroll" },
```

In `frontend/src/App.tsx`, add the import beside the other manager pages:

```ts
import Payroll from "./pages/manager/Payroll";
```

and the route immediately after the `check-in-report` route:

```tsx
            <Route path="payroll" element={<Payroll />} />
```

- [ ] **Step 2: Run the guard test to verify it fails before the page exists**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -q`
Expected: PASS — the parser reads strings, so it is already satisfied. (It is the tripwire for a typo in either string; run it again after step 4 too.)

Run: `cd frontend && npm run build`
Expected: FAIL — `Cannot find module './pages/manager/Payroll'`

- [ ] **Step 3: Create `frontend/src/pages/manager/Payroll.tsx`**

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import {
  approveTimeEntries,
  attestShift,
  deriveTimeEntries,
  downloadPayrollCsv,
  listPayrollExceptions,
  listTimeEntries,
  type PayrollRange,
} from "../../api/payroll";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { Location, PayrollExceptionRow, TimeEntryRow } from "../../types";
import { formatLateness, formatPaidHours } from "../../utils/payrollHours";
import { formatTime } from "../../utils/shiftTime";

/** "YYYY-MM-DD" from a browser-local Date, without going through toISOString
 *  (which converts to UTC and can hand back yesterday). */
function isoDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** The Monday-Sunday week that ended most recently.
 *
 *  The browser's local date is the right default HERE precisely because a
 *  manager picking "last week" means their own week. The backend never infers
 *  a range, so the "today is UTC" rule — which governs Python application code
 *  and tests — is not in tension with this. */
function lastCompleteWeek(): PayrollRange {
  const now = new Date();
  // getDay(): 0 = Sunday. Days since the most recent Monday.
  const sinceMonday = (now.getDay() + 6) % 7;
  const thisMonday = new Date(now);
  thisMonday.setDate(now.getDate() - sinceMonday);
  const lastMonday = new Date(thisMonday);
  lastMonday.setDate(thisMonday.getDate() - 7);
  const lastSunday = new Date(lastMonday);
  lastSunday.setDate(lastMonday.getDate() + 6);
  return { rangeStart: isoDate(lastMonday), rangeEnd: isoDate(lastSunday) };
}

export default function Payroll() {
  const { t } = useLanguage();
  const [range, setRange] = useState<PayrollRange>(lastCompleteWeek);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationId, setLocationId] = useState("");
  const [entries, setEntries] = useState<TimeEntryRow[]>([]);
  const [exceptions, setExceptions] = useState<PayrollExceptionRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [attesting, setAttesting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paidPlanOnly, setPaidPlanOnly] = useState(false);
  const [loading, setLoading] = useState(false);

  const latenessLabels = useMemo(
    () => ({
      early: t.payroll.latenessEarly,
      late: t.payroll.latenessLate,
      onTime: t.payroll.latenessOnTime,
    }),
    [t]
  );

  useEffect(() => {
    listLocations().then(setLocations).catch(() => setLocations([]));
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Derive first, then read. Derivation is idempotent, so calling it on
      // every load is free after the first.
      await deriveTimeEntries(range, locationId || undefined);
      const [entriesResp, exceptionsResp] = await Promise.all([
        listTimeEntries(range, locationId || undefined),
        listPayrollExceptions(range, locationId || undefined),
      ]);
      setEntries(entriesResp.rows);
      setExceptions(exceptionsResp.rows);
      setSelected(new Set());
      setPaidPlanOnly(false);
    } catch (err) {
      // A 402 is not an error to apologise for: free-plan managers reach this
      // page only by typing the URL, and the honest answer is that this is a
      // paid feature.
      if (err instanceof ApiError && err.status === 402) {
        setPaidPlanOnly(true);
      } else {
        setError(t.payroll.loadFailed);
      }
      setEntries([]);
      setExceptions([]);
    } finally {
      setLoading(false);
    }
  }, [range, locationId, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const unexportedApproved = entries.filter(
    (e) => e.approved_at !== null && e.exported_at === null
  ).length;

  const totalMinutes = entries.reduce((sum, e) => sum + e.paid_minutes, 0);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) =>
      prev.size === entries.length
        ? new Set()
        : new Set(entries.map((e) => e.id))
    );
  };

  const confirmAttest = async (shiftId: string) => {
    try {
      await attestShift(shiftId, reason.trim() || undefined);
      setAttesting(null);
      setReason("");
      await reload();
    } catch {
      setError(t.payroll.attestFailed);
    }
  };

  const approve = async (ids?: string[]) => {
    try {
      const result = await approveTimeEntries(
        range,
        locationId || undefined,
        ids
      );
      setNotice(
        t.payroll.approvedCount.replace("{count}", String(result.approved))
      );
      await reload();
    } catch {
      setError(t.payroll.loadFailed);
    }
  };

  const download = async () => {
    try {
      const { blob, filename } = await downloadPayrollCsv(
        range,
        locationId || undefined
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(t.payroll.nothingToExport);
      } else {
        setError(t.payroll.loadFailed);
      }
    }
  };

  if (paidPlanOnly) {
    return (
      <div className="p-6">
        <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
          {t.payroll.title}
        </h1>
        <p className={`mt-4 max-w-2xl ${text.muted}`}>
          {t.payroll.paidPlanOnly}
        </p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
        {t.payroll.title}
      </h1>
      <p className={`mb-6 max-w-2xl ${text.muted}`}>{t.payroll.desc}</p>

      {/* Range + location */}
      <div className="flex flex-wrap gap-4 mb-6">
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.rangeStart}
          <input
            type="date"
            value={range.rangeStart}
            onChange={(e) =>
              setRange((r) => ({ ...r, rangeStart: e.target.value }))
            }
            className="glass-input block mt-1"
          />
        </label>
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.rangeEnd}
          <input
            type="date"
            value={range.rangeEnd}
            onChange={(e) =>
              setRange((r) => ({ ...r, rangeEnd: e.target.value }))
            }
            className="glass-input block mt-1"
          />
        </label>
        <label className={`block text-sm ${text.muted}`}>
          {t.payroll.location}
          <select
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
            className="glass-input block mt-1"
          >
            <option value="">{t.payroll.allLocations}</option>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <div className="glass-alert-error mb-4">{error}</div>}
      {notice && <div className="glass-alert-success mb-4">{notice}</div>}

      {/* Exception queue */}
      <h2 className={`text-lg font-semibold mt-8 mb-1 ${text.body}`}>
        {t.payroll.exceptionsTitle}
      </h2>
      <p className={`mb-3 max-w-2xl ${text.muted}`}>
        {t.payroll.exceptionsDesc}
      </p>
      {exceptions.length === 0 ? (
        <p className={text.muted}>{t.payroll.exceptionsEmpty}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="text-start py-2">{t.payroll.columnEmployee}</th>
              <th className="text-start py-2">{t.payroll.columnDate}</th>
              <th className="text-start py-2">{t.payroll.columnLocation}</th>
              <th className="text-start py-2">{t.payroll.columnRole}</th>
              <th className="text-start py-2">{t.payroll.columnStart}</th>
              <th className="text-start py-2">{t.payroll.columnEnd}</th>
              <th className="text-end py-2">{t.payroll.columnPaidHours}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {exceptions.map((row) => (
              <tr key={row.shift_id}>
                <td className="py-1">{row.employee_name}</td>
                <td className="py-1">{row.pay_date}</td>
                <td className="py-1">{row.location_name}</td>
                <td className="py-1">{row.role_name}</td>
                {/* formatTime reads the wall-clock face off the string; a
                    Date here would show a London manager a New York 9am
                    shift as 2pm (#92). */}
                <td className="py-1">{formatTime(row.start_time)}</td>
                <td className="py-1">{formatTime(row.end_time)}</td>
                <td className="py-1 text-end">
                  {formatPaidHours(row.paid_minutes)}
                </td>
                <td className="py-1 text-end">
                  {attesting === row.shift_id ? (
                    <span className="flex gap-2 items-center justify-end">
                      <input
                        type="text"
                        value={reason}
                        maxLength={500}
                        placeholder={t.payroll.attestReason}
                        onChange={(e) => setReason(e.target.value)}
                        className="glass-input"
                      />
                      <button
                        className="glass-btn"
                        onClick={() => void confirmAttest(row.shift_id)}
                      >
                        {t.payroll.attestConfirm}
                      </button>
                      <button
                        className="glass-btn-secondary"
                        onClick={() => {
                          setAttesting(null);
                          setReason("");
                        }}
                      >
                        {t.payroll.attestCancel}
                      </button>
                    </span>
                  ) : (
                    <button
                      className="glass-btn"
                      onClick={() => {
                        setAttesting(row.shift_id);
                        setReason("");
                      }}
                    >
                      {t.payroll.attest}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Entries */}
      <h2 className={`text-lg font-semibold mt-10 mb-3 ${text.body}`}>
        {t.payroll.entriesTitle}
      </h2>
      {entries.length === 0 ? (
        <p className={text.muted}>{t.payroll.entriesEmpty}</p>
      ) : (
        <>
          <p className={`mb-3 ${text.muted}`}>
            {t.payroll.totalHours
              .replace("{hours}", formatPaidHours(totalMinutes))
              .replace("{count}", String(entries.length))}
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="text-start py-2">
                  <input
                    type="checkbox"
                    checked={
                      entries.length > 0 && selected.size === entries.length
                    }
                    onChange={toggleAll}
                  />
                </th>
                <th className="text-start py-2">{t.payroll.columnEmployee}</th>
                <th className="text-start py-2">{t.payroll.columnDate}</th>
                <th className="text-start py-2">{t.payroll.columnLocation}</th>
                <th className="text-start py-2">{t.payroll.columnRole}</th>
                <th className="text-start py-2">{t.payroll.columnStart}</th>
                <th className="text-start py-2">{t.payroll.columnEnd}</th>
                <th className="text-end py-2">{t.payroll.columnPaidHours}</th>
                <th className="text-start py-2">{t.payroll.columnSource}</th>
                <th className="text-start py-2">
                  {t.payroll.columnCheckedInAt}
                </th>
                <th className="text-start py-2">{t.payroll.columnLateness}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((row) => (
                <tr
                  key={row.id}
                  className={row.exported_at ? text.muted : undefined}
                >
                  <td className="py-1">
                    <input
                      type="checkbox"
                      checked={selected.has(row.id)}
                      onChange={() => toggle(row.id)}
                    />
                  </td>
                  <td className="py-1">{row.employee_name}</td>
                  <td className="py-1">{row.pay_date}</td>
                  <td className="py-1">{row.location_name}</td>
                  <td className="py-1">{row.role_name}</td>
                  <td className="py-1">{formatTime(row.start_time)}</td>
                  <td className="py-1">{formatTime(row.end_time)}</td>
                  <td className="py-1 text-end">
                    {formatPaidHours(row.paid_minutes)}
                  </td>
                  <td className="py-1">
                    {row.source === "manager_attested" ? (
                      <span
                        title={
                          row.attested_by_name
                            ? t.payroll.attestedBy
                                .replace("{name}", row.attested_by_name)
                                .replace(
                                  "{date}",
                                  (row.attested_at ?? "").slice(0, 10)
                                )
                            : undefined
                        }
                      >
                        {t.payroll.sourceAttested}
                      </span>
                    ) : (
                      t.payroll.sourceCheckedIn
                    )}
                  </td>
                  <td className="py-1">
                    {row.checked_in_at ? formatTime(row.checked_in_at) : "—"}
                  </td>
                  <td className="py-1">
                    {formatLateness(row.lateness_minutes, latenessLabels)}
                    {row.exported_at && (
                      <span className={`ms-2 ${text.muted}`}>
                        {t.payroll.alreadyExported.replace(
                          "{date}",
                          row.exported_at.slice(0, 10)
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-3 items-center mt-8">
        <button
          className="glass-btn"
          disabled={selected.size === 0 || loading}
          onClick={() => void approve(Array.from(selected))}
        >
          {t.payroll.approveSelected}
        </button>
        <button
          className="glass-btn"
          disabled={entries.length === 0 || loading}
          onClick={() => void approve()}
        >
          {t.payroll.approveAll}
        </button>
        <button
          className="glass-btn"
          disabled={unexportedApproved === 0 || loading}
          onClick={() => void download()}
        >
          {t.payroll.download}
        </button>
        <span className={`text-sm ${text.muted}`}>
          {unexportedApproved === 0
            ? t.payroll.nothingToExport
            : t.payroll.downloadDesc}
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Render the attestation panel on the check-in report**

The two `checkIn` keys added in Task 10 belong here — the attestation rate is
read on the report next to the payroll page that produces it, and the spec
places the panel on the report page.

In `frontend/src/pages/manager/CheckInReport.tsx`, extend the state and the
load, then render the panel.

Add to the imports:

```tsx
import type { AttestationRateRow } from "../../types";
```

(append `AttestationRateRow` to the existing `import type { CheckInReportRow, CheckInStatus, Employee } from "../../types";` line rather than adding a second one.)

Add the state beside `retentionDays`:

```tsx
  const [attestation, setAttestation] = useState<AttestationRateRow[]>([]);
```

Extend the existing `getCheckInReport` effect body so it also stores the new
field:

```tsx
      .then((r) => {
        setRows(r.rows);
        setRetentionDays(r.retention_days);
        setAttestation(r.attestation);
      })
      .catch(() => {
        setRows([]);
        setAttestation([]);
      });
```

Render the panel immediately before the closing `</div>` of the page, after
the existing table:

```tsx
      {attestation.length > 0 && (
        <>
          <h2 className={`text-lg font-semibold mt-10 mb-1 ${text.body}`}>
            {t.checkIn.attestationTitle}
          </h2>
          <p className={`mb-3 max-w-2xl ${text.muted}`}>
            {/* Reporting only. Nothing reads this rate to block an
                attestation, refuse an export, or cap anything. */}
            {t.checkIn.attestationDesc.replace("{days}", String(retentionDays))}
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="text-start py-2">{t.checkIn.selectLocation}</th>
                <th className="text-end py-2">{t.payroll.entriesTitle}</th>
                <th className="text-end py-2">{t.payroll.sourceAttested}</th>
                <th className="text-end py-2">{t.checkIn.attestationTitle}</th>
              </tr>
            </thead>
            <tbody>
              {attestation.map((r) => (
                <tr key={r.location_id}>
                  <td className="py-1">{r.location_name}</td>
                  <td className="py-1 text-end">{r.entries}</td>
                  <td className="py-1 text-end">{r.attested}</td>
                  <td className="py-1 text-end">
                    {`${Math.round(r.rate * 100)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
```

- [ ] **Step 5: Run the build and the frontend tests**

Run: `cd frontend && npm run build`
Expected: PASS

Run: `cd frontend && npm test`
Expected: PASS — including `logicalDirection.test.ts`, which fails the build if any physical-direction utility (`ml-*`, `text-left`, `left-0`, …) slipped into either page. Every spacing utility above is logical (`ms-2`, `text-start`, `text-end`).

- [ ] **Step 6: Run the sidebar guard**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -q`
Expected: PASS — the new `/manager/payroll` link resolves to the new route, and `payroll` exists in `en.ts`'s `nav` block.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/manager/Payroll.tsx \
        frontend/src/pages/manager/CheckInReport.tsx frontend/src/App.tsx \
        frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(payroll): manager payroll page, and the attestation panel on the check-in report (#78)"
```

---

### Task 12: End-to-end — one week, from scan to CSV

**Files:**
- Test: `tests/test_payroll_end_to_end.py`

**Interfaces:**
- Consumes: every endpoint from Tasks 4–7 and the report field from Task 9. Produces nothing new — this task adds no production code. If it fails, the bug is in an earlier task.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_payroll_end_to_end.py`:

```python
"""One week, scan to CSV, through the HTTP surface only.

Every earlier test file proves one component. This one proves they compose:
a scanned shift and a missed one become two payable rows, approval gates the
export, the export stamps exactly once, and the attestation rate reports what
happened without blocking any of it.
"""

import csv
import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import PayrollExport, TimeEntry
from backend.services.payroll_export import CSV_HEADER
from tests.test_payroll_api import (
    TODAY, WEEK_AGO, _range_body, _tenant, _worked_shift,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_a_whole_week_from_scan_to_csv(
    client: AsyncClient, db_session: AsyncSession, paid
):
    # Two worked shifts: one scanned, one where the phone died.
    await _worked_shift(db_session, paid, days_ago=3, hour=9, checked_in=True)
    missed = await _worked_shift(db_session, paid, days_ago=2, hour=9,
                                 checked_in=False)
    # And one that has not happened yet, which must stay out of everything.
    await _worked_shift(db_session, paid, days_ago=-3, checked_in=False)

    # 1. Derive: the scanned shift becomes an entry, the missed one an
    #    exception, the future one neither.
    derived = await client.post("/api/v1/payroll/entries/derive",
                                json=_range_body(paid),
                                headers=paid.manager_headers)
    assert derived.status_code == 200, derived.text
    assert derived.json() == {"created": 1, "existing": 0, "exception_count": 1}

    exceptions = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert [r["shift_id"] for r in exceptions.json()["rows"]] == [missed]

    # 2. Attest the missed one. Now there are two payable rows.
    attested = await client.post("/api/v1/payroll/attest",
                                 json={"shift_id": missed,
                                       "reason": "Phone battery died"},
                                 headers=paid.manager_headers)
    assert attested.status_code == 201, attested.text

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert listed.json()["total_entries"] == 2
    assert listed.json()["total_paid_minutes"] == 960

    # 3. Nothing exports unapproved.
    refused = await client.post("/api/v1/payroll/export",
                                json=_range_body(paid, include_exported=False),
                                headers=paid.manager_headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "nothing_to_export"

    # 4. Approve, then export.
    approved = await client.post("/api/v1/payroll/approve",
                                 json=_range_body(paid),
                                 headers=paid.manager_headers)
    assert approved.json() == {"approved": 2, "already_approved": 0}

    exported = await client.post("/api/v1/payroll/export",
                                 json=_range_body(paid, include_exported=False),
                                 headers=paid.manager_headers)
    assert exported.status_code == 200, exported.text

    rows = list(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert rows[0] == CSV_HEADER
    assert len(rows) == 3
    sources = {r[CSV_HEADER.index("source")] for r in rows[1:]}
    assert sources == {"checked_in", "manager_attested"}
    # The attestation reason is an internal note; the CSV leaves the building.
    assert "Phone battery died" not in exported.text

    attested_row = next(r for r in rows[1:]
                        if r[CSV_HEADER.index("source")] == "manager_attested")
    assert attested_row[CSV_HEADER.index("checked_in_at")] == ""
    assert attested_row[CSV_HEADER.index("lateness_minutes")] == ""
    assert attested_row[CSV_HEADER.index("paid_hours")] == "8.00"

    # 5. Exactly one audit row, and every entry stamped exactly once.
    exports = (await db_session.execute(select(PayrollExport))).scalars().all()
    assert len(exports) == 1
    assert exports[0].entry_count == 2
    assert exports[0].paid_minutes_total == 960

    entries = (await db_session.execute(select(TimeEntry))).scalars().all()
    assert len(entries) == 2
    for entry in entries:
        await db_session.refresh(entry)
        assert entry.exported_at is not None
        assert entry.payroll_export_id == exports[0].id

    # 6. A second export of the same range changes nothing.
    again = await client.post("/api/v1/payroll/export",
                              json=_range_body(paid, include_exported=False),
                              headers=paid.manager_headers)
    assert again.status_code == 409

    # 7. The attestation rate reports what happened and blocks nothing.
    report = await client.get("/api/v1/check-ins/report",
                              headers=paid.manager_headers)
    rate_row = next(r for r in report.json()["attestation"]
                    if r["location_id"] == paid.location_id)
    assert rate_row["entries"] == 2
    assert rate_row["attested"] == 1
    assert rate_row["rate"] == 0.5


async def test_re_deriving_after_the_whole_flow_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """The page calls derive on every load, including after an export. It must
    not resurrect, duplicate or un-stamp anything."""
    await _worked_shift(db_session, paid, checked_in=True)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)
    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    stamp = entry.exported_at

    again = await client.post("/api/v1/payroll/entries/derive",
                              json=_range_body(paid),
                              headers=paid.manager_headers)

    assert again.json() == {"created": 0, "existing": 1, "exception_count": 0}
    await db_session.refresh(entry)
    assert entry.exported_at == stamp
    assert entry.approved_at is not None
    assert len((await db_session.execute(select(TimeEntry))).scalars().all()) == 1
```

- [ ] **Step 2: Run it**

Run: `../../../backend/.venv/bin/python -m pytest tests/test_payroll_end_to_end.py -q`
Expected: PASS — 2 passed. A failure here means a bug in Tasks 4–9, not in this file; fix it there.

- [ ] **Step 3: Run the whole backend suite**

Run: `../../../backend/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — no failures, including `tests/test_utc_today.py` (the AST sweep that fails on any `date.today()` or bare `datetime.now()` added by this feature) and `tests/test_sidebar_routes.py`.

- [ ] **Step 4: Run the whole frontend suite and build**

Run: `cd frontend && npm test && npm run build`
Expected: PASS both.

- [ ] **Step 5: Commit**

```bash
git add tests/test_payroll_end_to_end.py
git commit -m "test(payroll): end-to-end from scan to CSV, and derive-after-export idempotency (#78)"
```

---

## Notes for the executor

- **Do not run `npm install`.** `frontend/node_modules` is a symlink into the main checkout.
- **Never use bare `git stash`.** The stash stack is shared with every other worktree.
- **Do not push or open a PR** as part of executing this plan unless asked to.
- `backend/scripts/run_retention.py` needs no change — it prints whatever `run_data_retention` returns, and Task 8's new summary key comes along for free.
- No Terraform change is needed: `Settings` supplies `RETENTION_PAYROLL_EXPORT_LOGS_DAYS` and the value is not a secret.
- No backfill migration is needed or wanted: entries are derived on demand, bounded by `RETENTION_CHECKINS_DAYS` (180 days).
