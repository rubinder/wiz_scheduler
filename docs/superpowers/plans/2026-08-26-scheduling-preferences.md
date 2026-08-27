# Weighted Scheduling Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three per-employee scheduling preferences — day preference, hour-range frequency cap, hour-range preference — each weighted 0–1 in 0.1 increments, honoured by both the deterministic and the AI scheduling paths.

**Architecture:** Weight `1.0` is a hard filter applied while building each slot's eligible-employee list, so the candidate never reaches either the sorting code or the language model. Weights `0.1`–`0.9` feed one scoring function with two consumers: `_pick_employee` sorts on it, and the AI path uses it to order the eligible list it renders into the prompt. An absent row means weight 0, which is why the feature is additive and needs no backfill.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, pytest/pytest-asyncio; React 18 + TypeScript + Vite + Tailwind.

**Spec:** `docs/superpowers/specs/2026-08-26-scheduling-preferences-design.md`

## Global Constraints

- **Run the test suite with `backend/.venv/bin/python -m pytest`** from the repo root. The system Anaconda python lacks this project's dependencies and fails on imports, which looks like broken code.
- **Frontend gates are `npx tsc --noEmit` and `npm run build`,** run from `frontend/`. The frontend has **no test runner** — no vitest, no jest, no `test` script. Do not add one.
- **Do not install new dependencies.** CLAUDE.md forbids it without explicit instruction.
- **All 19 locale files must carry every new key** (`ar bn de en es fr hi id ja mr pcm pt ru ta te tr ur vi zh`). `LanguageContext.tsx` types translations as `Record<Language, Translations>` derived from `en.ts`, so a key added to `en.ts` alone **fails the TypeScript build**.
- **Roles are never hardcoded.** No role-name string literal may appear outside `seed.py`.
- **Multi-tenancy:** every query filters by `company_id` from the authenticated user's JWT.
- **Not plan-gated.** These endpoints must **not** call `assert_paid_plan`. The deterministic scheduler is the free tier's product and these preferences improve it.
- **Never raise inside the scheduling graph.** Degrade to `VACANT`; the pipeline's contract is that parsing and validation failures produce a status, not an exception.
- **Times are local wall-clock `"HH:MM"` strings,** never converted through a timezone. Consistent with `EmployeeDayBlackout` and the availability write-path contract.
- **This plan depends on `docs/superpowers/plans/2026-08-26-sidebar-nav-grouping.md` having shipped first.** Task 8 adds three rows to the `groupSchedulingRules` group that plan creates.
- **One overlap threshold, `0.5`,** shared by the hour-range preference and the frequency cap.

---

### Task 1: Models and migration

**Files:**
- Modify: `backend/models/employee.py` (append after `EmployeeDayBlackout`, which ends at line 148)
- Modify: `backend/models/__init__.py`
- Create: `backend/alembic/versions/0031_add_scheduling_preferences.py`
- Test: `tests/test_scheduling_preferences_model.py`

**Interfaces:**
- Consumes: nothing
- Produces: `EmployeeDayPreference`, `EmployeeHourRangePreference`, `EmployeeHourRangeCap` — all exported from `backend.models`. Fields on each: `id`, `company_id`, `employee_id`, `weight`. Plus `day_of_week: int` on the first; `start_time: str` / `end_time: str` on the second; `start_time` / `end_time` / `max_per_week: int` on the third.

- [x] **Step 1: Write the failing test**

```python
"""Schema guarantees for the three preference tables.

The weight column is the load-bearing part: `Numeric(2, 1)` plus a 0-1 check
is what makes "0 to 1 in 0.1 increments" a database guarantee rather than a
slider convention. The unique constraints matter because a duplicate row
would contribute its points twice and silently double a preference's effect.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)

COMPANY_ID = "comp0001"
EMPLOYEE_ID = "empl0001"


@pytest.mark.asyncio
async def test_weight_defaults_to_seven_tenths(db_session: AsyncSession):
    """0.7 is the create-time value, not a per-employee default."""
    row = EmployeeDayPreference(
        company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=0
    )
    db_session.add(row)
    await db_session.commit()
    assert float(row.weight) == 0.7


@pytest.mark.asyncio
async def test_weight_above_one_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=1, weight=1.5
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_weight_below_zero_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=2, weight=-0.1
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_two_decimal_places_are_rounded_to_one(db_session: AsyncSession):
    """Numeric(2, 1) is what enforces the 0.1 increment."""
    row = EmployeeDayPreference(
        company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=3, weight=0.75
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert float(row.weight) in (0.7, 0.8)  # scale-1 storage, not 0.75


@pytest.mark.asyncio
async def test_day_of_week_out_of_range_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=7
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_day_for_one_employee_is_rejected(db_session: AsyncSession):
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=4
        )
    )
    await db_session.commit()
    db_session.add(
        EmployeeDayPreference(
            company_id=COMPANY_ID, employee_id=EMPLOYEE_ID, day_of_week=4
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_duplicate_hour_range_is_rejected(db_session: AsyncSession):
    for _ in range(2):
        db_session.add(
            EmployeeHourRangePreference(
                company_id=COMPANY_ID,
                employee_id=EMPLOYEE_ID,
                start_time="13:00",
                end_time="17:00",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_cap_stores_max_per_week(db_session: AsyncSession):
    row = EmployeeHourRangeCap(
        company_id=COMPANY_ID,
        employee_id=EMPLOYEE_ID,
        start_time="16:00",
        end_time="22:00",
        max_per_week=3,
    )
    db_session.add(row)
    await db_session.commit()
    result = await db_session.execute(select(EmployeeHourRangeCap))
    assert result.scalars().one().max_per_week == 3
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_scheduling_preferences_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmployeeDayPreference'`.

- [x] **Step 3: Add the models**

Append to `backend/models/employee.py`, after `EmployeeDayBlackout`. Add `Numeric` and `UniqueConstraint` to the existing `sqlalchemy` import at the top of the file, and `text` if not already imported.

