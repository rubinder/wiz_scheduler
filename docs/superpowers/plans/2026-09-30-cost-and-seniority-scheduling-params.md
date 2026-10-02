# Cost & Seniority Scheduling Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pay rate, overtime avoidance, and two-part seniority as opt-in soft scoring signals that both scheduling paths (deterministic `local_scheduler.py` and the AI/LangGraph path) use when picking who fills a slot.

**Architecture:** New nullable columns on `Employee`/`Company`/`Location`. A new pure-function module `backend/scheduling/cost_seniority.py` computes the three scores. `eligible_for_slot` (the one shared eligibility gate both paths already call) attaches `_cost_score`/`_seniority_score` to every candidate, exactly like it already attaches `_skill` — so both paths inherit identical values with no duplicated normalization logic. Overtime is scored separately at each path's per-assignment picking step, since it depends on hours committed so far in this run, which only exists at assignment time. Pay rate and overtime fields are paid-plan gated at the write (API) layer only; seniority is free.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x (Async), Alembic, pytest/pytest-asyncio, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-30-cost-and-seniority-scheduling-params-design.md`

## Global Constraints

- All new columns nullable, default NULL. A company that sets nothing gets byte-identical scheduling output to today — enforced by explicit tests in every task that touches scoring or rendering.
- Pay rate and overtime fields (`pay_rate`, `overtime_threshold_hours`, `overtime_premium_multiplier`) are paid-plan gated via `assert_paid_plan(db, company_id, "cost_aware_scheduling")` at the API write layer. `hire_date` and `seniority_rank` are ungated (free).
- Cost signal reaching the AI prompt is always a 0-1 per-slot relative score — never a raw dollar amount.
- No role name string literal outside `seed.py`. No new dependencies. Type hints throughout Python code.
- `hire_date` comparisons use plain `date` objects; nothing in this feature touches "today" or UTC-now logic, so the UTC-today convention doesn't apply here.

---

### Task 1: Data model — nullable columns + migration

**Files:**
- Modify: `backend/models/employee.py`
- Modify: `backend/models/company.py`
- Modify: `backend/models/location.py`
- Create: `backend/alembic/versions/0038_add_cost_and_seniority_fields.py`
- Test: `tests/test_models_cost_seniority.py`

**Interfaces:**
- Produces: `Employee.pay_rate: float | None`, `Employee.hire_date: date | None`, `Employee.seniority_rank: int | None`; `Company.overtime_threshold_hours: float | None`, `Company.overtime_premium_multiplier: float | None`; `Location.overtime_threshold_hours: float | None`, `Location.overtime_premium_multiplier: float | None`. Later tasks read these as plain ORM attributes.

- [ ] **Step 1: Write the failing model tests**

Create `tests/test_models_cost_seniority.py`:

```python
"""New nullable cost/seniority columns default NULL and round-trip."""
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location


@pytest.mark.asyncio
async def test_employee_cost_seniority_fields_default_null(
    db_session: AsyncSession, seed_company, seed_location
):
    emp = Employee(company_id=seed_company.id, full_name="Dana Okafor")
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    assert emp.pay_rate is None
    assert emp.hire_date is None
    assert emp.seniority_rank is None


@pytest.mark.asyncio
async def test_employee_cost_seniority_fields_can_be_set(
    db_session: AsyncSession, seed_company, seed_location
):
    emp = Employee(
        company_id=seed_company.id, full_name="Dana Okafor",
        pay_rate=24.50, hire_date=date(2022, 3, 1), seniority_rank=1,
    )
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    assert emp.pay_rate == 24.50
    assert emp.hire_date == date(2022, 3, 1)
    assert emp.seniority_rank == 1


@pytest.mark.asyncio
async def test_company_overtime_fields_default_null(
    db_session: AsyncSession, seed_company
):
    await db_session.refresh(seed_company)
    assert seed_company.overtime_threshold_hours is None
    assert seed_company.overtime_premium_multiplier is None


@pytest.mark.asyncio
async def test_location_overtime_fields_can_be_set(
    db_session: AsyncSession, seed_location
):
    seed_location.overtime_threshold_hours = 35.0
    seed_location.overtime_premium_multiplier = 2.0
    await db_session.commit()
    await db_session.refresh(seed_location)
    assert seed_location.overtime_threshold_hours == 35.0
    assert seed_location.overtime_premium_multiplier == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models_cost_seniority.py -v`
Expected: FAIL — `TypeError: 'pay_rate' is an invalid keyword argument for Employee` (and similar for the other new fields/attributes not existing yet).

- [ ] **Step 3: Add the columns to `Employee`**

In `backend/models/employee.py`, the imports already include `CheckConstraint, Date, Numeric, SmallInteger` — no import changes needed. Add after the existing `max_hours_per_week` column (line 32):

```python
    # Cost/seniority scheduling signals (#134). All nullable — NULL means the
    # manager hasn't opted this employee into the corresponding soft-weight
    # scoring term. pay_rate is paid-plan gated at the API write layer
    # (backend/routers/employees.py); hire_date/seniority_rank are free.
    pay_rate: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    seniority_rank: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("seniority_rank > 0", name="ck_employees_seniority_rank"),
    )
```

- [ ] **Step 4: Add the columns to `Company`**

In `backend/models/company.py`, change the import line to add `CheckConstraint` and `Float`:

```python
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, text
```

Add after `external_id` (end of class):

```python
    # Overtime scheduling defaults for this company (#134). NULL = fall back
    # to the next level down (location, then a 40h/1.5x code default) — see
    # backend/scheduling/cost_seniority.resolve_overtime_threshold. Paid-plan
    # gated at the API write layer.
    overtime_threshold_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    overtime_premium_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
            name="ck_companies_overtime_premium_multiplier",
        ),
    )
```

- [ ] **Step 5: Add the columns to `Location`**

In `backend/models/location.py`, change the import line to add `CheckConstraint`:

```python
from sqlalchemy import CheckConstraint, Float, ForeignKey, String
```

Add after `min_rest_hours` (end of class):

```python
    # Per-location override of the company default. NULL = inherit.
    overtime_threshold_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    overtime_premium_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
            name="ck_locations_overtime_premium_multiplier",
        ),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_models_cost_seniority.py -v`
Expected: PASS (all 4 tests). Tests run against the in-memory SQLite schema built by `tests/conftest.py`'s `Base.metadata.create_all`, so this does not require the Alembic migration to exist.

- [ ] **Step 7: Write the Alembic migration**

Create `backend/alembic/versions/0038_add_cost_and_seniority_fields.py`:

```python
"""add cost and seniority scheduling fields

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-30 00:00:00.000000

Pay rate, overtime threshold/multiplier, and seniority as opt-in scheduling
signals (#134). All columns nullable, no backfill — NULL is the "manager
hasn't opted in" state and must produce unchanged scheduling behavior.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("pay_rate", sa.Numeric(8, 2), nullable=True))
    op.add_column("employees", sa.Column("hire_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("seniority_rank", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "ck_employees_seniority_rank", "employees", "seniority_rank > 0"
    )

    op.add_column("companies", sa.Column("overtime_threshold_hours", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("overtime_premium_multiplier", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_companies_overtime_premium_multiplier", "companies",
        "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
    )

    op.add_column("locations", sa.Column("overtime_threshold_hours", sa.Float(), nullable=True))
    op.add_column("locations", sa.Column("overtime_premium_multiplier", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_locations_overtime_premium_multiplier", "locations",
        "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_locations_overtime_premium_multiplier", "locations", type_="check")
    op.drop_column("locations", "overtime_premium_multiplier")
    op.drop_column("locations", "overtime_threshold_hours")

    op.drop_constraint("ck_companies_overtime_premium_multiplier", "companies", type_="check")
    op.drop_column("companies", "overtime_premium_multiplier")
    op.drop_column("companies", "overtime_threshold_hours")

    op.drop_constraint("ck_employees_seniority_rank", "employees", type_="check")
    op.drop_column("employees", "seniority_rank")
    op.drop_column("employees", "hire_date")
    op.drop_column("employees", "pay_rate")
```

- [ ] **Step 8: Commit**

```bash
git add backend/models/employee.py backend/models/company.py backend/models/location.py \
  backend/alembic/versions/0038_add_cost_and_seniority_fields.py \
  tests/test_models_cost_seniority.py
git commit -m "feat(models): add pay rate, overtime, and seniority columns (#134)"
```

---

### Task 2: `cost_seniority.py` — pure scoring functions

**Files:**
- Create: `backend/scheduling/cost_seniority.py`
- Test: `tests/test_cost_seniority.py`

**Interfaces:**
- Consumes: nothing (pure functions over plain dicts/floats).
- Produces: `DEFAULT_OVERTIME_THRESHOLD_HOURS: float`, `DEFAULT_OVERTIME_PREMIUM_MULTIPLIER: float`, `COST_WEIGHT: float`, `SENIORITY_WEIGHT: float`, `resolve_overtime_threshold(company_threshold, location_threshold) -> float`, `resolve_overtime_multiplier(company_multiplier, location_multiplier) -> float`, `cost_score(emp: dict, pool: list[dict]) -> float`, `resolve_seniority_ranks(pool: list[dict]) -> dict[str, float]`, `seniority_score(emp: dict, resolved_ranks: dict[str, float]) -> float`, `overtime_score(hours_committed: float, shift_duration_hrs: float, threshold: float) -> float`. Tasks 3-5 import all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_seniority.py`:

```python
"""Pure scoring functions for cost, seniority, and overtime (#134).

Lower score = more preferred, matching preferences.preference_score and
local_scheduler._affinity_score. 0.0 is always the "no data configured"
result, which is what keeps this feature additive.
"""
from backend.scheduling.cost_seniority import (
    DEFAULT_OVERTIME_THRESHOLD_HOURS,
    DEFAULT_OVERTIME_PREMIUM_MULTIPLIER,
    cost_score,
    overtime_score,
    resolve_overtime_multiplier,
    resolve_overtime_threshold,
    resolve_seniority_ranks,
    seniority_score,
)


def test_cost_score_none_when_pay_rate_unset():
    assert cost_score({"id": "e1", "pay_rate": None}, [{"id": "e1", "pay_rate": None}]) == 0.0


def test_cost_score_half_when_only_one_rated_candidate():
    pool = [{"id": "e1", "pay_rate": 20.0}]
    assert cost_score(pool[0], pool) == 0.5