```python
class EmployeeDayPreference(Base):
    """Days of the week an employee prefers to work, with a 0-1 weight.

    weight 0    -> no effect (the state of an employee with no row at all)
    0.1 - 0.9   -> soft: scored, but violated rather than leaving a shift unfilled
    1.0         -> hard: the employee is not eligible on other days, and a slot
                   with no surviving candidate is emitted VACANT

    day_of_week follows Python's datetime.weekday() convention (0 = Monday),
    matching EmployeeDayBlackout.
    """

    __tablename__ = "employee_day_preferences"
    __table_args__ = (
        CheckConstraint(
            "day_of_week BETWEEN 0 AND 6", name="ck_employee_day_preferences_dow"
        ),
        CheckConstraint(
            "weight >= 0 AND weight <= 1", name="ck_employee_day_preferences_weight"
        ),
        UniqueConstraint(
            "employee_id", "day_of_week", name="uq_employee_day_preferences"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weight: Mapped[float] = mapped_column(
        Numeric(2, 1), nullable=False, server_default=text("0.7")
    )


class EmployeeHourRangePreference(Base):
    """An hour range an employee prefers to work, with a 0-1 weight.

    A shift satisfies the preference when at least 50% of the shift falls
    inside the range (SCHEDULING_RANGE_MATCH_THRESHOLD).
    """

    __tablename__ = "employee_hour_range_preferences"
    __table_args__ = (
        CheckConstraint(
            "weight >= 0 AND weight <= 1",
            name="ck_employee_hour_range_preferences_weight",
        ),
        UniqueConstraint(
            "employee_id",
            "start_time",
            "end_time",
            name="uq_employee_hour_range_preferences",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)    # "HH:MM"
    weight: Mapped[float] = mapped_column(
        Numeric(2, 1), nullable=False, server_default=text("0.7")
    )


class EmployeeHourRangeCap(Base):
    """A weekly cap on how often an employee works a given hour range.

    "16:00-22:00 at most 3 times a week". A shift counts toward the cap when
    at least 50% of it falls inside the range — the same threshold the
    hour-range preference uses.
    """

    __tablename__ = "employee_hour_range_caps"
    __table_args__ = (
        CheckConstraint(
            "weight >= 0 AND weight <= 1", name="ck_employee_hour_range_caps_weight"
        ),
        CheckConstraint(
            "max_per_week >= 0", name="ck_employee_hour_range_caps_max"
        ),
        UniqueConstraint(
            "employee_id", "start_time", "end_time", name="uq_employee_hour_range_caps"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)    # "HH:MM"
    max_per_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weight: Mapped[float] = mapped_column(
        Numeric(2, 1), nullable=False, server_default=text("0.7")
    )
```

- [x] **Step 4: Export them**

In `backend/models/__init__.py`, add to the `employee` import line and to `__all__`:

```python
from backend.models.employee import (
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)
```

```python
    "EmployeeDayPreference",
    "EmployeeHourRangeCap",
    "EmployeeHourRangePreference",
```

- [x] **Step 5: Write the migration**

Create `backend/alembic/versions/0031_add_scheduling_preferences.py`. The current head is `0030`.

```python
"""add scheduling preference tables

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_day_preferences",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column(
            "weight", sa.Numeric(2, 1), nullable=False, server_default=sa.text("0.7")
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6", name="ck_employee_day_preferences_dow"
        ),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1", name="ck_employee_day_preferences_weight"
        ),
        sa.UniqueConstraint(
            "employee_id", "day_of_week", name="uq_employee_day_preferences"
        ),
    )
    op.create_index(
        "ix_employee_day_preferences_company_id",
        "employee_day_preferences",
        ["company_id"],
    )
    op.create_index(
        "ix_employee_day_preferences_employee_id",
        "employee_day_preferences",
        ["employee_id"],
    )

    for table, extra_checks in (
        ("employee_hour_range_preferences", []),
        (
            "employee_hour_range_caps",
            [sa.CheckConstraint("max_per_week >= 0", name="ck_employee_hour_range_caps_max")],
        ),
    ):
        columns = [
            sa.Column("id", sa.String(length=8), nullable=False),
            sa.Column("company_id", sa.String(length=8), nullable=False),
            sa.Column("employee_id", sa.String(length=8), nullable=False),
            sa.Column("start_time", sa.String(length=5), nullable=False),
            sa.Column("end_time", sa.String(length=5), nullable=False),
        ]
        if table == "employee_hour_range_caps":
            columns.append(sa.Column("max_per_week", sa.SmallInteger(), nullable=False))
        columns.append(
            sa.Column(
                "weight", sa.Numeric(2, 1), nullable=False, server_default=sa.text("0.7")
            )
        )
        op.create_table(
            table,
            *columns,
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("weight >= 0 AND weight <= 1", name=f"ck_{table}_weight"),
            *extra_checks,
            sa.UniqueConstraint(
                "employee_id", "start_time", "end_time", name=f"uq_{table}"
            ),
        )
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.create_index(f"ix_{table}_employee_id", table, ["employee_id"])


def downgrade() -> None:
    op.drop_table("employee_hour_range_caps")
    op.drop_table("employee_hour_range_preferences")
    op.drop_table("employee_day_preferences")
```

- [x] **Step 6: Apply the migration and run the tests**

```bash
cd backend && .venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic current
cd .. && backend/.venv/bin/python -m pytest tests/test_scheduling_preferences_model.py -v
```
Expected: `alembic current` reports `0031 (head)`; all 8 tests PASS.

- [x] **Step 7: Confirm nothing else broke**

Run: `backend/.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [x] **Step 8: Commit**

```bash
git add backend/models/ backend/alembic/versions/0031_add_scheduling_preferences.py tests/test_scheduling_preferences_model.py
git commit -m "feat(scheduling): add weighted preference tables

Numeric(2,1) plus a 0-1 check makes the 0.1 increment a database guarantee.
An absent row means weight 0, so the feature is additive: no backfill, and
no existing schedule changes until a manager opts an employee in."
```

---

### Task 2: Extract the shared eligibility builder

**This is the riskiest task in the plan and it deliberately carries no new behaviour.** It removes a duplication so Task 5 has one place to apply the hard filter.

`local_scheduler._build_eligible_map:41` and `prompts.py:151` each run the *same four filters* — role match, has a window that day, `_time_covers`, `_blackout_blocks` — and differ only in what they append (`{**e, "_skill": skill}` vs the string `f'{e["id"]} [skill={skill}]'`). `local_scheduler.py:22` already imports `_blackout_blocks`, `_build_date_map`, `_parse_avail_by_day` and `_time_covers` from `prompts.py`, so `prompts.py` is the established home for these primitives.

**Files:**
- Modify: `backend/scheduling/prompts.py` (add the function; rewrite the loop at 151–188 to call it)
- Modify: `backend/scheduling/local_scheduler.py:41-99` (rewrite `_build_eligible_map` to call it)
- Test: `tests/test_eligibility_shared.py`

**Interfaces:**
- Consumes: `EmployeeDayPreference` etc. are *not* used yet — that is Task 5.
- Produces: `eligible_for_slot(prepared_employees, day, role_name, start, end) -> list[dict]` in `backend/scheduling/prompts.py`. Returns the matching prepared-employee dicts, each with a `_skill` key added. Tasks 5 and 6 both extend this one function.

- [x] **Step 1: Write the failing guard test**

```python
"""Both scheduling paths share one eligibility builder.

They used to hold two independent copies of the same four filters. The
weight-1.0 hard filter is applied inside this function, so a second private
copy would silently reintroduce a path where a hard preference is ignored —
exactly the failure this feature exists to prevent.

Written in the style of tests/test_scheduling_model.py: it reads the source,
because what is being asserted is structural, not behavioural.
"""

import re
from pathlib import Path

from backend.scheduling.prompts import eligible_for_slot

_SCHED = Path(__file__).resolve().parent.parent / "backend" / "scheduling"


def test_local_scheduler_uses_the_shared_builder():
    source = (_SCHED / "local_scheduler.py").read_text()
    assert "eligible_for_slot(" in source, (
        "local_scheduler must call eligible_for_slot rather than filtering "
        "candidates itself — the weight-1.0 hard filter lives in there"
    )


def test_prompts_uses_the_shared_builder():
    source = (_SCHED / "prompts.py").read_text()
    body = source.split("def eligible_for_slot", 1)[1]
    after = body.split("\ndef ", 1)[1] if "\ndef " in body else ""
    assert "eligible_for_slot(" in after, (
        "prompts.py must call eligible_for_slot when rendering SHIFT "
        "REQUIREMENTS rather than filtering candidates inline"
    )


def test_no_path_reimplements_the_blackout_filter():
    """_blackout_blocks should be called in exactly one place: the shared
    builder. A second call site means a second copy of the filter chain."""
    for name in ("local_scheduler.py", "prompts.py"):
        source = (_SCHED / name).read_text()
        calls = re.findall(r"_blackout_blocks\(", source)
        # prompts.py holds the definition (1 hit) plus the shared builder's
        # single call (1 hit). local_scheduler.py should hold none.
        limit = 2 if name == "prompts.py" else 0
        assert len(calls) <= limit, (
            f"{name} calls _blackout_blocks {len(calls)} times; the filter "
            "chain belongs only in eligible_for_slot"
        )


def test_shared_builder_returns_dicts_with_skill():
    prepared = [
        {
            "id": "e1",
            "_role_names": {"Cook"},
            "_day_windows": {"Monday": [("09:00", "17:00")]},
            "roles": [{"role_name": "Cook", "skill_level": 4}],
            "day_blackouts": [],
        }
    ]
    out = eligible_for_slot(prepared, "Monday", "Cook", "09:00", "17:00")
    assert [e["id"] for e in out] == ["e1"]
    assert out[0]["_skill"] == 4


def test_shared_builder_excludes_wrong_role_and_wrong_day():
    prepared = [
        {
            "id": "e1",
            "_role_names": {"Cook"},
            "_day_windows": {"Monday": [("09:00", "17:00")]},
            "roles": [{"role_name": "Cook", "skill_level": 4}],
            "day_blackouts": [],
        }
    ]
    assert eligible_for_slot(prepared, "Monday", "Server", "09:00", "17:00") == []
    assert eligible_for_slot(prepared, "Tuesday", "Cook", "09:00", "17:00") == []
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_eligibility_shared.py -v`
Expected: FAIL — `ImportError: cannot import name 'eligible_for_slot'`.

- [x] **Step 3: Add the shared builder to `prompts.py`**

Insert after `_blackout_blocks` (which ends around line 80):

```python
def eligible_for_slot(
    prepared_employees: List[Dict[str, Any]],
    day: str,
    role_name: str,
    start: str,
    end: str,
) -> List[Dict[str, Any]]:
    """Employees eligible for one (day, role, time) slot.

    THE single eligibility gate. Both the deterministic scheduler and the
    prompt builder call this — they previously held independent copies of the
    same four filters, which is why the weight-1.0 hard preference filter
    lives here: a candidate removed at this point is invisible to the sorting
    code AND to the language model, so a hard constraint cannot be violated by
    a model that never saw the candidate.

    Callers must pass employees already prepared with `_role_names` and
    `_day_windows`. Each returned dict is the input dict plus `_skill` for the
    requested role.
    """
    eligible: List[Dict[str, Any]] = []
    for e in prepared_employees:
        if role_name not in e["_role_names"]:
            continue
        day_ranges = e["_day_windows"].get(day, [])
        if not day_ranges:
            continue
        if not _time_covers(day_ranges, start, end):
            continue
        if _blackout_blocks(e.get("day_blackouts", []), day, start, end):
            continue
        skill = next(
            (
                r.get("skill_level", 0)
                for r in e.get("roles", [])
                if r.get("role_name") == role_name
            ),
            0,
        )
        eligible.append({**e, "_skill": skill})
    return eligible
```

- [x] **Step 4: Rewrite the `prompts.py` requirements loop**

Replace the inline filter at lines 151–188 (from `# Find eligible employees:` through `eligible.append(f'{e["id"]} [skill={skill}]')`) so the loop body becomes:

```python
            candidates = eligible_for_slot(emp_data, day, role_name, start, end)
            eligible = [f'{c["id"]} [skill={c["_skill"]}]' for c in candidates]
```

- [x] **Step 5: Rewrite `_build_eligible_map`**

In `local_scheduler.py`, replace the inner `for e in emp_prepared:` loop (lines ~76–96) with a single call, and add `eligible_for_slot` to the import on line 22:

```python
from backend.scheduling.prompts import (
    _blackout_blocks,
    _build_date_map,
    _parse_avail_by_day,
    _time_covers,
    eligible_for_slot,
)
```

```python
    eligible_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for day, slots in weekly_schedule.items():
        for slot in slots:
            role_name = slot.get("role_name", "Unknown")
            start = slot.get("start_time", "00:00")
            end = slot.get("end_time", "23:59")
            eligible_map[(day, role_name)] = eligible_for_slot(
                emp_prepared, day, role_name, start, end
            )

    return eligible_map
```

`_blackout_blocks` and `_time_covers` may now be unused in `local_scheduler.py`. If so, drop them from the import — `test_no_path_reimplements_the_blackout_filter` requires zero `_blackout_blocks` call sites there.

- [x] **Step 6: Run the guard and the full suite**

```bash
backend/.venv/bin/python -m pytest tests/test_eligibility_shared.py -v
backend/.venv/bin/python -m pytest tests/ -q
```
Expected: guard tests PASS, and **the full suite passes with the same count as before this task** — this refactor must change no behaviour. If any scheduling test changed outcome, the extraction is wrong; do not proceed.

- [x] **Step 7: Commit**

```bash
git add backend/scheduling/prompts.py backend/scheduling/local_scheduler.py tests/test_eligibility_shared.py
git commit -m "refactor(scheduling): one eligibility builder for both paths

local_scheduler._build_eligible_map and the prompt's SHIFT REQUIREMENTS loop
ran the same four filters in two independent copies. Extracted to
prompts.eligible_for_slot with a test that fails if a private copy returns.

No behaviour change — this is groundwork for the weight-1.0 hard filter,
which has to live in exactly one place to be a guarantee."
```

---

### Task 3: The overlap helper

**Files:**
- Modify: `backend/config.py` (add the threshold near the scheduling settings)
- Create: `backend/scheduling/preferences.py`
- Test: `tests/test_preference_overlap.py`

**Interfaces:**
- Consumes: nothing
- Produces: `overlap_fraction(shift_start: str, shift_end: str, range_start: str, range_end: str) -> float` and `matches_range(shift_start, shift_end, range_start, range_end) -> bool` in `backend.scheduling.preferences`; `settings.SCHEDULING_RANGE_MATCH_THRESHOLD`.

- [x] **Step 1: Write the failing test**