def test_cost_score_half_when_all_rates_equal():
    pool = [{"id": "e1", "pay_rate": 20.0}, {"id": "e2", "pay_rate": 20.0}]
    assert cost_score(pool[0], pool) == 0.5
    assert cost_score(pool[1], pool) == 0.5


def test_cost_score_min_max_normalized():
    pool = [{"id": "e1", "pay_rate": 10.0}, {"id": "e2", "pay_rate": 30.0}]
    assert cost_score(pool[0], pool) == 0.0
    assert cost_score(pool[1], pool) == 1.0


def test_cost_score_unrated_employee_excluded_from_range():
    pool = [
        {"id": "e1", "pay_rate": 10.0},
        {"id": "e2", "pay_rate": 30.0},
        {"id": "e3", "pay_rate": None},
    ]
    assert cost_score(pool[2], pool) == 0.0
    assert cost_score(pool[0], pool) == 0.0
    assert cost_score(pool[1], pool) == 1.0


def test_resolve_seniority_ranks_manual_wins_over_hire_date():
    from datetime import date
    pool = [
        {"id": "e1", "seniority_rank": None, "hire_date": date(2020, 1, 1)},
        {"id": "e2", "seniority_rank": 1, "hire_date": date(2024, 1, 1)},
    ]
    ranks = resolve_seniority_ranks(pool)
    assert ranks["e2"] == 1.0
    assert ranks["e1"] == 1.0  # earliest (only) hire_date among the non-manual group


def test_resolve_seniority_ranks_hire_date_order():
    from datetime import date
    pool = [
        {"id": "e1", "seniority_rank": None, "hire_date": date(2022, 1, 1)},
        {"id": "e2", "seniority_rank": None, "hire_date": date(2020, 1, 1)},
    ]
    ranks = resolve_seniority_ranks(pool)
    assert ranks["e2"] == 1.0
    assert ranks["e1"] == 2.0


def test_resolve_seniority_ranks_excludes_employees_with_neither_field():
    pool = [{"id": "e1", "seniority_rank": None, "hire_date": None}]
    assert resolve_seniority_ranks(pool) == {}


def test_seniority_score_zero_when_no_resolved_rank():
    assert seniority_score({"id": "e1"}, {}) == 0.0


def test_seniority_score_min_max_normalized():
    ranks = {"e1": 1.0, "e2": 3.0}
    assert seniority_score({"id": "e1"}, ranks) == 0.0
    assert seniority_score({"id": "e2"}, ranks) == 1.0


def test_overtime_score_zero_under_threshold():
    assert overtime_score(hours_committed=30.0, shift_duration_hrs=8.0, threshold=40.0) == 0.0


def test_overtime_score_zero_exactly_at_threshold():
    assert overtime_score(hours_committed=32.0, shift_duration_hrs=8.0, threshold=40.0) == 0.0


def test_overtime_score_positive_and_scaled_over_threshold():
    low = overtime_score(hours_committed=36.0, shift_duration_hrs=8.0, threshold=40.0)
    high = overtime_score(hours_committed=40.0, shift_duration_hrs=8.0, threshold=40.0)
    assert low > 0.0
    assert high > low


def test_resolve_overtime_threshold_location_beats_company_beats_default():
    assert resolve_overtime_threshold(None, None) == DEFAULT_OVERTIME_THRESHOLD_HOURS
    assert resolve_overtime_threshold(35.0, None) == 35.0
    assert resolve_overtime_threshold(35.0, 30.0) == 30.0


def test_resolve_overtime_multiplier_location_beats_company_beats_default():
    assert resolve_overtime_multiplier(None, None) == DEFAULT_OVERTIME_PREMIUM_MULTIPLIER
    assert resolve_overtime_multiplier(2.0, None) == 2.0
    assert resolve_overtime_multiplier(2.0, 1.8) == 1.8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cost_seniority.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.scheduling.cost_seniority'`.

- [ ] **Step 3: Write the implementation**

Create `backend/scheduling/cost_seniority.py`:

```python
"""Cost and seniority scoring for scheduling (#134).

Both scheduling paths — local_scheduler.py's deterministic picker and
prompts.py's AI-path candidate ordering/rendering — consume these functions
so the two paths score identically. Mirrors preferences.py's conventions:
plain Dict[str, Any] employee representations, lower score = more preferred,
and 0.0 for "no data configured", which is what keeps this feature additive
(a company that sets nothing gets unchanged scheduling behavior).
"""
from typing import Any, Dict, List

DEFAULT_OVERTIME_THRESHOLD_HOURS = 40.0
DEFAULT_OVERTIME_PREMIUM_MULTIPLIER = 1.5

# Same points-scale convention as preferences.PREFERENCE_PENALTY (50.0) and
# _affinity_score's +/-50: cost and seniority are tie-break nudges here, so
# their weights stay well under that scale rather than dominating it.
COST_WEIGHT = 20.0
SENIORITY_WEIGHT = 10.0
OVERTIME_PENALTY_PER_HOUR = 15.0


def resolve_overtime_threshold(
    company_threshold: float | None, location_threshold: float | None
) -> float:
    """Location override beats company default beats the 40h constant."""
    if location_threshold is not None:
        return float(location_threshold)
    if company_threshold is not None:
        return float(company_threshold)
    return DEFAULT_OVERTIME_THRESHOLD_HOURS


def resolve_overtime_multiplier(
    company_multiplier: float | None, location_multiplier: float | None
) -> float:
    """Location override beats company default beats the 1.5x constant."""
    if location_multiplier is not None:
        return float(location_multiplier)
    if company_multiplier is not None:
        return float(company_multiplier)
    return DEFAULT_OVERTIME_PREMIUM_MULTIPLIER


def cost_score(emp: Dict[str, Any], pool: List[Dict[str, Any]]) -> float:
    """0-1 min-max normalized pay_rate among *pool* members with pay_rate set.

    0.0 = cheapest rated candidate, 1.0 = most expensive, 0.5 when every
    rated candidate shares the same rate (no discriminating information).
    Returns 0.0 when this employee has no pay_rate configured — opt-in per
    employee, not just per company.
    """
    rate = emp.get("pay_rate")
    if rate is None:
        return 0.0
    rates = [e["pay_rate"] for e in pool if e.get("pay_rate") is not None]
    if len(rates) < 2:
        return 0.5
    lo, hi = min(rates), max(rates)
    if hi == lo:
        return 0.5
    return (float(rate) - lo) / (float(hi) - float(lo))


def resolve_seniority_ranks(pool: List[Dict[str, Any]]) -> Dict[str, float]:
    """Resolved seniority rank per employee id in *pool*.

    A manual `seniority_rank` wins when set; otherwise rank is derived from
    `hire_date` (1 = earliest hire_date) among the remaining employees.
    Employees with neither field set are omitted from the result.
    """
    resolved: Dict[str, float] = {}
    derive_from: List[Dict[str, Any]] = []
    for e in pool:
        eid = str(e["id"])
        if e.get("seniority_rank") is not None:
            resolved[eid] = float(e["seniority_rank"])
        elif e.get("hire_date") is not None:
            derive_from.append(e)
    for i, e in enumerate(sorted(derive_from, key=lambda x: x["hire_date"]), start=1):
        resolved[str(e["id"])] = float(i)
    return resolved


def seniority_score(emp: Dict[str, Any], resolved_ranks: Dict[str, float]) -> float:
    """0-1 min-max normalized resolved rank among *resolved_ranks*.

    0.0 = most senior present, 0.5 when every ranked candidate ties, 0.0 when
    this employee has no resolved rank.
    """
    eid = str(emp["id"])
    if eid not in resolved_ranks:
        return 0.0
    ranks = list(resolved_ranks.values())
    if len(ranks) < 2:
        return 0.5
    lo, hi = min(ranks), max(ranks)
    if hi == lo:
        return 0.5
    return (resolved_ranks[eid] - lo) / (hi - lo)


def overtime_score(
    hours_committed: float, shift_duration_hrs: float, threshold: float
) -> float:
    """Penalty proportional to how far past *threshold* this shift would push
    the employee. 0.0 when the projected total stays at or under threshold —
    this is a soft nudge, never a filter."""
    projected = hours_committed + shift_duration_hrs
    over = projected - threshold
    if over <= 0:
        return 0.0
    return over * OVERTIME_PENALTY_PER_HOUR
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cost_seniority.py -v`
Expected: PASS (all 15 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/cost_seniority.py tests/test_cost_seniority.py
git commit -m "feat(scheduling): add pure cost/seniority/overtime scoring functions (#134)"
```

---

### Task 3: Attach `_cost_score`/`_seniority_score` in the shared eligibility gate

**Files:**
- Modify: `backend/scheduling/prompts.py` (`eligible_for_slot`, lines 100-161)
- Test: `tests/test_cost_seniority_eligibility.py`

**Interfaces:**
- Consumes: `cost_score`, `resolve_seniority_ranks`, `seniority_score` from Task 2's `backend/scheduling/cost_seniority`.
- Produces: `eligible_for_slot(...)` return dicts now always carry `_cost_score: float` and `_seniority_score: float` alongside the existing `_skill: int`. Both `local_scheduler._build_eligible_map` and `prompts.build_schedule_prompt` call this function already, so they inherit these keys with no changes of their own required for this task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_seniority_eligibility.py`:

```python
"""eligible_for_slot attaches _cost_score/_seniority_score identically for
both scheduling paths — the same way it already attaches _skill (#134)."""
from backend.scheduling.prompts import eligible_for_slot


def _prepared(eid, **overrides):
    base = {
        "id": eid,
        "_role_names": {"Floor"},
        "_day_windows": {"Monday": [("09:00", "17:00")]},
        "roles": [{"role_name": "Floor", "skill_level": 1}],
        "day_blackouts": [],
        "pay_rate": None,
        "hire_date": None,
        "seniority_rank": None,
    }
    base.update(overrides)
    return base


def test_no_data_configured_scores_are_zero():
    pool = [_prepared("e1"), _prepared("e2")]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    assert {c["_cost_score"] for c in result} == {0.0}
    assert {c["_seniority_score"] for c in result} == {0.0}


def test_cost_score_normalized_across_eligible_pool():
    pool = [_prepared("e1", pay_rate=10.0), _prepared("e2", pay_rate=30.0)]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    scores = {c["id"]: c["_cost_score"] for c in result}
    assert scores["e1"] == 0.0
    assert scores["e2"] == 1.0


def test_cost_score_normalized_within_this_slot_only():
    """A third, ineligible candidate (wrong role) must not affect normalization."""
    pool = [
        _prepared("e1", pay_rate=10.0),
        _prepared("e2", pay_rate=30.0),
        _prepared("e3", pay_rate=20.0, _role_names={"Lead"}),
    ]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    ids = {c["id"] for c in result}
    assert ids == {"e1", "e2"}


def test_seniority_score_derived_from_hire_date():
    from datetime import date
    pool = [
        _prepared("e_junior", hire_date=date(2024, 1, 1)),
        _prepared("e_senior", hire_date=date(2020, 1, 1)),
    ]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    scores = {c["id"]: c["_seniority_score"] for c in result}
    assert scores["e_senior"] == 0.0
    assert scores["e_junior"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cost_seniority_eligibility.py -v`
Expected: FAIL with `KeyError: '_cost_score'`.

- [ ] **Step 3: Modify `eligible_for_slot`**

In `backend/scheduling/prompts.py`, add the import (line 4):

```python
from backend.scheduling.cost_seniority import cost_score, resolve_seniority_ranks, seniority_score
from backend.scheduling.preferences import blocked_by_hard_preference, preference_score
```

Replace the body of `eligible_for_slot` (lines 138-161) — the filtering loop stays identical, only the return construction changes:

```python
    filtered: List[Dict[str, Any]] = []
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
        if day_index is not None:
            if blocked_by_hard_preference(e, day_index, start, end, range_counts or {}):
                continue
        skill = next(
            (
                r.get("skill_level", 0)
                for r in e.get("roles", [])
                if r.get("role_name") == role_name
            ),
            0,
        )
        filtered.append({**e, "_skill": skill})

    # Cost and seniority are normalized against this slot's own eligible
    # pool (not the whole roster), so what a candidate's score expresses is
    # "how does this person compare to the others who could actually fill
    # this slot" — see cost_seniority.cost_score/seniority_score.
    resolved_ranks = resolve_seniority_ranks(filtered)
    return [
        {
            **e,
            "_cost_score": cost_score(e, filtered),
            "_seniority_score": seniority_score(e, resolved_ranks),
        }
        for e in filtered
    ]
```

Also update the docstring line `"Each returned dict is the input dict plus \`_skill\` for the requested role."` to read:

```
    Each returned dict is the input dict plus `_skill`, `_cost_score`, and
    `_seniority_score` (see backend.scheduling.cost_seniority), all computed
    once here so both scheduling paths see identical values.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cost_seniority_eligibility.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Run the full existing scheduling test suite to confirm no regression**

Run: `pytest tests/test_eligibility_shared.py tests/test_preferences_local_scheduler.py tests/test_preferences_ai_path.py tests/test_local_scheduler.py -v`
Expected: PASS, unchanged — `eligible_for_slot`'s existing callers don't read the new keys yet, so behavior is identical.

- [ ] **Step 6: Commit**

```bash
git add backend/scheduling/prompts.py tests/test_cost_seniority_eligibility.py
git commit -m "feat(scheduling): attach cost/seniority scores in the shared eligibility gate (#134)"
```

---

### Task 4: Wire overtime + cost/seniority scoring into the deterministic scheduler

**Files:**
- Modify: `backend/scheduling/local_scheduler.py`
- Test: `tests/test_local_scheduler.py` (extend)

**Interfaces:**
- Consumes: `COST_WEIGHT`, `SENIORITY_WEIGHT`, `DEFAULT_OVERTIME_THRESHOLD_HOURS`, `overtime_score` from `backend.scheduling.cost_seniority`. `_cost_score`/`_seniority_score` already present on every candidate dict via Task 3.
- Produces: `_pick_employee(..., overtime_threshold: float | None = None)` — a new optional kwarg. `local_schedule` resolves `location.get("overtime_threshold_hours")` and passes it through.

- [ ] **Step 1: Write the failing tests**

In `tests/test_local_scheduler.py`, extend the `_make_employee` helper (near the top) to accept the three new optional fields:

```python
def _make_employee(
    eid: str, name: str, roles: list[dict], location_id: str,
    windows: list[dict] | None = None,
    pay_rate: float | None = None,
    hire_date=None,
    seniority_rank: int | None = None,
):
    """Build an employee dict matching the shape used in SchedulingState."""
    return {
        "id": eid,
        "full_name": name,
        "email": f"{name.lower().replace(' ', '.')}@test.com",
        "location_ids": [location_id],
        "roles": roles,
        "affinities": [],
        "available_windows": windows or [],
        "pay_rate": pay_rate,
        "hire_date": hire_date,
        "seniority_rank": seniority_rank,
    }
```

Append a new test class at the end of the file:

```python
class TestCostSeniorityOvertimeScoring:
    """#134: cost, seniority, and overtime as soft tie-break signals."""

    def test_cheaper_employee_preferred_when_otherwise_equal(self):
        cheap = _make_employee("e001", "Cheap Carla", [ROLE_FLOOR], "loc00001", [MON_9_17], pay_rate=10.0)
        pricey = _make_employee("e002", "Pricey Pat", [ROLE_FLOOR], "loc00001", [MON_9_17], pay_rate=30.0)
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([cheap, pricey], schedule)

        result = local_schedule(state, strategy="rotation")

        assert len(result["current_parsed_shifts"]) == 1
        assert result["current_parsed_shifts"][0]["employee_id"] == "e001"

    def test_more_senior_employee_preferred_via_hire_date(self):
        from datetime import date
        junior = _make_employee("e001", "Junior", [ROLE_FLOOR], "loc00001", [MON_9_17], hire_date=date(2024, 1, 1))
        senior = _make_employee("e002", "Senior", [ROLE_FLOOR], "loc00001", [MON_9_17], hire_date=date(2018, 1, 1))
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([junior, senior], schedule)

        result = local_schedule(state, strategy="rotation")

        assert result["current_parsed_shifts"][0]["employee_id"] == "e002"

    def test_manual_seniority_rank_overrides_hire_date(self):
        from datetime import date
        # e001 hired more recently but manually ranked as more senior (rank 1).
        manual_senior = _make_employee("e001", "Manual Senior", [ROLE_FLOOR], "loc00001", [MON_9_17], hire_date=date(2024, 1, 1), seniority_rank=1)
        by_tenure = _make_employee("e002", "By Tenure", [ROLE_FLOOR], "loc00001", [MON_9_17], hire_date=date(2018, 1, 1))
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([manual_senior, by_tenure], schedule)

        result = local_schedule(state, strategy="rotation")

        assert result["current_parsed_shifts"][0]["employee_id"] == "e001"

    def test_overtime_penalized_when_projected_hours_exceed_threshold(self):
        near_cap = _make_employee("e001", "Near Cap", [ROLE_FLOOR], "loc00001", [MON_9_17])
        fresh = _make_employee("e002", "Fresh", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([near_cap, fresh], schedule)
        state["current_location"]["overtime_threshold_hours"] = 40.0
        # e001 already has 38h committed this run; this 8h shift would push
        # them to 46h (6h over). e002 starts fresh: 0 -> 8h, no penalty.
        state["employee_weekly_hours_draft"] = {"e001": 38.0}

        result = local_schedule(state, strategy="rotation")

        assert result["current_parsed_shifts"][0]["employee_id"] == "e002"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_local_scheduler.py::TestCostSeniorityOvertimeScoring -v`
Expected: FAIL — all four assertions fail because `_pick_employee` doesn't yet read `_cost_score`/`_seniority_score` or apply an overtime penalty, so ties are broken randomly (or by the earlier-declared candidate) rather than by these signals.

- [ ] **Step 3: Wire the scoring into `local_scheduler.py`**

Add the import near the top (after the `preferences` import, around line 26):

```python
from backend.scheduling.cost_seniority import (
    COST_WEIGHT,
    DEFAULT_OVERTIME_THRESHOLD_HOURS,
    SENIORITY_WEIGHT,
    overtime_score,
)
```

In `local_schedule`, next to where `min_rest_hours` is read (line 178), add:

```python
    # Configurable per company/location (Location overrides Company, both
    # NULL falls back to the 40h constant) -- resolved once per location run,
    # same pattern as min_rest_hours above.
    overtime_threshold = location.get("overtime_threshold_hours")
```

In the `_pick_employee(...)` call site (lines 277-289), add the new kwarg:

```python
                chosen = _pick_employee(
                    available, role_name, role_fill_counts, strategy,
                    current_coworkers, affinity_lookup,
                    history_minutes=history_minutes,
                    strategy_param=strategy_param,
                    strategy_param2=strategy_param2,
                    employee_hours=employee_hours,
                    shift_duration_hrs=slot_duration,
                    day_index=_DAY_INDEX.get(day),
                    start=start_hm,
                    end=end_hm,
                    range_counts=range_counts,
                    overtime_threshold=overtime_threshold,
                )
```

Add the new parameter to `_pick_employee`'s signature (after `range_counts`, line 463):

```python
    range_counts: Dict[Any, int] | None = None,
    overtime_threshold: float | None = None,
) -> Dict[str, Any] | None:
```

In each of the four scoring branches (`random`, `rotation`, `rotation_history`, `max_hours`), add the three new terms right after the existing `pref` line and fold them into `score`. For the `random` branch (lines 500-517):

```python
    if strategy == "random":
        # Compute affinity-adjusted opportunity cost, pick from best tier
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp = e.get("_num_required_roles", 99)
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            pref = (
                preference_score(e, day_index, start, end, range_counts)
                if day_index is not None else 0.0
            )
            cost = e.get("_cost_score", 0.0) * COST_WEIGHT
            sen = e.get("_seniority_score", 0.0) * SENIORITY_WEIGHT
            ot = overtime_score(
                (employee_hours or {}).get(eid, 0.0), shift_duration_hrs,
                overtime_threshold if overtime_threshold is not None else DEFAULT_OVERTIME_THRESHOLD_HOURS,
            )
            score = opp * 100 + aff + pref + cost + sen + ot
            scored.append((score, e))
```

For `rotation` (lines 519-536):

```python
    elif strategy == "rotation":
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp_cost = e.get("_num_required_roles", 99)
            fills = role_fill_counts.get((eid, role_name), 0)
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            pref = (
                preference_score(e, day_index, start, end, range_counts)
                if day_index is not None else 0.0
            )
            cost = e.get("_cost_score", 0.0) * COST_WEIGHT
            sen = e.get("_seniority_score", 0.0) * SENIORITY_WEIGHT
            ot = overtime_score(
                (employee_hours or {}).get(eid, 0.0), shift_duration_hrs,
                overtime_threshold if overtime_threshold is not None else DEFAULT_OVERTIME_THRESHOLD_HOURS,
            )
            score = opp_cost * 100 + fills * 10 - e.get("_skill", 0) + aff + pref + cost + sen + ot
            scored.append((score, e))
```

For `rotation_history` (lines 538-556):

```python
    elif strategy == "rotation_history":
        # strategy_param: 1.0 = always pick fewest-hours, 0.0 = essentially random
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp_cost = e.get("_num_required_roles", 99)
            fills = role_fill_counts.get((eid, role_name), 0)
            hist_mins = (history_minutes or {}).get((eid, role_name), 0.0)
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            # History component: scale minutes to a reasonable range (0-1000 points)
            # Higher minutes = higher penalty
            history_penalty = hist_mins / 60.0  # convert to hours for scaling
            pref = (
                preference_score(e, day_index, start, end, range_counts)
                if day_index is not None else 0.0
            )
            cost = e.get("_cost_score", 0.0) * COST_WEIGHT
            sen = e.get("_seniority_score", 0.0) * SENIORITY_WEIGHT
            ot = overtime_score(
                (employee_hours or {}).get(eid, 0.0), shift_duration_hrs,
                overtime_threshold if overtime_threshold is not None else DEFAULT_OVERTIME_THRESHOLD_HOURS,
            )
            # Blend between random (opp_cost only) and full history consideration
            score = opp_cost * 100 + fills * 10 - e.get("_skill", 0) + aff + pref + cost + sen + ot + (history_penalty * strategy_param * 10)
            scored.append((score, e))
```

For `max_hours` (lines 586-608), add the three terms into the existing `score` line:

```python
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp_cost = e.get("_num_required_roles", 99)
            current_hrs = emp_hrs.get(eid, 0.0)
            projected_hrs = current_hrs + shift_duration_hrs
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            pref = (
                preference_score(e, day_index, start, end, range_counts)
                if day_index is not None else 0.0
            )
            cost = e.get("_cost_score", 0.0) * COST_WEIGHT
            sen = e.get("_seniority_score", 0.0) * SENIORITY_WEIGHT
            ot = overtime_score(
                current_hrs, shift_duration_hrs,
                overtime_threshold if overtime_threshold is not None else DEFAULT_OVERTIME_THRESHOLD_HOURS,
            )

            # Penalty for being near/over the cap, scaled by strictness
            if projected_hrs > max_hrs:
                over_penalty = (projected_hrs - max_hrs) * strictness * 200
            else:
                over_penalty = 0.0

            # Prefer employees with fewer hours (spread the load)
            hours_penalty = current_hrs * strictness * 5

            score = opp_cost * 100 - e.get("_skill", 0) + aff + pref + cost + sen + ot + over_penalty + hours_penalty
            scored.append((score, e))
```

Note: `max_hours`'s own `over_penalty`/`hours_penalty` are about that strategy's own configurable `strategy_param` cap — a separate, pre-existing concept from the new company/location `overtime_threshold_hours`. Both apply independently; this is intentional, not a duplication.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_local_scheduler.py::TestCostSeniorityOvertimeScoring -v`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Run the full existing local_scheduler suite to confirm no regression**

Run: `pytest tests/test_local_scheduler.py -v`
Expected: PASS, 100% unchanged. Every existing test builds employees via `_make_employee` (now defaulting the three new fields to `None`), which score `0.0` on all three new terms — behavior is identical to before this task.

- [ ] **Step 6: Commit**

```bash
git add backend/scheduling/local_scheduler.py tests/test_local_scheduler.py
git commit -m "feat(scheduling): wire cost/seniority/overtime into the deterministic scheduler (#134)"
```

---

### Task 5: Wire cost/seniority/overtime into the AI-path prompt

**Files:**
- Modify: `backend/scheduling/prompts.py` (`build_schedule_prompt`, and a new small helper)
- Test: `tests/test_cost_seniority_ai_path.py`

**Interfaces:**
- Consumes: `COST_WEIGHT`, `SENIORITY_WEIGHT`, `DEFAULT_OVERTIME_THRESHOLD_HOURS`, `overtime_score`, `resolve_seniority_ranks` from `backend.scheduling.cost_seniority`.
- Produces: `build_schedule_prompt(..., employee_hours_committed: Dict[str, float] | None = None)` — one new optional kwarg. Task 6's `nodes.build_prompt` will pass `state.get("employee_weekly_hours_draft", {})` for this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_seniority_ai_path.py`:

```python
"""Cost, seniority, and overtime reach the AI path via eligible_for_slot's
attached scores (Task 3) plus build_schedule_prompt's rendering (#134)."""
from datetime import date

from backend.scheduling.prompts import build_schedule_prompt

LOCATION = {"id": "loc1", "name": "Test Cafe", "timezone": "America/New_York"}
MON_9_17 = {"start": "2026-08-31T09:00:00+00:00", "end": "2026-08-31T17:00:00+00:00"}

SHIFT_TEMPLATE = {
    "id": "tmpl1",
    "name": "T",
    "weekly_schedule": {
        "Monday": [
            {"role_name": "Floor", "role_id": "role1",
             "start_time": "09:00", "end_time": "17:00", "headcount": 1}
        ],
    },
}


def _employee(eid, **overrides):
    base = {
        "id": eid,
        "full_name": eid,
        "email": f"{eid}@test.com",
        "location_ids": ["loc1"],
        "roles": [{"role_name": "Floor", "role_id": "role1", "skill_level": 1}],
        "affinities": [],
        "available_windows": [MON_9_17],
        "day_blackouts": [],
        "day_preferences": [],
        "hour_range_preferences": [],
        "hour_range_caps": [],
        "pay_rate": None,
        "hire_date": None,
        "seniority_rank": None,
    }
    base.update(overrides)
    return base


def _eligible_line(prompt: str) -> str:
    return next(line for line in prompt.splitlines() if line.strip().startswith("Eligible:"))


def test_no_data_configured_prompt_unchanged():
    employees = [_employee("e1"), _employee("e2")]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    assert "cost=" not in prompt
    assert "seniority" not in prompt
    assert "hours_committed" not in prompt


def test_cheaper_candidate_ordered_first_and_shown():
    employees = [_employee("e_pricey", pay_rate=30.0), _employee("e_cheap", pay_rate=10.0)]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    line = _eligible_line(prompt)
    assert line.index("e_cheap") < line.index("e_pricey")
    assert "cost=" in prompt


def test_more_senior_candidate_ordered_first_via_hire_date():
    employees = [
        _employee("e_junior", hire_date=date(2024, 1, 1)),
        _employee("e_senior", hire_date=date(2020, 1, 1)),
    ]
    prompt = build_schedule_prompt(LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31")
    line = _eligible_line(prompt)
    assert line.index("e_senior") < line.index("e_junior")
    assert "seniority_rank: 1" in prompt


def test_overtime_pushes_committed_employee_later_and_shows_hours():
    employees = [_employee("e_committed"), _employee("e_fresh")]
    prompt = build_schedule_prompt(
        LOCATION, SHIFT_TEMPLATE, employees, "2026-08-31",
        employee_hours_committed={"e_committed": 38.0},
    )
    line = _eligible_line(prompt)
    assert line.index("e_fresh") < line.index("e_committed")
    assert "hours_committed=38.0" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cost_seniority_ai_path.py -v`
Expected: FAIL — `build_schedule_prompt` doesn't accept `employee_hours_committed` yet, and none of the new prompt text exists.

- [ ] **Step 3: Add a local shift-duration helper**

In `backend/scheduling/prompts.py`, near `_DAY_INDEX_FOR_PROMPT` (after line 12), add — duplicated from `local_scheduler._shift_duration_hours` for the same reason `_DAY_INDEX_FOR_PROMPT` is duplicated (importing back from `local_scheduler.py` would be circular, since it already imports from this module):

```python
def _shift_duration_hours(start_hm: str, end_hm: str) -> float:
    """Calculate shift duration in hours from HH:MM strings."""
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if end_min <= start_min:
        end_min += 24 * 60  # overnight shift
    return (end_min - start_min) / 60.0
```

- [ ] **Step 4: Update the import line**

```python
from backend.scheduling.cost_seniority import (
    COST_WEIGHT,
    DEFAULT_OVERTIME_THRESHOLD_HOURS,
    SENIORITY_WEIGHT,
    cost_score,
    overtime_score,
    resolve_seniority_ranks,
    seniority_score,
)
from backend.scheduling.preferences import blocked_by_hard_preference, preference_score
```

- [ ] **Step 5: Add the `employee_hours_committed` parameter and resolve the threshold**

In `build_schedule_prompt`'s signature:

```python
def build_schedule_prompt(
    location: Dict[str, Any],
    shift_template: Dict[str, Any],
    employees: List[Dict[str, Any]],
    week_start_date: str,
    conflict_notes: str = "",
    num_days: int = 7,
    employee_hours_committed: Dict[str, float] | None = None,
) -> str:
```

Right after `tz_offset = _tz_offset_example(location["timezone"])`:

```python
    overtime_threshold = location.get("overtime_threshold_hours")
    if overtime_threshold is None:
        overtime_threshold = DEFAULT_OVERTIME_THRESHOLD_HOURS
    hours_committed_map = employee_hours_committed or {}
```

- [ ] **Step 6: Extend the sort key and rendered candidate line**

Replace the sort + render block (lines 249-259):

```python
            candidates.sort(key=lambda c: (
                (preference_score(c, day_index, start, end, {}) if day_index is not None else 0.0)
                + c.get("_cost_score", 0.0) * COST_WEIGHT
                + c.get("_seniority_score", 0.0) * SENIORITY_WEIGHT
                + overtime_score(
                    hours_committed_map.get(str(c["id"]), 0.0),
                    _shift_duration_hours(start, end),
                    overtime_threshold,
                )
            ))
            eligible = []
            for c in candidates:
                cid = str(c["id"])
                parts = [f"skill={c['_skill']}"]
                if c.get("pay_rate") is not None:
                    parts.append(f"cost={c['_cost_score']:.2f}")
                if c.get("seniority_rank") is not None or c.get("hire_date") is not None:
                    parts.append(f"seniority={c['_seniority_score']:.2f}")
                hours_committed = hours_committed_map.get(cid, 0.0)
                if hours_committed > 0:
                    parts.append(f"hours_committed={hours_committed:.1f}/threshold={overtime_threshold:.0f}")
                eligible.append(f'{cid} [{", ".join(parts)}]')
```

(The line below, `eligible_str = ", ".join(eligible) if eligible else "NONE AVAILABLE"`, stays unchanged.)

- [ ] **Step 7: Add seniority to the roster block**

Before the roster-building loop (`for e in emp_data:`, line 265), add:

```python
    roster_seniority_ranks = resolve_seniority_ranks(emp_data)
```

Inside the loop, extend the line construction:

```python
    for e in emp_data:
        affinities = e.get("affinities", [])
        aff_str = ""
        if affinities:
            aff_parts = [f'{a.get("target_id", "")}:{a.get("level", 0)}' for a in affinities]
            aff_str = f"\n  affinities: [{', '.join(aff_parts)}]"
        eid = str(e["id"])
        seniority_str = ""
        if eid in roster_seniority_ranks:
            seniority_str = f"\n  seniority_rank: {int(roster_seniority_ranks[eid])}"
        roster_lines.append(
            f"- {e['id']}\n"
            f"  roles: [{e['_roles_display']}]\n"
            f"  available: {e['_avail_display']}{aff_str}{seniority_str}"
        )
```

- [ ] **Step 8: Renumber the optional instruction rules and add the new one**

Replace the `preference_rule` block (lines 307-321) and its use in the prompt f-string with a dynamically-numbered `extra_rules` block, fixing a latent numbering gap along the way (today only one optional rule exists so it never surfaces, but a second one would produce a bare "8." with no "7."):

```python
    has_preferences = any(
        e.get("day_preferences") or e.get("hour_range_preferences") or e.get("hour_range_caps")
        for e in emp_data
    )
    has_cost_seniority_overtime = any(
        e.get("pay_rate") is not None
        or e.get("seniority_rank") is not None
        or e.get("hire_date") is not None
        for e in emp_data
    ) or any(v > 0 for v in hours_committed_map.values())

    extra_rules = ""
    next_rule_num = 7
    if has_preferences:
        extra_rules += (
            f"{next_rule_num}. The Eligible list is ordered BEST FIRST by employee scheduling\n"
            f"   preferences. Prefer earlier entries when candidates are otherwise\n"
            f"   equal.\n"
        )
        next_rule_num += 1
    if has_cost_seniority_overtime:
        extra_rules += (
            f"{next_rule_num}. Some Eligible entries show cost, seniority, and/or\n"
            f"   hours_committed/threshold. Prefer lower cost and higher seniority when\n"
            f"   candidates are otherwise equal, and avoid pushing an employee's\n"
            f"   hours_committed past their threshold when an alternative eligible\n"
            f"   employee can cover the slot.\n"
        )
        next_rule_num += 1
```

Then in the final prompt f-string, replace `f"{preference_rule}"` with `f"{extra_rules}"`.

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_cost_seniority_ai_path.py -v`
Expected: PASS (all 4 tests).

- [ ] **Step 10: Run the full existing prompts/preferences suite to confirm no regression**

Run: `pytest tests/test_preferences_ai_path.py tests/test_eligibility_shared.py -v`
Expected: PASS, unchanged.

- [ ] **Step 11: Commit**

```bash
git add backend/scheduling/prompts.py tests/test_cost_seniority_ai_path.py
git commit -m "feat(scheduling): render cost/seniority/overtime in the AI prompt (#134)"
```

---

### Task 6: Load the new fields into `SchedulingState` (graph.py + nodes.py)

**Files:**
- Modify: `backend/scheduling/graph.py`
- Modify: `backend/scheduling/nodes.py` (`build_prompt`)
- Test: `tests/test_schedule_pipeline.py` (extend)

**Interfaces:**
- Consumes: `resolve_overtime_threshold` from `backend.scheduling.cost_seniority`.
- Produces: every location dict in `state["locations"]` (and `state["current_location"]`) carries a resolved `overtime_threshold_hours: float` (never None — the 40.0 default is baked in here). Every employee dict carries `pay_rate`, `hire_date`, `seniority_rank`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_schedule_pipeline.py`:

```python
async def test_load_initial_state_resolves_overtime_threshold_and_loads_cost_fields(
    db_session, seed_company, seed_location,
):
    """Location override beats company default beats the 40h constant, and
    pay_rate/hire_date/seniority_rank load onto each employee dict (#134)."""
    from datetime import date
    from backend.models import Employee
    from backend.scheduling.graph import _load_initial_state

    seed_company.overtime_threshold_hours = 35.0
    emp = Employee(
        company_id=seed_company.id, full_name="Dana Okafor",
        location_ids=[seed_location.id],
        pay_rate=24.50, hire_date=date(2022, 3, 1), seniority_rank=2,
    )
    db_session.add(emp)
    await db_session.commit()

    state = await _load_initial_state(
        company_id=str(seed_company.id),
        week_start_date="2026-12-21",
        db=db_session,
        num_days=7,
    )

    loc = next(l for l in state["locations"] if l["id"] == str(seed_location.id))
    assert loc["overtime_threshold_hours"] == 35.0  # company default, no location override

    loaded_emp = next(e for e in state["employees"] if e["id"] == str(emp.id))
    assert loaded_emp["pay_rate"] == 24.50
    assert loaded_emp["hire_date"] == date(2022, 3, 1)
    assert loaded_emp["seniority_rank"] == 2


async def test_load_initial_state_location_overtime_threshold_overrides_company(
    db_session, seed_company, seed_location,
):
    from backend.scheduling.graph import _load_initial_state

    seed_company.overtime_threshold_hours = 35.0
    seed_location.overtime_threshold_hours = 30.0
    await db_session.commit()

    state = await _load_initial_state(
        company_id=str(seed_company.id),
        week_start_date="2026-12-21",
        db=db_session,
        num_days=7,
    )

    loc = next(l for l in state["locations"] if l["id"] == str(seed_location.id))
    assert loc["overtime_threshold_hours"] == 30.0
```

No per-test decorator needed — this file sets `pytestmark = pytest.mark.asyncio` at module level (line 13), which already applies to every `async def test_...` in the file, matching the neighboring `test_load_initial_state_fuses_override_into_weekly_schedule`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_pipeline.py -k overtime_threshold -v`
Expected: FAIL with `KeyError: 'overtime_threshold_hours'` (location dict) and `KeyError: 'pay_rate'` (employee dict).

- [ ] **Step 3: Wire the Company query and location dict in `graph.py`**

Add the import near the other `backend.scheduling.*` imports (after the `local_scheduler` import):

```python
from backend.scheduling.cost_seniority import resolve_overtime_threshold
```

Before the "Load locations" block (line 643), add a Company lookup (the `Company` model is already imported at the top of this file):

```python
    # Resolve the company-level overtime default once; each location below
    # applies its own override on top (see cost_seniority.resolve_overtime_threshold).
    company_result = await db.execute(select(Company).where(Company.id == company_id))
    company_row = company_result.scalar_one_or_none()
    company_overtime_threshold = company_row.overtime_threshold_hours if company_row else None
```

Update the location dict construction (lines 649-658):

```python
    locations: List[Dict[str, Any]] = [
        {
            "id": str(loc.id),
            "name": loc.name,
            "timezone": loc.timezone,
            "address": loc.address,
            "min_rest_hours": loc.min_rest_hours,
            "overtime_threshold_hours": resolve_overtime_threshold(
                company_overtime_threshold, loc.overtime_threshold_hours
            ),
        }
        for loc in locations_orm
    ]
```

- [ ] **Step 4: Add the employee fields**

In the employee dict construction (around line 748, next to `"max_hours_per_week": emp.max_hours_per_week,`), add:

```python
            "max_hours_per_week": emp.max_hours_per_week,
            "pay_rate": emp.pay_rate,
            "hire_date": emp.hire_date,
            "seniority_rank": emp.seniority_rank,
```

- [ ] **Step 5: Pass `employee_hours_committed` through `nodes.build_prompt`**

In `backend/scheduling/nodes.py`, `build_prompt` (around line 224), add the new kwarg:

```python
    prompt = build_schedule_prompt(
        location=location,
        shift_template=shift_template,
        employees=employees,
        week_start_date=state["week_start_date"],
        conflict_notes=conflict_notes,
        num_days=state.get("num_days", 7),
        employee_hours_committed=state.get("employee_weekly_hours_draft", {}),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_schedule_pipeline.py -k overtime_threshold -v`
Expected: PASS (both new tests).

- [ ] **Step 7: Run the full pipeline test suite to confirm no regression**

Run: `pytest tests/test_schedule_pipeline.py tests/test_schedule_week_limit.py -v`
Expected: PASS, unchanged.

- [ ] **Step 8: Commit**

```bash
git add backend/scheduling/graph.py backend/scheduling/nodes.py tests/test_schedule_pipeline.py
git commit -m "feat(scheduling): load and resolve cost/seniority/overtime fields in the pipeline (#134)"
```

---

### Task 7: API — schemas, endpoints, and paid-plan gating

**Files:**
- Modify: `backend/schemas/employee.py`
- Modify: `backend/routers/employees.py`
- Modify: `backend/schemas/company.py`
- Modify: `backend/routers/company.py`
- Modify: `backend/schemas/location.py`
- Modify: `backend/routers/locations.py`
- Test: `tests/test_cost_seniority_api.py`

**Interfaces:**
- Consumes: `assert_paid_plan(db, company_id, feature) -> None` from `backend.services.plan` (raises `HTTPException(402, detail={"code": f"{feature}_requires_paid_plan", ...})` on a free plan).
- Produces: `PATCH/PUT /api/v1/employees/{id}` and `POST /api/v1/employees/` accept `pay_rate` (paid-gated), `hire_date`, `seniority_rank` (free). `PUT /api/v1/company/` and `PUT/POST /api/v1/locations/...` accept `overtime_threshold_hours`/`overtime_premium_multiplier` (paid-gated).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_seniority_api.py`:

```python
"""Pay rate and overtime fields are paid-plan gated at the write layer;
hire_date/seniority_rank are free (#134)."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region, User
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


async def _tenant(db: AsyncSession, *, paid: bool) -> SimpleNamespace:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G", stripe_subscription_id="sub_x" if paid else None))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}", ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, employee_id, manager_id = _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id, name="Main", timezone="America/New_York"))
    db.add(User(id=manager_id, company_id=company_id, email=f"{manager_id}@example.com", hashed_password="x", full_name="Mo Manager", user_role="manager"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="Dana Okafor", location_ids=[location_id]))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, employee_id=employee_id,
        manager_headers={"Authorization": f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
    )


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=False)


async def test_free_plan_cannot_set_employee_pay_rate(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"pay_rate": 20.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_employee_pay_rate(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{paid.employee_id}",
        json={"pay_rate": 20.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pay_rate"] == 20.0


async def test_free_plan_can_set_employee_hire_date_and_seniority_rank(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"hire_date": "2022-03-01", "seniority_rank": 1}, headers=free.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hire_date"] == "2022-03-01"
    assert body["seniority_rank"] == 1


async def test_free_plan_cannot_set_company_overtime_threshold(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        "/api/v1/company/",
        json={"overtime_threshold_hours": 35.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_company_overtime_threshold(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        "/api/v1/company/",
        json={"overtime_threshold_hours": 35.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["overtime_threshold_hours"] == 35.0


async def test_free_plan_cannot_set_location_overtime_threshold(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/locations/{free.location_id}",
        json={"overtime_threshold_hours": 30.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_location_overtime_threshold(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/locations/{paid.location_id}",
        json={"overtime_threshold_hours": 30.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["overtime_threshold_hours"] == 30.0


async def test_seniority_rank_zero_rejected(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"seniority_rank": 0}, headers=free.manager_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_negative_pay_rate_rejected(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{paid.employee_id}",
        json={"pay_rate": -5.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 422, resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cost_seniority_api.py -v`
Expected: FAIL — `pay_rate`/`hire_date`/`seniority_rank`/`overtime_threshold_hours` are unknown fields (Pydantic ignores extras by default so these currently 200 without effect, then fail on the response-body assertions) or 422/404 depending on schema strictness.

- [ ] **Step 3: Update `backend/schemas/employee.py`**

Add to `EmployeeCreate` (`Field` is already imported at the top of this file):

```python
class EmployeeCreate(BaseModel):
    full_name: str
    email: str | None = None
    user_id: str | None = None
    location_ids: list[str] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[str] | None = None  # additional companies to assign to
    max_hours_per_week: float | None = None
    pay_rate: float | None = Field(default=None, ge=0)  # paid-plan gated in the router
    hire_date: date | None = None
    seniority_rank: int | None = Field(default=None, gt=0)
```

Add the same three fields to `EmployeeUpdate`:

```python
class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    user_id: str | None = None
    location_ids: list[str] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[str] | None = None  # update company assignments
    # Use a sentinel to distinguish "unset" from "explicitly clear to null".
    # Pydantic v2: a field not present in the payload stays as default None;
    # routers must send this field to mutate it (nullable means "no cap").
    max_hours_per_week: float | None = None
    pay_rate: float | None = Field(default=None, ge=0)  # paid-plan gated in the router
    hire_date: date | None = None
    seniority_rank: int | None = Field(default=None, gt=0)
```

Add the same three fields to `EmployeeResponse`:

```python
class EmployeeResponse(BaseModel):
    id: str
    company_id: str
    user_id: str | None
    full_name: str
    email: str | None
    location_ids: list[str] | None
    max_hours_per_week: float | None = None
    pay_rate: float | None = None
    hire_date: date | None = None
    seniority_rank: int | None = None
    roles: list[EmployeeRoleResponse] = []
    company_ids: list[str] = []

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Update `backend/routers/employees.py`**

Add `assert_paid_plan` to the existing plan import:

```python
from backend.services.plan import assert_can_add, assert_paid_plan, assert_roster_editable
```

In `create_employee`, gate before constructing the ORM object when `pay_rate` is provided:

```python
    await assert_roster_editable(db, str(current_user.company_id))
    await assert_can_add(db, str(current_user.company_id), employees=1)
    if body.pay_rate is not None:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    employee = Employee(
        company_id=current_user.company_id,
        full_name=body.full_name,
        email=body.email,
        user_id=body.user_id,
        location_ids=body.location_ids,
        max_hours_per_week=body.max_hours_per_week,
        pay_rate=body.pay_rate,
        hire_date=body.hire_date,
        seniority_rank=body.seniority_rank,
    )
```

In `update_employee`, gate before the field assignments when `pay_rate` is present in the payload, and apply all three new fields using the same `model_fields_set` clearable pattern `max_hours_per_week` already uses:

```python
    if "pay_rate" in body.model_fields_set:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    if body.full_name is not None:
        employee.full_name = body.full_name
    if body.email is not None:
        employee.email = body.email
    if body.user_id is not None:
        employee.user_id = body.user_id
    if body.location_ids is not None:
        employee.location_ids = body.location_ids
    # Use model_fields_set so the caller can explicitly clear the cap to null
    # (remove the restriction) by sending {"max_hours_per_week": null}.
    if "max_hours_per_week" in body.model_fields_set:
        employee.max_hours_per_week = body.max_hours_per_week
    if "pay_rate" in body.model_fields_set:
        employee.pay_rate = body.pay_rate
    if "hire_date" in body.model_fields_set:
        employee.hire_date = body.hire_date
    if "seniority_rank" in body.model_fields_set:
        employee.seniority_rank = body.seniority_rank
```

- [ ] **Step 5: Update `backend/schemas/company.py`**

Add `Field` to the existing `from pydantic import BaseModel` import:

```python
from pydantic import BaseModel, Field
```

```python
class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    ownership_group_id: str | None = None
    created_at: datetime
    overtime_threshold_hours: float | None = None
    overtime_premium_multiplier: float | None = None

    model_config = {"from_attributes": True}


class CompanyUpdate(BaseModel):
    name: str | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)
```

- [ ] **Step 6: Update `backend/routers/company.py`**

```python
from backend.services.plan import assert_paid_plan
```

```python
@router.put("/", response_model=CompanyResponse)
async def update_company(
    body: CompanyUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    if body.name is not None:
        company.name = body.name
    if "overtime_threshold_hours" in body.model_fields_set or "overtime_premium_multiplier" in body.model_fields_set:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")
    if "overtime_threshold_hours" in body.model_fields_set:
        company.overtime_threshold_hours = body.overtime_threshold_hours
    if "overtime_premium_multiplier" in body.model_fields_set:
        company.overtime_premium_multiplier = body.overtime_premium_multiplier

    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)
```

- [ ] **Step 7: Update `backend/schemas/location.py`**

Add `Field` to the existing `from pydantic import BaseModel` import:

```python
from pydantic import BaseModel, Field
```

```python
class LocationCreate(BaseModel):
    region_id: str
    name: str
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str
    # Minimum rest hours between shifts on different days (NYC Fair Workweek
    # clopening rule = 11). NULL/omitted = no constraint.
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)