```python
"""The 50% overlap rule, shared by the hour-range preference and the cap.

Fraction thresholds are where off-by-one bugs live, so the boundary is
asserted from both sides. The fraction is of the SHIFT's duration, not the
range's — a 1-hour shift fully inside an 8-hour range is 100% matched, not
12.5%.
"""

from backend.config import settings
from backend.scheduling.preferences import matches_range, overlap_fraction


def test_fully_inside_is_one():
    assert overlap_fraction("13:00", "17:00", "13:00", "17:00") == 1.0


def test_short_shift_inside_a_long_range_is_one():
    """The denominator is the shift, not the range."""
    assert overlap_fraction("14:00", "15:00", "13:00", "21:00") == 1.0


def test_no_overlap_is_zero():
    assert overlap_fraction("09:00", "12:00", "16:00", "22:00") == 0.0


def test_touching_edges_is_zero():
    assert overlap_fraction("09:00", "16:00", "16:00", "22:00") == 0.0


def test_half_overlap_is_one_half():
    # 14:00-18:00 is 4h; 16:00-22:00 covers 16:00-18:00 = 2h.
    assert overlap_fraction("14:00", "18:00", "16:00", "22:00") == 0.5


def test_zero_length_shift_is_zero_not_a_crash():
    assert overlap_fraction("13:00", "13:00", "13:00", "17:00") == 0.0


def test_threshold_is_one_half():
    assert settings.SCHEDULING_RANGE_MATCH_THRESHOLD == 0.5


def test_exactly_at_the_threshold_matches():
    assert matches_range("14:00", "18:00", "16:00", "22:00") is True


def test_just_below_the_threshold_does_not_match():
    # 14:00-18:10 is 250 min; overlap 16:00-18:10 = 130 min -> 0.52 ... so use
    # a shift where the overlap is unambiguously under half:
    # 13:00-18:00 is 5h; overlap with 16:00-22:00 is 2h -> 0.4
    assert matches_range("13:00", "18:00", "16:00", "22:00") is False
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_preference_overlap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.scheduling.preferences'`.

- [x] **Step 3: Add the setting**

In `backend/config.py`, add above the `# LLM billing` block:

```python
    # Fraction of a SHIFT that must fall inside a configured hour range for
    # that shift to count. Used by BOTH the hour-range preference ("did this
    # employee get the hours they prefer?") and the frequency cap ("does this
    # shift count against their weekly allowance?"). One number, one rule,
    # one sentence to explain in the UI.
    SCHEDULING_RANGE_MATCH_THRESHOLD: float = 0.5
```

- [x] **Step 4: Create the module**

```python
"""Weighted scheduling preferences: overlap arithmetic and scoring.

Kept separate from local_scheduler.py so both scheduling paths can import it
without pulling in the deterministic strategies.
"""

from typing import Any, Dict, List

from backend.config import settings


def _minutes(hhmm: str) -> int:
    """'HH:MM' (or 'HH:MM:SS') to minutes since midnight."""
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def overlap_fraction(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> float:
    """Fraction of the SHIFT that falls inside the range, 0.0 to 1.0.

    The denominator is deliberately the shift, not the range: a one-hour shift
    sitting entirely inside an eight-hour preferred range has fully given the
    employee what they asked for, and should score 1.0 rather than 0.125.
    """
    s_start, s_end = _minutes(shift_start), _minutes(shift_end)
    duration = s_end - s_start
    if duration <= 0:
        return 0.0
    r_start, r_end = _minutes(range_start), _minutes(range_end)
    overlap = min(s_end, r_end) - max(s_start, r_start)
    if overlap <= 0:
        return 0.0
    return overlap / duration
```

**This body predates overnight handling and does not match what shipped.**
Task 5 (Step 1 of the fix wave that follows this one) found that a naive
`min/max` overlap silently returns 0 for a shift or range that crosses
midnight (e.g. "22:00"-"06:00"), because `_minutes("06:00")` is *smaller*
than `_minutes("22:00")`, making `duration` negative and short-circuiting
before any real overlap is computed. The shipped `overlap_fraction`
(`backend/scheduling/preferences.py`) normalises either side that wraps past
midnight by adding 24h to its end, handles a genuinely zero-length shift
before that normalisation so it isn't mistaken for a full-day shift, and then
tries the range shifted a full day earlier and later as well — because even
after normalising, the shift and range can land on different 24-hour "pages"
of the clock while still genuinely overlapping — keeping whichever alignment
gives the largest overlap:

```python
def overlap_fraction(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> float:
    s_start, s_end = _minutes(shift_start), _minutes(shift_end)
    if s_end == s_start:
        return 0.0
    if s_end <= s_start:
        s_end += 24 * 60
    duration = s_end - s_start
    if duration <= 0:
        return 0.0

    r_start, r_end = _minutes(range_start), _minutes(range_end)
    if r_end <= r_start:
        r_end += 24 * 60

    best_overlap = 0.0
    for page in (-24 * 60, 0, 24 * 60):
        overlap = min(s_end, r_end + page) - max(s_start, r_start + page)
        if overlap > best_overlap:
            best_overlap = overlap
    return best_overlap / duration
```

```python
def matches_range(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> bool:
    """Whether a shift counts as being 'in' a configured hour range."""
    return (
        overlap_fraction(shift_start, shift_end, range_start, range_end)
        >= settings.SCHEDULING_RANGE_MATCH_THRESHOLD
    )
```

- [x] **Step 5: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_preference_overlap.py -v`
Expected: 9 passed.

- [x] **Step 6: Commit**

```bash
git add backend/config.py backend/scheduling/preferences.py tests/test_preference_overlap.py
git commit -m "feat(scheduling): 50% shift-overlap rule for hour ranges"
```

---

### Task 4: Hard filter and soft score

**Files:**
- Modify: `backend/scheduling/preferences.py`
- Test: `tests/test_preference_scoring.py`

**Interfaces:**
- Consumes: `overlap_fraction`, `matches_range` from Task 3.
- Produces:
  - `blocked_by_hard_preference(emp: dict, day_index: int, start: str, end: str, range_counts: dict) -> bool`
  - `preference_score(emp: dict, day_index: int, start: str, end: str, range_counts: dict) -> float` — lower is better, 0.0 when the employee has no preferences.
  - `PREFERENCE_PENALTY = 50.0`
  - Each employee dict is expected to carry `day_preferences`, `hour_range_preferences` and `hour_range_caps` lists (Task 5 loads them). `range_counts` maps `(employee_id, range_start, range_end)` to how many shifts already assigned this week.

- [x] **Step 1: Write the failing test**

```python
"""Hard filtering and soft scoring for the three preference parameters.

Score convention matches local_scheduler._affinity_score: LOWER IS BETTER, on
the same +/-50 point scale, so the terms compose without rescaling.

The most important assertion here is test_no_preferences_scores_zero: an
employee with no rows must be completely unaffected, which is what makes the
whole feature additive.
"""

from backend.scheduling.preferences import (
    PREFERENCE_PENALTY,
    blocked_by_hard_preference,
    preference_score,
)

MONDAY, TUESDAY = 0, 1


def _emp(day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": "e1",
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_no_preferences_scores_zero():
    assert preference_score(_emp(), MONDAY, "09:00", "17:00", {}) == 0.0


def test_no_preferences_is_never_blocked():
    assert blocked_by_hard_preference(_emp(), MONDAY, "09:00", "17:00", {}) is False


def test_preferred_day_is_not_penalised():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.7}])
    assert preference_score(emp, MONDAY, "09:00", "17:00", {}) == 0.0


def test_non_preferred_day_is_penalised_in_proportion_to_weight():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.7}])
    assert preference_score(emp, TUESDAY, "09:00", "17:00", {}) == 0.7 * PREFERENCE_PENALTY


def test_weight_zero_contributes_nothing():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.0}])
    assert preference_score(emp, TUESDAY, "09:00", "17:00", {}) == 0.0


def test_hard_day_preference_blocks_other_days():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 1.0}])
    assert blocked_by_hard_preference(emp, TUESDAY, "09:00", "17:00", {}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "09:00", "17:00", {}) is False


def test_soft_day_preference_never_blocks():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.9}])
    assert blocked_by_hard_preference(emp, TUESDAY, "09:00", "17:00", {}) is False


def test_hour_range_preference_penalises_a_non_matching_shift():
    emp = _emp(range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 0.5}])
    assert preference_score(emp, MONDAY, "13:00", "17:00", {}) == 0.0
    assert preference_score(emp, MONDAY, "06:00", "10:00", {}) == 0.5 * PREFERENCE_PENALTY


def test_hard_hour_range_preference_blocks_a_non_matching_shift():
    emp = _emp(range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 1.0}])
    assert blocked_by_hard_preference(emp, MONDAY, "06:00", "10:00", {}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "13:00", "17:00", {}) is False


def test_cap_penalises_only_once_the_allowance_is_used():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.7}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert preference_score(emp, MONDAY, "16:00", "22:00", {key: 2}) == 0.0
    assert preference_score(emp, MONDAY, "16:00", "22:00", {key: 3}) == 0.7 * PREFERENCE_PENALTY


def test_hard_cap_blocks_once_the_allowance_is_used():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 1.0}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert blocked_by_hard_preference(emp, MONDAY, "16:00", "22:00", {key: 3}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "16:00", "22:00", {key: 2}) is False


def test_a_cap_does_not_apply_to_a_shift_outside_its_range():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 1.0}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert blocked_by_hard_preference(emp, MONDAY, "06:00", "10:00", {key: 9}) is False


def test_penalties_from_several_parameters_add_up():
    emp = _emp(
        day_prefs=[{"day_of_week": MONDAY, "weight": 0.4}],
        range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 0.6}],
    )
    expected = (0.4 + 0.6) * PREFERENCE_PENALTY
    assert preference_score(emp, TUESDAY, "06:00", "10:00", {}) == expected
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_preference_scoring.py -v`
Expected: FAIL — `ImportError: cannot import name 'PREFERENCE_PENALTY'`.

- [x] **Step 3: Implement**

Append to `backend/scheduling/preferences.py`:

```python
# Same points scale as local_scheduler._affinity_score, so preference and
# affinity terms compose in _pick_employee without rescaling either.
PREFERENCE_PENALTY = 50.0

_HARD = 1.0


def _cap_count(range_counts: Dict[Any, int], emp_id: str, cap: Dict[str, Any]) -> int:
    return range_counts.get((emp_id, cap["start_time"], cap["end_time"]), 0)


def _day_violated(emp: Dict[str, Any], day_index: int) -> List[Dict[str, Any]]:
    """Day preferences this slot violates.

    A day preference only means anything as a set: if an employee prefers Mon,
    Tue and Wed, scheduling them on Thursday violates all three rows at once.
    Returning them individually would multiply the penalty by the number of
    preferred days, so the set is collapsed to at most one violation carrying
    the strongest weight.
    """
    prefs = emp.get("day_preferences") or []
    if not prefs:
        return []
    if any(int(p["day_of_week"]) == day_index for p in prefs):
        return []
    return [max(prefs, key=lambda p: float(p["weight"]))]


def _range_violated(emp: Dict[str, Any], start: str, end: str) -> List[Dict[str, Any]]:
    """Hour-range preferences this slot violates, by the same set logic."""
    prefs = emp.get("hour_range_preferences") or []
    if not prefs:
        return []
    if any(matches_range(start, end, p["start_time"], p["end_time"]) for p in prefs):
        return []
    return [max(prefs, key=lambda p: float(p["weight"]))]


def _caps_exceeded(
    emp: Dict[str, Any], start: str, end: str, range_counts: Dict[Any, int]
) -> List[Dict[str, Any]]:
    """Caps whose weekly allowance this slot would exceed."""
    hit: List[Dict[str, Any]] = []
    for cap in emp.get("hour_range_caps") or []:
        if not matches_range(start, end, cap["start_time"], cap["end_time"]):
            continue
        if _cap_count(range_counts, emp["id"], cap) >= int(cap["max_per_week"]):
            hit.append(cap)
    return hit