class LocationUpdate(BaseModel):
    region_id: str | None = None
    name: str | None = None
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str | None = None
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)


class LocationResponse(BaseModel):
    id: str
    company_id: str
    region_id: str
    name: str
    address: str | None
    geo_coord: dict | None
    timezone: str
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = None
    overtime_premium_multiplier: float | None = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 8: Update `backend/routers/locations.py`**

```python
from backend.services.plan import assert_can_add, assert_paid_plan
```

In `create_location`, gate before constructing the ORM object:

```python
    await assert_can_add(db, str(current_user.company_id), locations=1)
    if body.overtime_threshold_hours is not None or body.overtime_premium_multiplier is not None:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    location = Location(
        company_id=current_user.company_id,
        region_id=body.region_id,
        name=body.name,
        address=body.address,
        geo_coord=body.geo_coord,
        timezone=body.timezone,
        min_rest_hours=body.min_rest_hours,
        overtime_threshold_hours=body.overtime_threshold_hours,
        overtime_premium_multiplier=body.overtime_premium_multiplier,
    )
```

In `update_location`:

```python
    if "overtime_threshold_hours" in body.model_fields_set or "overtime_premium_multiplier" in body.model_fields_set:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    if body.region_id is not None:
        location.region_id = body.region_id
    if body.name is not None:
        location.name = body.name
    if body.address is not None:
        location.address = body.address
    if body.geo_coord is not None:
        location.geo_coord = body.geo_coord
    if body.timezone is not None:
        location.timezone = body.timezone
    # Nullable + clearable: send {"min_rest_hours": null} to remove the rule.
    if "min_rest_hours" in body.model_fields_set:
        location.min_rest_hours = body.min_rest_hours
    if "overtime_threshold_hours" in body.model_fields_set:
        location.overtime_threshold_hours = body.overtime_threshold_hours
    if "overtime_premium_multiplier" in body.model_fields_set:
        location.overtime_premium_multiplier = body.overtime_premium_multiplier
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_cost_seniority_api.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 10: Run the full backend test suite to confirm no regression**

Run: `pytest tests/ -v`
Expected: PASS, 100%. This is the final backend regression gate for the whole feature — every prior task's no-op guarantee is being exercised together here for the first time.

- [ ] **Step 11: Commit**

```bash
git add backend/schemas/employee.py backend/routers/employees.py \
  backend/schemas/company.py backend/routers/company.py \
  backend/schemas/location.py backend/routers/locations.py \
  tests/test_cost_seniority_api.py