def blocked_by_hard_preference(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> bool:
    """Whether a weight-1.0 preference makes this employee ineligible.

    Called from eligible_for_slot, so a blocked employee is never offered to
    the sorting code or to the language model. A slot where this removes every
    candidate is emitted VACANT — that is the intended meaning of a hard
    preference, not a failure.
    """
    violations = (
        _day_violated(emp, day_index)
        + _range_violated(emp, start, end)
        + _caps_exceeded(emp, start, end, range_counts)
    )
    return any(float(v["weight"]) >= _HARD for v in violations)


def preference_score(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> float:
    """Soft-preference penalty for assigning this employee to this slot.

    Lower is better, matching _affinity_score. Returns 0.0 for an employee
    with no preferences configured — the state of every employee until a
    manager opts them in, and what makes this feature additive.
    """
    violations = (
        _day_violated(emp, day_index)
        + _range_violated(emp, start, end)
        + _caps_exceeded(emp, start, end, range_counts)
    )
    return sum(
        float(v["weight"]) * PREFERENCE_PENALTY
        for v in violations
        if float(v["weight"]) < _HARD
    )
```

- [x] **Step 4: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_preference_scoring.py -v`
Expected: 13 passed.

- [x] **Step 5: Commit**

```bash
git add backend/scheduling/preferences.py tests/test_preference_scoring.py
git commit -m "feat(scheduling): hard filter and soft score for preferences

Day and hour-range preferences collapse to at most one violation each, so an
employee preferring three days is not penalised three times for working a
fourth."
```

---

### Task 5: Wire into the deterministic path

**Files:**
- Modify: `backend/scheduling/prompts.py` (`eligible_for_slot` gains the hard filter)
- Modify: `backend/scheduling/local_scheduler.py` (`_pick_employee` adds the soft score; `local_schedule` tracks `range_counts`)
- Modify: `backend/scheduling/graph.py` (load preferences onto employee dicts)
- Test: `tests/test_preferences_local_scheduler.py`

**Interfaces:**
- Consumes: `blocked_by_hard_preference`, `preference_score` (Task 4); `eligible_for_slot` (Task 2).
- Produces: `eligible_for_slot` gains two optional keyword arguments — `day_index: int | None = None`, `range_counts: dict | None = None`. When both are omitted the hard filter is skipped, so existing callers keep working.

- [x] **Step 1: Write the failing test — the no-op regression first**

```python
"""Preferences change the deterministic scheduler only when configured.

test_zero_preferences_is_a_no_op is the single most important test in this
feature. _pick_employee is on the path of all four strategies, so if adding a
scoring term perturbs the no-preference case, every existing schedule changes
silently.
"""

import pytest

from backend.scheduling.preferences import blocked_by_hard_preference
from backend.scheduling.prompts import eligible_for_slot


def _emp(eid, day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": eid,
        "_role_names": {"Cook"},
        "_day_windows": {"Monday": [("00:00", "23:59")]},
        "roles": [{"role_name": "Cook", "skill_level": 3}],
        "day_blackouts": [],
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_zero_preferences_is_a_no_op():
    """With no preferences, the eligible set is exactly what it was before."""
    prepared = [_emp("e1"), _emp("e2")]
    without = eligible_for_slot(prepared, "Monday", "Cook", "09:00", "17:00")
    with_args = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in without] == ["e1", "e2"]
    assert [e["id"] for e in with_args] == ["e1", "e2"]


def test_hard_day_preference_removes_the_candidate():
    prepared = [
        _emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}]),  # Monday only
        _emp("e2"),
    ]
    # Monday is day_index 0 -> e1 stays
    monday = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in monday] == ["e1", "e2"]
    # Tuesday -> e1 is filtered out entirely
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    prepared[1]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    tuesday = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in tuesday] == ["e2"]


def test_a_slot_can_lose_every_candidate():
    """This is what produces a VACANT shift, and it must not raise."""
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    assert eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    ) == []


def test_soft_preference_does_not_remove_the_candidate():
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 0.9}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    out = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in out] == ["e1"]
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_preferences_local_scheduler.py -v`
Expected: FAIL — `eligible_for_slot() got an unexpected keyword argument 'day_index'`.

- [x] **Step 3: Add the hard filter to `eligible_for_slot`**

In `prompts.py`, change the signature and add one condition. Import at the top of the function body to avoid a circular import at module load:

```python
def eligible_for_slot(
    prepared_employees: List[Dict[str, Any]],
    day: str,
    role_name: str,
    start: str,
    end: str,
    day_index: int | None = None,
    range_counts: Dict[Any, int] | None = None,
) -> List[Dict[str, Any]]:
```

and immediately after the `_blackout_blocks` check inside the loop:

```python
        if day_index is not None:
            from backend.scheduling.preferences import blocked_by_hard_preference

            if blocked_by_hard_preference(e, day_index, start, end, range_counts or {}):
                continue
```

Update the docstring to note that omitting `day_index` skips the preference filter.

- [x] **Step 4: Pass the day index and counts from `_build_eligible_map`**

`weekly_schedule` is keyed by day *name*. Map it to the `datetime.weekday()` index the preferences use. In `local_scheduler.py`, above `_build_eligible_map`:

```python
_DAY_INDEX = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}
```

and in the loop:

```python
            eligible_map[(day, role_name)] = eligible_for_slot(
                emp_prepared,
                day,
                role_name,
                start,
                end,
                day_index=_DAY_INDEX.get(day),
            )
```

**Corrected from the original plan:** `_build_eligible_map` does *not* gain a
`range_counts` parameter, and no `range_counts = {}` is introduced here.
`eligible_for_slot` is called with `range_counts` simply omitted, which
defaults to `None` and is treated as `{}` inside the function — a map built
once before any assignment exists can never see a non-empty count regardless,
so there is nothing for a parameter to thread through at this call site. The
real `range_counts` (see Step 5) lives in `local_schedule` and is only ever
passed to `_pick_employee`, not to `_build_eligible_map`.

- [x] **Step 5: Track counts and score in `local_schedule` / `_pick_employee`**

In `local_schedule`, initialise `range_counts: Dict[Any, int] = {}` alongside the other draft state. After each assignment, for every cap on the chosen employee whose range the shift matches, increment `range_counts[(emp_id, cap["start_time"], cap["end_time"])]`.

In `_pick_employee`, add the preference term to each candidate's score in all four strategy branches, beside the existing `_affinity_score` call:

```python
        pref = preference_score(emp, day_index, start, end, range_counts)
```

and include `pref` in the tuple that `scored.sort(key=lambda x: x[0])` orders on.

- [x] **Step 6: Load preferences onto employee dicts**

In `backend/scheduling/graph.py`, where employees are loaded with their roles, affinities and `day_blackouts`, also select the three preference tables filtered by `company_id`, and attach `day_preferences`, `hour_range_preferences` and `hour_range_caps` lists to each employee dict. Each entry is a plain dict with the model's column names, weights cast to `float`.

- [x] **Step 7: Run the tests**

```bash
backend/.venv/bin/python -m pytest tests/test_preferences_local_scheduler.py -v
backend/.venv/bin/python -m pytest tests/ -q
```
Expected: new tests PASS; **the full suite passes with no change to existing scheduling test outcomes** — the seeded demo has no preference rows, so every existing schedule must be unchanged.

- [x] **Step 8: Commit**

```bash
git add backend/scheduling/ tests/test_preferences_local_scheduler.py
git commit -m "feat(scheduling): honour preferences in the deterministic path

Hard weights filter candidates inside eligible_for_slot; soft weights score
in _pick_employee. Omitting day_index skips the filter, so the no-preference
case is provably unchanged."
```

---

### Task 6: Wire into the AI path

**Files:**
- Modify: `backend/scheduling/prompts.py` (order the eligible list by score)
- Modify: `backend/scheduling/nodes.py` (`validate_and_update_availability` trims cap violations)
- Test: `tests/test_preferences_ai_path.py`

**Interfaces:**
- Consumes: `preference_score`, `matches_range` (Tasks 3–4); `eligible_for_slot` with the hard filter (Task 5).
- Produces: no new public names. `validate_and_update_availability` gains cap-trimming behaviour, setting `shift["status"] = "VACANT"` on assignments beyond `max_per_week`.

- [x] **Step 1: Write the failing test**

```python
"""Preferences reach the AI path two ways.

Hard weights are already handled: prompts.eligible_for_slot filters them, so
the model is never offered the candidate. Soft weights order the Eligible list
so the model reads better candidates first.

The frequency cap is the one parameter that cannot be pre-filtered here — a
whole week generates in a single call, so per-slot counts do not exist
beforehand. It is enforced after generation instead.
"""

from backend.scheduling.nodes import validate_and_update_availability


def _shift(eid, date, start, end, status="ok"):
    return {
        "employee_id": eid,
        "employee_name": eid,
        "role_id": "r1",
        "role_name": "Cook",
        "location_id": "loc1",
        "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00",
        "status": status,
    }


def test_cap_violations_beyond_the_allowance_are_vacated():
    """Four 16:00-22:00 shifts against a cap of 3 -> the fourth is VACANT."""
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
        _shift("e1", "2026-09-02", "16:00", "22:00"),
        _shift("e1", "2026-09-03", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 3, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses.count("VACANT") == 1
    assert statuses[:3] == ["ok", "ok", "ok"]


def test_a_cap_within_its_allowance_vacates_nothing():
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 3, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    assert all(s["status"] == "ok" for s in out["current_parsed_shifts"])


def test_no_preferences_leaves_shifts_untouched():
    shifts = [_shift("e1", "2026-08-31", "16:00", "22:00")]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {},
    }
    out = validate_and_update_availability(state)
    assert out["current_parsed_shifts"][0]["status"] == "ok"
```

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_preferences_ai_path.py -v`
Expected: FAIL — no cap trimming yet, so `statuses.count("VACANT") == 0`.

- [x] **Step 3: Order the prompt's eligible list**

In `prompts.py`, the requirements loop from Task 2 Step 4 becomes:

```python
            candidates = eligible_for_slot(
                emp_data, day, role_name, start, end,
                day_index=_DAY_INDEX_FOR_PROMPT.get(day), range_counts={},
            )
            candidates.sort(key=lambda c: preference_score(
                c, _DAY_INDEX_FOR_PROMPT.get(day, 0), start, end, {}
            ))
            eligible = [f'{c["id"]} [skill={c["_skill"]}]' for c in candidates]
```

Define `_DAY_INDEX_FOR_PROMPT` in `prompts.py` with the same mapping as `_DAY_INDEX`.

**Corrected from the original plan:** `preference_score` ships as a module-level
import in `prompts.py` (alongside the existing module-level import of the same
name), not imported inside the function. There is no circular import between
`prompts.py` and `preferences.py` to avoid — `preferences.py` imports nothing
from `prompts.py` — so the function-local import this plan originally called
for was unnecessary, and a review round moved it (along with the
`blocked_by_hard_preference` import Task 5 added the same way) to module level
for consistency.

Add one line to the numbered instruction block (currently ending at rule 6, `prompts.py:259`):

```python
        f"7. The Eligible list is ordered BEST FIRST by employee scheduling\n"
        f"   preferences. Prefer earlier entries when candidates are otherwise\n"
        f"   equal.\n"
```

- [x] **Step 4: Trim cap violations in the validator**

In `validate_and_update_availability` (`nodes.py:806`), after the existing conflict handling and before returning, add a pass that walks the shifts in date order, counts matches per `(employee_id, cap range)` using `matches_range`, and sets `shift["status"] = "VACANT"` on any assignment beyond `max_per_week` for a cap with `weight >= 1.0`. Read preferences from `state.get("employee_preferences", {})`; when the key is absent the pass is a no-op.

Never raise here — the graph's contract is that this node degrades rather than throwing.

- [x] **Step 5: Populate `employee_preferences` in state**

Add `employee_preferences: Dict[str, Dict[str, list]]` to `SchedulingState` in `backend/scheduling/state.py`, and populate it in `graph.py` alongside the per-employee lists added in Task 5 Step 6.

- [x] **Step 6: Run the tests**

```bash
backend/.venv/bin/python -m pytest tests/test_preferences_ai_path.py -v
backend/.venv/bin/python -m pytest tests/ -q
```
Expected: 3 new tests PASS; full suite green.

- [x] **Step 7: Commit**

```bash
git add backend/scheduling/ tests/test_preferences_ai_path.py
git commit -m "feat(scheduling): honour preferences in the AI path

Hard weights were already filtered out of the eligible list the prompt
renders. Soft weights now order that list best-first. Frequency caps cannot
be pre-filtered here — a whole week generates in one call — so they are
trimmed after generation in the validator."
```

---

### Task 7: API

**Files:**
- Create: `backend/routers/scheduling_preferences.py`
- Modify: `backend/main.py` (register the router near line 102)
- Test: `tests/test_scheduling_preferences_api.py`

**Interfaces:**
- Consumes: the three models from Task 1.
- Produces: `GET|POST|PUT|DELETE /api/v1/scheduling-preferences/days`, `/hour-ranges`, `/caps`.

- [x] **Step 1: Write the failing test**

```python
"""API surface for the three preference types.

The gating assertions are the point: these endpoints must NOT call
assert_paid_plan. The deterministic scheduler is the free tier's product and
these preferences improve it, so gating them would weaken the tier they most
help. A future refactor that adds a paid gate should fail here.
"""

import pytest
from httpx import AsyncClient

BASE = "/api/v1/scheduling-preferences"


@pytest.mark.asyncio
async def test_requires_authentication(client: AsyncClient):
    resp = await client.get(f"{BASE}/days")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_free_plan_is_not_blocked(client: AsyncClient, manager_token: str):
    """Explicitly NOT 402 — these are ungated."""
    resp = await client.get(
        f"{BASE}/days", headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_defaults_weight_to_seven_tenths(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 0},
    )
    assert resp.status_code == 201
    assert resp.json()["weight"] == 0.7


@pytest.mark.asyncio
async def test_weight_above_one_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 1, "weight": 1.5},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_two_decimal_weight_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 2, "weight": 0.75},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cannot_create_for_another_companys_employee(
    client: AsyncClient, manager_token: str, other_company_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": other_company_employee_id, "day_of_week": 0},
    )
    assert resp.status_code in (403, 404)
```

Reuse the `client`, `manager_token` and employee fixtures already used by `tests/test_plan_enforcement.py`; add `other_company_employee_id` to `tests/conftest.py` if it does not exist.

- [x] **Step 2: Run it to confirm it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_scheduling_preferences_api.py -v`
Expected: FAIL — 404 on every route.

- [x] **Step 3: Write the router**

Create `backend/routers/scheduling_preferences.py` with a `router = APIRouter(prefix="/scheduling-preferences", tags=["scheduling-preferences"])` and, for each of the three resources, `GET` (list for the company), `POST` (create, 201), `PUT /{id}` (update weight and fields), `DELETE /{id}` (204). Every handler takes `current_user: User = Depends(require_manager)` and filters by `current_user.company_id`. Creating or updating verifies the target employee belongs to the caller's company before writing.

The shared weight validation, used by all three Pydantic request models:

```python
weight: float = Field(default=0.7, ge=0.0, le=1.0)

@field_validator("weight")
@classmethod
def one_decimal_place(cls, v: float) -> float:
    if round(v, 1) != v:
        raise ValueError("weight must be in increments of 0.1")
    return v
```

**Do not call `assert_paid_plan`.**

- [x] **Step 4: Register the router**

In `backend/main.py`, beside the other `include_router` calls (line ~102):

```python
    app.include_router(scheduling_preferences.router, prefix=api_prefix)
```

- [x] **Step 5: Run the tests**

```bash
backend/.venv/bin/python -m pytest tests/test_scheduling_preferences_api.py -v
backend/.venv/bin/python -m pytest tests/ -q
```
Expected: 6 new tests PASS; full suite green.

- [x] **Step 6: Commit**

```bash
git add backend/routers/scheduling_preferences.py backend/main.py tests/test_scheduling_preferences_api.py tests/conftest.py
git commit -m "feat(api): CRUD for weighted scheduling preferences

Deliberately ungated — the deterministic scheduler is the free tier's
product and these preferences improve it."
```

---

### Task 8: Frontend

Depends on `docs/superpowers/plans/2026-08-26-sidebar-nav-grouping.md` having shipped; this adds three rows to the `groupSchedulingRules` group it created.

**Files:**
- Create: `frontend/src/api/schedulingPreferences.ts`
- Create: `frontend/src/pages/manager/DayPreferences.tsx`
- Create: `frontend/src/pages/manager/HourRangePreferences.tsx`
- Create: `frontend/src/pages/manager/FrequencyCaps.tsx`
- Create: `frontend/src/components/shared/WeightSlider.tsx`
- Modify: `frontend/src/App.tsx` (three routes)
- Modify: `frontend/src/components/layout/Sidebar.tsx` (three entries in `groupSchedulingRules`)
- Modify: all 19 locale files

**Interfaces:**
- Consumes: the endpoints from Task 7.
- Produces: routes `/manager/day-preferences`, `/manager/hour-range-preferences`, `/manager/frequency-caps`.

- [x] **Step 1: Add the typed API client**

`frontend/src/api/schedulingPreferences.ts`, following `frontend/src/api/affinities.ts` — one exported interface and list/create/update/remove function per resource, all through the shared `apiFetch`.

- [x] **Step 2: Build `WeightSlider`**

One shared component used by all three pages, so the 1.0 warning is written once:

```tsx
interface Props {
  value: number;
  onChange: (v: number) => void;
  label: string;
  hardWarning: string;
}

export default function WeightSlider({ value, onChange, label, hardWarning }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <label className="flex items-center gap-3 text-sm">
        <span className="w-24">{label}</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1"
        />
        <span className="w-10 text-right tabular-nums">{value.toFixed(1)}</span>
      </label>
      {value >= 1 && (
        <p className="text-xs text-orange-700 ml-24">{hardWarning}</p>
      )}
    </div>
  );
}
```

The warning renders only at 1.0, where the consequence is real — a manager must see it at the moment of choosing, not discover it in the next schedule.

- [x] **Step 3: Build the three pages**

Follow `frontend/src/pages/manager/HourRestrictions.tsx` (278 lines: local draft rows, a name filter, save/delete per row). New rows start at `weight = 0.7`. Employees with no row are not listed — that is weight 0.

Both range pages render the matching rule verbatim: **"A shift counts when at least 50% of it falls inside this range."**

- [x] **Step 4: Add routes**

In `App.tsx`, beside the other manager routes:

```tsx
            <Route path="day-preferences" element={<DayPreferences />} />
            <Route path="hour-range-preferences" element={<HourRangePreferences />} />
            <Route path="frequency-caps" element={<FrequencyCaps />} />
```

- [x] **Step 5: Add the sidebar entries**

In `Sidebar.tsx`, append to the `groupSchedulingRules` children array:

```tsx
      { to: "/manager/day-preferences", labelKey: "dayPreferences" },
      { to: "/manager/hour-range-preferences", labelKey: "hourRangePreferences" },
      { to: "/manager/frequency-caps", labelKey: "frequencyCaps" },
```

- [x] **Step 6: Add i18n keys to all 19 locales**

Three `nav` keys (`dayPreferences`, `hourRangePreferences`, `frequencyCaps`) plus a page-strings block per feature — titles, column headers, the 50% sentence, and the 1.0 VACANT warning. Translate per locale; do not paste English.

- [x] **Step 7: Verify**

```bash
cd frontend && npx tsc --noEmit && npm run build
cd .. && backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -v
```
Expected: `tsc` exit 0, build succeeds, and `tests/test_sidebar_routes.py` passes in full —
the route guard confirms all three new links resolve, and
`test_every_label_key_exists_in_en_translations` confirms the three new
`labelKey`s added in Step 5 (`dayPreferences`, `hourRangePreferences`,
`frequencyCaps`) all have matching `nav` keys added in Step 6. That test is
the only thing that catches a mismatch between those two steps.

- [x] **Step 8: Commit**

```bash
git add frontend/src tests/
git commit -m "feat(ui): manager pages for the three scheduling preferences"
```

---

### Task 9: End-to-end verification

**Files:** none modified. This task produces evidence, not code.

- [ ] **Step 1: Bring up a clean local stack**

```bash
docker compose up -d postgres
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
cd backend && .venv/bin/python -m alembic upgrade head && cd ..
backend/.venv/bin/python -m backend.seed
backend/.venv/bin/python -m uvicorn backend.main:app --port 8000 &
```

Note the port: `docker-compose.yml` maps postgres to host **5433**, while `.env` points at 5432. Override `DATABASE_URL` as above rather than editing `.env`.

- [ ] **Step 2: Baseline with no preferences**

Log in as `abc@example.com` / `example`, generate a schedule with `{"use_local": true, "strategy": "rotation"}`, and record the shifts. With no preference rows this must match the pre-feature output exactly.

- [ ] **Step 3: Add a soft preference and confirm it biases without blocking**

Give one employee a day preference at `0.7` for a day they are not currently scheduled. Regenerate. The schedule should still fill every slot; that employee should shift toward their preferred day.

- [ ] **Step 4: Add a hard preference and confirm VACANT**

Raise the weight to `1.0` on an employee whose role is thinly staffed, so no other candidate can cover a slot. Regenerate and confirm the slot comes back **VACANT** and the response is a normal 200 stream, not an error.

- [ ] **Step 5: Confirm the frequency cap in both modes**

Set a cap of 1 per week on a range covering the template's main slot, weight `1.0`. Generate with `use_local: true` and confirm no employee exceeds it.

**Out of scope, not required PR evidence:** this step originally also asked to repeat generation with `use_local: false` (AI mode) against a live Anthropic call. That was ruled out of scope for this feature's PR evidence — this branch is based on `main`, which already hardcodes a retired model id (`claude-sonnet-4-20250514` at `backend/scheduling/nodes.py:227`), a pre-existing problem unrelated to scheduling preferences. Fixing it is separate work; blocking this PR's evidence on a live AI-mode run through a broken model pin would conflate the two. The AI path's preference logic (prompt filtering, post-generation trim) is covered by the unit tests in Task 6, which do not call the LLM.

- [x] **Step 6: Full suite and frontend gates**

```bash
backend/.venv/bin/python -m pytest tests/ -q
cd frontend && npx tsc --noEmit && npm run build
```

- [ ] **Step 7: Record the evidence in the PR description**

Include the baseline-vs-preference schedules from Steps 2–4 and the suite count. "Leaves a shift VACANT" is correct-but-alarming behaviour; showing it deliberately in the PR is what stops a reviewer filing it as a bug.

---

## Self-Review

**Spec coverage.** Data model → Task 1. Shared eligibility builder → Task 2. The 50% threshold → Task 3. Hard filter and soft score → Task 4. Deterministic path → Task 5. AI path including the post-hoc cap trim → Task 6. API, ungated → Task 7. Pages, slider, 1.0 warning, 50% sentence, sidebar rows, 19 locales → Task 8. The no-op regression, threshold boundaries, VACANT, the structural guard, multi-tenancy and DB constraints all have named tests (Tasks 1, 2, 3, 4, 5, 7). The spec's "out of scope" items — approved-schedule editing, employee self-service, per-role defaults, affinity changes — have no tasks, correctly.

**Placeholder scan.** No TBDs. Tasks 5 Steps 5–6, 6 Step 4, 7 Step 3 and all of Task 8 describe changes prose-first rather than as literal diffs, because they modify long functions whose surrounding lines will have moved by the time they are reached; each names the exact file, function and behaviour, and each is gated by a test written earlier in the same task that fails until it is right.

**Type consistency.** `eligible_for_slot` is defined in Task 2 with five positional parameters and extended in Task 5 with two optional keyword parameters — existing callers keep working, which is what makes Task 2's "no behaviour change" claim verifiable. `preference_score` and `blocked_by_hard_preference` share one signature `(emp, day_index, start, end, range_counts)` across Tasks 4, 5 and 6. `range_counts` is keyed `(employee_id, range_start, range_end)` in Task 4's tests and in Task 5's tracking. `PREFERENCE_PENALTY = 50.0` matches `_affinity_score`'s existing `level * 50` scale, so the terms compose without rescaling. `_DAY_INDEX` (local_scheduler) and `_DAY_INDEX_FOR_PROMPT` (prompts) are deliberately separate constants in separate modules with identical contents, avoiding an import cycle.

**One known gap, stated rather than hidden.** The frequency cap is pre-filtered in deterministic mode but trimmed post-generation in AI mode, because a whole week generates in a single call and per-slot counts do not exist beforehand. The delivered schedule never exceeds a cap either way; the mechanism differs. This is carried from the spec, not introduced here.