git commit -m "feat(api): expose pay rate/overtime/seniority fields, paid-gate cost/overtime (#134)"
```

---

### Task 8: Frontend — Employee form fields

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/employees.ts`
- Modify: `frontend/src/pages/manager/Employees.tsx`
- Modify: `frontend/src/i18n/en.ts` and all 18 other locale files under `frontend/src/i18n/` (`ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`)

**Interfaces:**
- Produces: the Employees table gains editable `pay_rate` (disabled with an explanatory message on a free plan, matching this page's existing `plan?.plan === "free"` gating style), `hire_date`, and `seniority_rank` columns, all optional.

- [ ] **Step 1: Add the fields to the `Employee` type**

In `frontend/src/types/index.ts`, `Employee` is defined at line 121:

```typescript
export interface Employee {
  id: string;
  company_id: string;
  user_id: string | null;
  full_name: string;
  email: string | null;
  location_ids: string[] | null;
  max_hours_per_week: number | null;
  pay_rate: number | null;
  hire_date: string | null;
  seniority_rank: number | null;
  roles: EmployeeRole[];
  company_ids: string[];
}
```

- [ ] **Step 2: Add the fields to the API request bodies**

In `frontend/src/api/employees.ts`, add to both `createEmployee`'s and `updateEmployee`'s inline body types (each currently ends with `max_hours_per_week?: number | null;`):

```typescript
  max_hours_per_week?: number | null;
  pay_rate?: number | null;
  hire_date?: string | null;
  seniority_rank?: number | null;
```

- [ ] **Step 3: Add the i18n keys**

In `frontend/src/i18n/en.ts`, the `employeesPage` object (line 411) currently ends with `csvLimitError` before its closing brace. Add:

```typescript
    csvLimitError: "This file has {rows} employees but your free plan has room for {remaining}. The whole file would be rejected — upgrade, or upload a smaller file.",
    payRate: "Pay Rate ($/hr)",
    payRateGatedHint: "Upgrade to a paid plan to set pay rate for cost-aware scheduling.",
    hireDate: "Hire Date",
    seniorityRank: "Seniority Rank",
    seniorityRankHint: "Manual override — leave blank to rank by hire date.",
  },
```

Add the identical five keys (`payRate`, `payRateGatedHint`, `hireDate`, `seniorityRank`, `seniorityRankHint`), translated, to the `employeesPage` object in every other locale file in `frontend/src/i18n/`. If a translation isn't obvious, use the English string as a placeholder value in that locale file rather than skipping the key — the TypeScript build fails if any locale is missing a key that `en.ts` defines, so completeness matters more than translation quality for this step; a native-language follow-up can refine the copy later.

- [ ] **Step 4: Extend `editValues`/`addValues` state and their lifecycle functions**

In `frontend/src/pages/manager/Employees.tsx`, the state types (lines 36-52) become:

```typescript
  const [editValues, setEditValues] = useState<{
    full_name: string;
    email: string;
    roles: RoleAssignment[];
    location_ids: string[];
    company_ids: string[];
    pay_rate: string;
    hire_date: string;
    seniority_rank: string;
  }>({
    full_name: "", email: "", roles: [], location_ids: [], company_ids: [],
    pay_rate: "", hire_date: "", seniority_rank: "",
  });

  // Add-row state
  const [showAddRow, setShowAddRow] = useState(false);
  const [addValues, setAddValues] = useState<{
    full_name: string;
    email: string;
    roles: RoleAssignment[];
    location_ids: string[];
    company_ids: string[];
    pay_rate: string;
    hire_date: string;
    seniority_rank: string;
  }>({
    full_name: "", email: "", roles: [], location_ids: [], company_ids: [],
    pay_rate: "", hire_date: "", seniority_rank: "",
  });
```

The three new fields are kept as plain strings (matching `full_name`/`email`) rather than `number | null`, so a half-typed number never fights a controlled `<input>`; they're parsed on save.

`startEdit` (line 92) gains three lines:

```typescript
  const startEdit = (emp: Employee) => {
    setEditingId(emp.id);
    setEditValues({
      full_name: emp.full_name,
      email: emp.email ?? "",
      roles: emp.roles.map((r) => ({
        role_id: r.role_id,
        skill_level: r.skill_level,
      })),
      location_ids: emp.location_ids ?? [],
      company_ids: emp.company_ids ?? [],
      pay_rate: emp.pay_rate != null ? String(emp.pay_rate) : "",
      hire_date: emp.hire_date ?? "",
      seniority_rank: emp.seniority_rank != null ? String(emp.seniority_rank) : "",
    });
  };
```

`cancelEdit` (line 106) gains the same three keys reset to `""`:

```typescript
  const cancelEdit = () => {
    setEditingId(null);
    setEditValues({
      full_name: "",
      email: "",
      roles: [],
      location_ids: [],
      company_ids: [],
      pay_rate: "",
      hire_date: "",
      seniority_rank: "",
    });
  };
```

`handleSave` (line 117) gains three parsed fields in the `updateEmployee` payload:

```typescript
      await employeesApi.updateEmployee(editingId, {
        full_name: editValues.full_name,
        email: editValues.email || null,
        roles: editValues.roles,
        location_ids:
          editValues.location_ids.length > 0
            ? editValues.location_ids
            : null,
        company_ids:
          editValues.company_ids.length > 0
            ? editValues.company_ids
            : undefined,
        pay_rate: editValues.pay_rate === "" ? null : Number(editValues.pay_rate),
        hire_date: editValues.hire_date === "" ? null : editValues.hire_date,
        seniority_rank: editValues.seniority_rank === "" ? null : Number(editValues.seniority_rank),
      });
```

`handleCreate` (line 150) gains the same three fields in the `createEmployee` payload, plus the reset afterward:

```typescript
      await employeesApi.createEmployee({
        full_name: addValues.full_name,
        email: addValues.email || null,
        roles: addValues.roles.length > 0 ? addValues.roles : undefined,
        location_ids:
          addValues.location_ids.length > 0
            ? addValues.location_ids
            : undefined,
        company_ids:
          addValues.company_ids.length > 0
            ? addValues.company_ids
            : undefined,
        pay_rate: addValues.pay_rate === "" ? null : Number(addValues.pay_rate),
        hire_date: addValues.hire_date === "" ? null : addValues.hire_date,
        seniority_rank: addValues.seniority_rank === "" ? null : Number(addValues.seniority_rank),
      });
      setAddValues({
        full_name: "",
        email: "",
        roles: [],
        location_ids: [],
        company_ids: [],
        pay_rate: "",
        hire_date: "",
        seniority_rank: "",
      });
```

- [ ] **Step 5: Add the three table columns**

In the `<thead>` (line ~424), insert three new `<th>` cells right after the email header and before the locations header:

```tsx
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.email}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.employeesPage.payRate}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.employeesPage.hireDate}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.employeesPage.seniorityRank}
              </th>
              <th className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}>
                {t.common.locations}
              </th>
```

In the per-row `<tbody>` map (right after the email `<td>` block that ends around line 494, before the `location_ids` `<td>`), insert:

```tsx
                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        className="glass-input-sm w-full"
                        disabled={plan?.plan === "free"}
                        title={plan?.plan === "free" ? t.employeesPage.payRateGatedHint : undefined}
                        value={editValues.pay_rate}
                        onChange={(e) =>
                          setEditValues((v) => ({ ...v, pay_rate: e.target.value }))
                        }
                      />
                    ) : (
                      <span className={`text-sm ${text.body}`}>{emp.pay_rate ?? ""}</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        type="date"
                        className="glass-input-sm w-full"
                        value={editValues.hire_date}
                        onChange={(e) =>
                          setEditValues((v) => ({ ...v, hire_date: e.target.value }))
                        }
                      />
                    ) : (
                      <span className={`text-sm ${text.body}`}>{emp.hire_date ?? ""}</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {isEditing ? (
                      <input
                        type="number"
                        min="1"
                        step="1"
                        className="glass-input-sm w-full"
                        title={t.employeesPage.seniorityRankHint}
                        value={editValues.seniority_rank}
                        onChange={(e) =>
                          setEditValues((v) => ({ ...v, seniority_rank: e.target.value }))
                        }
                      />
                    ) : (
                      <span className={`text-sm ${text.body}`}>{emp.seniority_rank ?? ""}</span>
                    )}
                  </td>
```

In the add-row block (right after the email `<td>` that ends around line 583, before the `location_ids` `<td>`), insert:

```tsx
                <td className="px-4 py-2">
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    className="glass-input-sm w-full"
                    placeholder={t.employeesPage.payRate}
                    disabled={plan?.plan === "free"}
                    title={plan?.plan === "free" ? t.employeesPage.payRateGatedHint : undefined}
                    value={addValues.pay_rate}
                    onChange={(e) => setAddValues((v) => ({ ...v, pay_rate: e.target.value }))}
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="date"
                    className="glass-input-sm w-full"
                    value={addValues.hire_date}
                    onChange={(e) => setAddValues((v) => ({ ...v, hire_date: e.target.value }))}
                  />
                </td>
                <td className="px-4 py-2">
                  <input
                    type="number"
                    min="1"
                    step="1"
                    className="glass-input-sm w-full"
                    placeholder={t.employeesPage.seniorityRank}
                    title={t.employeesPage.seniorityRankHint}
                    value={addValues.seniority_rank}
                    onChange={(e) => setAddValues((v) => ({ ...v, seniority_rank: e.target.value }))}
                  />
                </td>
```

Also add the same three keys, reset to `""`, to the add-row Cancel button's `setAddValues({...})` reset call (right below the `handleCreate` button in that block).

- [ ] **Step 6: Verify the TypeScript build**

Run: `cd frontend && npm run build`
Expected: succeeds with no type errors (confirms every locale file has the new i18n keys and the `Employee` type additions are consistent everywhere they're used).

- [ ] **Step 7: Manually verify in the browser**

Start the dev servers (`uvicorn main:app --reload` and `npm run dev`), log in as a manager on a free-plan seeded company, open the Employees page, and confirm: the pay rate input is disabled with the upgrade tooltip, hire date and seniority rank are editable and save correctly (reload the page and confirm the values persisted). Then switch to (or seed) a paid-plan company and confirm the pay rate field is enabled and saves.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/manager/Employees.tsx frontend/src/api/employees.ts \
  frontend/src/i18n/*.ts frontend/src/types/index.ts
git commit -m "feat(frontend): add pay rate, hire date, seniority rank to Employee table (#134)"
```

---

### Task 9: Frontend — Company and Location settings fields

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/api/company.ts`
- Modify: `frontend/src/api/locations.ts`
- Modify: `frontend/src/pages/manager/Company.tsx`
- Modify: `frontend/src/pages/manager/Locations.tsx`
- Modify: `frontend/src/i18n/en.ts` and all 18 other locale files under `frontend/src/i18n/`

**Interfaces:**
- Produces: Company settings gains editable `overtime_threshold_hours`/`overtime_premium_multiplier` inputs; Locations' `DataTable` gains two matching override columns. Both paid-plan gated the same way as Task 8's pay rate field.

- [ ] **Step 1: Add the fields to the `Company` and `Location` types**

In `frontend/src/types/index.ts`, `Company` (line 44) and `Location` (line 82) become:

```typescript
export interface Company {
  id: string;
  name: string;
  slug: string;
  ownership_group_id: string | null;
  created_at: string;
  overtime_threshold_hours: number | null;
  overtime_premium_multiplier: number | null;
}
```

```typescript
export interface Location {
  id: string;
  company_id: string;
  region_id: string;
  name: string;
  address: string | null;
  geo_coord: Record<string, unknown> | null;
  timezone: string;
  /** Minimum rest hours between shifts on different days (NYC Fair Workweek
   * clopening rule = 11). null = no constraint. */
  min_rest_hours: number | null;
  /** Overrides the company default when set; both null falls back to a 40h
   * code default. Paid-plan gated at the API write layer. */
  overtime_threshold_hours: number | null;
  overtime_premium_multiplier: number | null;
}
```

- [ ] **Step 2: Add the fields to the API request bodies**

`frontend/src/api/company.ts` in full is currently:

```typescript
import type { Company } from "../types";
import { apiFetch } from "./client";

export function getCompany(): Promise<Company> {
  return apiFetch<Company>("/company/");
}

export function listGroupCompanies(): Promise<Company[]> {
  return apiFetch<Company[]>("/company/all");
}

export function updateCompany(body: {
  name?: string;
  overtime_threshold_hours?: number | null;
  overtime_premium_multiplier?: number | null;
}): Promise<Company> {
  return apiFetch<Company>("/company/", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
```

In `frontend/src/api/locations.ts`, add the two fields to both `createLocation`'s and `updateLocation`'s body types (each currently ends with `min_rest_hours?: number | null;`):

```typescript
  min_rest_hours?: number | null;
  overtime_threshold_hours?: number | null;
  overtime_premium_multiplier?: number | null;
```

- [ ] **Step 3: Add the i18n keys for Company settings**

Company.tsx uses `t.companyPage.*` for its labels (Locations.tsx's DataTable columns use plain hardcoded English strings for `min_rest_hours`, not i18n — match each page's own existing convention rather than introducing i18n into Locations.tsx). In `frontend/src/i18n/en.ts`, `companyPage` (line 360) becomes:

```typescript
  companyPage: {
    title: "Company Settings",
    companyName: "Company Name",
    slug: "Slug",
    updateSuccess: "Company name updated successfully.",
    overtimeThreshold: "Overtime Threshold (hrs/week)",
    overtimeMultiplier: "Overtime Premium Multiplier",
    overtimeGatedHint: "Upgrade to a paid plan to configure overtime settings.",
  },
```

Add the same three keys (`overtimeThreshold`, `overtimeMultiplier`, `overtimeGatedHint`), translated, to the `companyPage` object in every other locale file (English placeholder acceptable per Task 8 Step 3's note).

- [ ] **Step 4: Add the Company settings fields**

`frontend/src/pages/manager/Company.tsx` is a plain `useState`/`<input>` form (102 lines, no `DataTable`). Add plan-awareness and the two new fields:

```tsx
import React, { useCallback, useEffect, useState } from "react";
import * as companyApi from "../../api/company";
import type { Company as CompanyType } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { usePlan } from "../../hooks/usePlan";
import DemoGuard from "../../components/shared/DemoGuard";
import { text, border, bg } from "../../theme";

export default function Company() {
  const { t } = useLanguage();
  const { plan } = usePlan();
  const [company, setCompany] = useState<CompanyType | null>(null);
  const [name, setName] = useState("");
  const [overtimeThresholdHours, setOvertimeThresholdHours] = useState("");
  const [overtimePremiumMultiplier, setOvertimePremiumMultiplier] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const fetchCompany = useCallback(async () => {
    try {
      const data = await companyApi.getCompany();
      setCompany(data);
      setName(data.name);
      setOvertimeThresholdHours(
        data.overtime_threshold_hours != null ? String(data.overtime_threshold_hours) : ""
      );
      setOvertimePremiumMultiplier(
        data.overtime_premium_multiplier != null ? String(data.overtime_premium_multiplier) : ""
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load company");
    }
  }, []);

  useEffect(() => {
    fetchCompany();
  }, [fetchCompany]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const updated = await companyApi.updateCompany({
        name,
        overtime_threshold_hours:
          overtimeThresholdHours === "" ? null : Number(overtimeThresholdHours),
        overtime_premium_multiplier:
          overtimePremiumMultiplier === "" ? null : Number(overtimePremiumMultiplier),
      });
      setCompany(updated);
      setSuccess(t.companyPage.updateSuccess);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update company");
    } finally {
      setSaving(false);
    }
  };

  if (!company && !error) {
    return <div className={text.muted}>{t.common.loading}</div>;
  }

  const overtimeGated = plan?.plan === "free";

  return (
    <div>
      <h1 className={`text-2xl font-bold ${text.heading} mb-6`}>{t.companyPage.title}</h1>
      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      {success && (
        <div className="glass-alert-success mb-4">
          {success}
        </div>
      )}
      <div className="glass-card p-6 max-w-lg">
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="glass-label">
              {t.companyPage.companyName}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          {company && (
            <div>
              <label className="glass-label">
                {t.companyPage.slug}
              </label>
              <input
                type="text"
                value={company.slug}
                disabled
                className={`w-full rounded px-3 py-2 ${bg.sectionSubtle} ${text.muted} border ${border.subtle}`}
              />
            </div>
          )}
          <div>
            <label className="glass-label">
              {t.companyPage.overtimeThreshold}
            </label>
            <input
              type="number"
              step="0.5"
              min="0"
              value={overtimeThresholdHours}
              disabled={overtimeGated}
              title={overtimeGated ? t.companyPage.overtimeGatedHint : undefined}
              onChange={(e) => setOvertimeThresholdHours(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          <div>
            <label className="glass-label">
              {t.companyPage.overtimeMultiplier}
            </label>
            <input
              type="number"
              step="0.1"
              min="1"
              value={overtimePremiumMultiplier}
              disabled={overtimeGated}
              title={overtimeGated ? t.companyPage.overtimeGatedHint : undefined}
              onChange={(e) => setOvertimePremiumMultiplier(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          {overtimeGated && (
            <p className={`text-xs ${text.muted}`}>{t.companyPage.overtimeGatedHint}</p>
          )}
          <DemoGuard>
            <button
              type="submit"
              disabled={saving}
              className="glass-btn-primary"
            >
              {saving ? t.common.saving : t.common.save}
            </button>
          </DemoGuard>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Add the Location settings columns**

`DataTable`'s `Column` type (`frontend/src/components/shared/DataTable.tsx:6-15`) has no per-column disabled prop, so — matching how `Locations.tsx` already keeps `min_rest_hours`'s label/title as plain hardcoded strings rather than i18n — gate these two columns by omitting them from the `columns` array entirely on a free plan, with a hint line above the table. In `frontend/src/pages/manager/Locations.tsx`, the `columns` memo (lines 45-67) becomes:

```typescript
  const overtimeGated = plan?.plan === "free";

  const columns: Column[] = useMemo(
    () => [
      { key: "name", label: "Name", type: "text" },
      {
        key: "region_id",
        label: "Region",
        type: "select",
        options: regions.map((r) => ({ value: r.id, label: r.name })),
      },
      { key: "address", label: "Address", type: "text" },
      { key: "timezone", label: "Timezone", type: "text" },
      {
        key: "min_rest_hours",
        label: "Min rest (h)",
        type: "number",
        placeholder: "e.g. 11",
        title:
          "Minimum hours of rest between an employee's shifts on different days. " +
          "Set 11 for NYC Fair Workweek compliance (no clopenings). Leave blank for no limit.",
      },
      ...(overtimeGated
        ? []
        : [
            {
              key: "overtime_threshold_hours",
              label: "Overtime threshold (h/wk)",
              type: "number" as const,
              placeholder: "e.g. 40",
              title:
                "Hours/week after which the scheduler avoids assigning more hours to an " +
                "employee at this location. Leave blank to use the company default (or 40h).",
            },
            {
              key: "overtime_premium_multiplier",
              label: "Overtime premium multiplier",
              type: "number" as const,
              placeholder: "e.g. 1.5",
              title:
                "Pay multiplier for overtime hours at this location. Leave blank to use the " +
                "company default (or 1.5x).",
            },
          ]),
    ],
    [regions, overtimeGated]
  );
```

Extend `parseMinRest`-style null coercion for the two new fields (reusing the same helper, since the rule — blank/invalid → null, otherwise a non-negative number — is identical) in `handleSave` and `handleCreate`:

```typescript
      await locationsApi.updateLocation(locations[idx].id, {
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: row.timezone as string,
        min_rest_hours: parseMinRest(row.min_rest_hours),
        overtime_threshold_hours: parseMinRest(row.overtime_threshold_hours),
        overtime_premium_multiplier: parseMinRest(row.overtime_premium_multiplier),
      });
```

```typescript
      await locationsApi.createLocation({
        name: row.name as string,
        region_id: row.region_id as string,
        address: (row.address as string) || null,
        timezone: (row.timezone as string) || "UTC",
        min_rest_hours: parseMinRest(row.min_rest_hours),
        overtime_threshold_hours: parseMinRest(row.overtime_threshold_hours),
        overtime_premium_multiplier: parseMinRest(row.overtime_premium_multiplier),
      });
```

Add a hint line above the `DataTable` when gated:

```tsx
      {overtimeGated && (
        <p className={`text-xs ${text.muted} mb-2`}>
          Upgrade to a paid plan to configure overtime settings for a location.
        </p>
      )}
      <div className="glass-card">
        <DataTable
```

- [ ] **Step 6: Verify the TypeScript build**

Run: `cd frontend && npm run build`
Expected: succeeds with no type errors.

- [ ] **Step 7: Manually verify in the browser**

On a free-plan company: confirm the Company overtime fields are disabled with the upgrade hint, and the Location table's two overtime columns are absent with the hint line shown above the table. On a paid-plan company: set a company-level `overtime_threshold_hours`, then set a different value on one location, reload both pages, and confirm both values persisted independently (this is the override relationship Task 6's backend resolution depends on).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/manager/Company.tsx frontend/src/pages/manager/Locations.tsx \
  frontend/src/api/company.ts frontend/src/api/locations.ts \
  frontend/src/types/index.ts frontend/src/i18n/*.ts
git commit -m "feat(frontend): add overtime threshold/multiplier to Company and Location settings (#134)"
```

---

## Final verification

After all 9 tasks: run `pytest tests/ -v` (full backend suite) and `cd frontend && npm run build` (full frontend build) one more time together, then do one end-to-end manual pass — generate a schedule (both the deterministic and AI paths, via the strategy toggle if the UI exposes one) for a paid-plan company with pay_rate, overtime_threshold_hours, and seniority set on a few employees, and confirm the resulting assignments visibly favor cheaper/more-senior/under-threshold candidates when the schedule has room to choose.
