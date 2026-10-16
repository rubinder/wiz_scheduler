# Open Shifts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unfilled shifts survive approval as labelled open shifts. Managers are told about them, and fill them from a picker ranked by who fits.

**Architecture:** An open shift is a `Shift` row with `employee_id IS NULL`, and a draft entry with `employee_id: null, status: "OPEN"`.
- The scheduling graph keeps its internal `VACANT` placeholder; `emit_result` converts it at the boundary.
- One shared availability check (`services/availability_check.py`) backs both the edit warnings and a new candidate-ranking endpoint.
- The frontend renders open cards, a banner, a ranked `CandidatePicker`, an add-open-shift form and a Sidebar badge.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Postgres (testcontainers), pytest-asyncio, React 18 + TypeScript + Vite, vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-open-shifts-design.md`

## Global Constraints

- Branch `feat/open-shifts`; never commit to `main`.
- An open shift is `Shift.employee_id IS NULL`. In draft JSON it is `"employee_id": null, "employee_name": null, "status": "OPEN"`. The legacy `"employee_id": "VACANT"` / `"status": "VACANT"` must still be read as open.
- `CONFLICT` entries are never open and are never materialised.
- Candidate tiers, exactly:
  1. available + has role + at location
  2. has role + at location (not available)
  3. available + at location (no role)

  Alphabetical (case-insensitive) within a tier. Everyone else (not at the location, or at it with neither the role nor availability) is **not offered** in the picker. `tier_for` still returns 4 for them internally; `rank_candidates` drops them.
- Everything is company-scoped (`current_user.company_id`). No per-manager location scoping.
- Badge window: open shifts in approved schedules with `start_time` in `[now, now + 14 days)`.
- "Today"/"now" is always UTC: `datetime.now(timezone.utc)`, never `date.today()`.
- Roles are never hardcoded outside `seed.py` (test fixtures excepted).
- Tailwind: logical utilities only (`ms-`, `me-`, `ps-`, `border-s`, `text-start`, `start-0`).
- Every new UI string goes into all 19 locale files; `tsc` (`npm run build`) enforces parity.
- No new dependencies.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi
  ```
- Backend tests: `pytest tests/<file> -v` from the repo root (Postgres via testcontainers; Docker must be running). Frontend: `cd frontend && npm test` and `npm run build`.

## Review Focus

1. **A draft generated before the deploy, approved after it.** Its `"VACANT"` entries, including a legacy cap-trimmed one that still names a real employee, must become open shifts, never holds on that employee and never an FK error. → Task 3, `test_approve_turns_a_legacy_trimmed_entry_into_an_open_shift_not_a_hold`.
2. **A draft entry posted with `employee_id: ""`** (the search box cleared) must be normalised to open, not 500 on approval. → Task 3, `test_draft_save_normalises_an_empty_employee_to_open`.
3. **An overnight slot (22:00–06:00)** in the candidate ranking must end on the next day, not produce an empty or negative span. → Task 8, `test_slot_span_rolls_an_overnight_end_to_the_next_day`.
4. **Unassigning a shift already exported to 7shifts** must still warn `already_exported` with `employee_id: null`, not `"None"`. → Task 4, `test_unassigning_an_exported_shift_warns_with_a_null_employee`.
5. **An employee with an invalid stored timezone** must rank as if they had none, never 500 the picker. → Task 8, `test_invalid_employee_timezone_degrades_to_no_timezone`.

---

## File Map

**Backend:**

| Action | File | Change |
|---|---|---|
| Create | `backend/scheduling/open_shifts.py` | `is_open_entry`, `to_open_entry`, constants |
| Create | `backend/alembic/versions/0047_open_shifts.py` | nullable `shifts.employee_id`; guarded downgrade |
| Create | `backend/services/availability_check.py` | the one availability definition |
| Create | `backend/services/candidates.py` | tiered ranking (5 bulk queries) |
| Create | `backend/routers/open_shifts.py` | `POST /schedules/candidates`, `GET /schedules/open-shifts/count` |
| Modify | `backend/models/schedule.py` | `Shift.employee_id` nullable |
| Modify | `backend/scheduling/state.py` | `ShiftAssignment.employee_id` optional; `LocationResult.open_count` |
| Modify | `backend/scheduling/nodes.py` | `emit_result` converts VACANT → open, sets `open_count` |
| Modify | `backend/scheduling/graph.py` | holds and fairness history skip open shifts |
| Modify | `backend/schemas/schedule.py` | optional employee fields; `ApprovedShiftEdit.open`; `EditWarning.employee_id` optional |
| Modify | `backend/routers/schedules.py` | approval, draft save, approved edits, `_shift_to_response` |
| Modify | `backend/services/edit_warnings.py` | uses `availability_check`; skips open |
| Modify | `backend/routers/export_schedules.py` | 7shifts skips open; JSONL/list carry null |
| Modify | `backend/main.py` | register `open_shifts.router` before `schedules.router` |

**Frontend:**

| Action | File | Change |
|---|---|---|
| Create | `frontend/src/utils/openShifts.ts` (+ `.test.ts`) | pure helpers |
| Create | `frontend/src/api/openShifts.ts` | candidates, count, change event |
| Create | `frontend/src/components/shared/CandidatePicker.tsx` | ranked picker |
| Create | `frontend/src/components/shared/OpenShiftBanner.tsx` | count banner |
| Create | `frontend/src/components/shared/AddOpenShiftModal.tsx` | add-open-shift form |
| Modify | `frontend/src/types/index.ts` | shift types |
| Modify | `frontend/src/api/approvedSchedules.ts` | shift types, `open` edit |
| Modify | `frontend/src/api/exportSchedules.ts` | open-shift fields on export types |
| Modify | `frontend/src/components/shared/ScheduleGrid.tsx` | open card, highlight |
| Modify | `frontend/src/components/shared/EditShiftModal.tsx` | approved editor uses the picker |
| Modify | `frontend/src/pages/manager/Schedule.tsx` | draft editor, banner, add |
| Modify | `frontend/src/pages/manager/ApprovedSchedules.tsx` | banner, add, change event |
| Modify | `frontend/src/pages/manager/ExportSchedules.tsx` | open label, skipped message |
| Modify | `frontend/src/components/layout/Sidebar.tsx` | badge |
| Modify | `frontend/src/i18n/*.ts` (19 files) | new strings |

**Tests:**

| Action | File |
|---|---|
| Create | `tests/test_open_shift_helpers.py` |
| Create | `tests/test_open_shifts_emit.py` |
| Create | `tests/test_open_shifts_approval_integration.py` |
| Create | `tests/test_open_shifts_edit_integration.py` |
| Create | `tests/test_open_shifts_readers_integration.py` |
| Create | `tests/test_availability_check.py` |
| Create | `tests/test_edit_warnings_blackout_integration.py` |
| Create | `tests/test_open_shifts_candidates_integration.py` |
| Modify | `tests/test_schedules_integration.py` (two approval tests) |
| Modify | `tests/test_migrations.py` |

**Docs:** `README.md`, `CLAUDE.md`.

---

### Task 1: Data model, migration and open-entry helpers

**Files:**
- Create: `backend/scheduling/open_shifts.py`
- Create: `backend/alembic/versions/0047_open_shifts.py`
- Modify: `backend/models/schedule.py:55-57`
- Modify: `backend/scheduling/state.py:13-22,34-52`
- Modify: `backend/schemas/schedule.py` (`ShiftUpdate`, `ShiftResponse`, `ApprovedShiftEdit`, `EditWarning`)
- Test: `tests/test_open_shift_helpers.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces:
  - `OPEN_STATUS = "OPEN"`, `LEGACY_VACANT = "VACANT"`
  - `is_open_entry(entry: Mapping[str, Any]) -> bool`
  - `to_open_entry(entry: Mapping[str, Any]) -> dict[str, Any]`
  - `Shift.employee_id: Mapped[str | None]`
  - `ShiftUpdate.employee_id: str | None`, `ShiftUpdate.employee_name: str | None`
  - `ShiftResponse.employee_id: str | None`, `ShiftResponse.employee_name: str | None = ""`
  - `ApprovedShiftEdit.open: bool = False`
  - `EditWarning.employee_id: str | None`

- [ ] **Step 1: Write the failing helper tests**

`tests/test_open_shift_helpers.py`:

```python
"""Open-entry helpers (#174): which draft entries are open shifts."""

from backend.scheduling.open_shifts import OPEN_STATUS, is_open_entry, to_open_entry


def test_open_status_is_open():
    assert is_open_entry({"status": "OPEN", "employee_id": None})


def test_null_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": None})


def test_empty_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": ""})


def test_legacy_vacant_employee_is_open():
    assert is_open_entry({"status": "ok", "employee_id": "VACANT"})


def test_legacy_vacant_status_is_open_even_naming_a_real_employee():
    # A cap trim from before the trim reset employee_id left this shape.
    assert is_open_entry({"status": "VACANT", "employee_id": "e1"})


def test_filled_shift_is_not_open():
    assert not is_open_entry({"status": "ok", "employee_id": "e1"})


def test_conflict_is_never_open():
    assert not is_open_entry({"status": "CONFLICT", "employee_id": "VACANT"})


def test_to_open_entry_clears_the_person_and_keeps_the_slot():
    entry = {
        "employee_id": "VACANT", "employee_name": "VACANT-Staffing Shortage",
        "role_id": "r1", "role_name": "Cook", "location_id": "l1",
        "date": "2026-08-31", "start_time": "2026-08-31T09:00:00",
        "end_time": "2026-08-31T17:00:00", "status": "VACANT",
        "preference_violations": [{"kind": "day"}],
    }
    out = to_open_entry(entry)
    assert out == {
        **entry,
        "employee_id": None,
        "employee_name": None,
        "status": OPEN_STATUS,
        "preference_violations": [],
    }
    assert entry["employee_id"] == "VACANT"  # input untouched
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/test_open_shift_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.scheduling.open_shifts'`.

- [ ] **Step 3: Write `backend/scheduling/open_shifts.py`**

```python
"""Open shifts (#174): a shift with no one on it yet.

Inside the scheduling graph an unfilled slot is still the legacy "VACANT"
placeholder: every node's checks are written against it. It becomes an open
shift at the graph's boundary (emit_result), and everything downstream --
draft JSON, approval, the API -- reads the open shape.

Drafts saved before #174 still hold "VACANT" entries, so every reader of
draft JSON goes through is_open_entry, which accepts both. A CONFLICT entry
is never open: it is a genuine error, not a vacancy.
"""

from typing import Any, Mapping

OPEN_STATUS = "OPEN"
LEGACY_VACANT = "VACANT"


def is_open_entry(entry: Mapping[str, Any]) -> bool:
    """True for a draft entry that is an open shift, in either shape."""
    status = entry.get("status")
    if status == "CONFLICT":
        return False
    if status in (OPEN_STATUS, LEGACY_VACANT):
        return True
    return entry.get("employee_id") in (None, "", LEGACY_VACANT)


def to_open_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of *entry* in the open shape. The slot (role, location, date,
    times) is kept; the person and any preference asterisks are cleared."""
    out = dict(entry)
    out["employee_id"] = None
    out["employee_name"] = None
    out["status"] = OPEN_STATUS
    out["preference_violations"] = []
    return out
```

- [ ] **Step 4: Run the helper tests**

Run: `pytest tests/test_open_shift_helpers.py -v`
Expected: 8 passed.

- [ ] **Step 5: Make the model, the TypedDicts and the schemas nullable**

`backend/models/schedule.py`, replace:

```python
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
```

with:

```python
    # NULL = an open shift (#174): a real slot with no one on it yet.
    employee_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=True
    )
```

`backend/scheduling/state.py`:
- In `_ShiftAssignmentRequired`, change `employee_id: str` to `employee_id: str | None` and `employee_name: str` to `employee_name: str | None`.
- Change the status comment to `# "ok" | "CONFLICT" | "VACANT" (inside the graph) | "OPEN" (after emit_result)`.
- In `LocationResult`, add after `signal_config`:

```python
    # Open shifts in this location's result (#174), counted by emit_result.
    open_count: int
```

`backend/schemas/schedule.py`:

In `ShiftUpdate`, replace the first two fields:

```python
    # None = an open shift (#174).
    employee_id: str | None
    employee_name: str | None
```

In `ShiftResponse`, replace `employee_id: str` with `employee_id: str | None`, and `employee_name: str = ""` with `employee_name: str | None = ""`.

In `ApprovedShiftEdit`, add after `deleted: bool = False`:

```python
    # True unassigns an existing shift, or (with no shift_id) creates an open
    # one (#174). A missing employee_id already means "unchanged", so "no one"
    # needs its own flag. Mutually exclusive with employee_id.
    open: bool = False
```

In `EditWarning`, replace `employee_id: str` with `employee_id: str | None`.

- [ ] **Step 6: Write migration `backend/alembic/versions/0047_open_shifts.py`**

```python
"""Open shifts (#174): shifts.employee_id becomes nullable; NULL = open.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "shifts", "employee_id", existing_type=sa.String(8), nullable=True
    )


def downgrade() -> None:
    # Refuse rather than delete: an open shift is a slot a manager still has
    # to fill, and dropping it silently is exactly the bug #174 fixes.
    open_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM shifts WHERE employee_id IS NULL")
    ).scalar()
    if open_count:
        raise RuntimeError(
            f"{open_count} open shift(s) exist; fill or delete them before "
            "downgrading past 0047"
        )
    op.alter_column(
        "shifts", "employee_id", existing_type=sa.String(8), nullable=False
    )
```

- [ ] **Step 7: Add the migration test**

Append to the end of `tests/test_migrations.py`. It goes last because it leaves the module-scoped database at head.

```python
def _alembic_fails(url: str, *args: str) -> str:
    """Run alembic expecting failure; return its combined output."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=repo_root / "backend",
        env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result.stdout + result.stderr


def test_0047_downgrade_refuses_while_an_open_shift_exists(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")
    _psql(pg, (
        "INSERT INTO companies (id, name, slug) VALUES ('mgc00001', 'M', 'mig-open-1');"
        "INSERT INTO regions (id, company_id, name) VALUES ('mgr00001', 'mgc00001', 'R');"
        "INSERT INTO locations (id, company_id, region_id, name, timezone)"
        " VALUES ('mgl00001', 'mgc00001', 'mgr00001', 'L', 'UTC');"
        "INSERT INTO roles (id, company_id, name) VALUES ('mgo00001', 'mgc00001', 'X');"
        "INSERT INTO shift_schedules (id, company_id, location_id, week_start_date, status)"
        " VALUES ('mgs00001', 'mgc00001', 'mgl00001', '2026-08-31', 'approved');"
        "INSERT INTO shifts (id, company_id, shift_schedule_id, location_id, employee_id,"
        " role_id, role_name, date, start_time, end_time)"
        " VALUES ('mgf00001', 'mgc00001', 'mgs00001', 'mgl00001', NULL, 'mgo00001', 'X',"
        " '2026-08-31', '2026-08-31 09:00+00', '2026-08-31 17:00+00');"
    ))
    try:
        out = _alembic_fails(url, "downgrade", "0046")
        assert "open shift(s) exist" in out
    finally:
        _psql(pg, (
            "DELETE FROM shifts WHERE id = 'mgf00001';"
            "DELETE FROM shift_schedules WHERE id = 'mgs00001';"
            "DELETE FROM roles WHERE id = 'mgo00001';"
            "DELETE FROM locations WHERE id = 'mgl00001';"
            "DELETE FROM regions WHERE id = 'mgr00001';"
            "DELETE FROM companies WHERE id = 'mgc00001';"
        ))
    _alembic(url, "downgrade", "0046")
    _alembic(url, "upgrade", "head")
```

If an `INSERT` fails on a NOT NULL column without a server default, add that column to the insert with a literal value; the psql error names it.

- [ ] **Step 8: Run the migration tests**

Run: `pytest tests/test_migrations.py -v`
Expected: all pass, including `test_0047_downgrade_refuses_while_an_open_shift_exists` and the existing `test_migration_chain_integrity` (it checks the new file's revision chain).

- [ ] **Step 9: Run the existing schedule tests to catch typing fallout**

Run: `pytest tests/test_schedules_integration.py tests/test_edit_approved_schedule_integration.py -v`
Expected: all pass. Nothing writes open shifts yet.

- [ ] **Step 10: Commit**

```bash
git add backend/scheduling/open_shifts.py backend/alembic/versions/0047_open_shifts.py backend/models/schedule.py backend/scheduling/state.py backend/schemas/schedule.py tests/test_open_shift_helpers.py tests/test_migrations.py
git commit -m "feat: open shifts can be stored (nullable shifts.employee_id) (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 2: The scheduler emits open shifts

**Files:**
- Modify: `backend/scheduling/nodes.py` (`emit_result`, ~line 1441)
- Test: `tests/test_open_shifts_emit.py`

**Interfaces:**
- Consumes: `is_open_entry`, `to_open_entry`, `OPEN_STATUS` (Task 1).
- Produces: every `LocationResult` has `open_count: int`. Its `shifts` contain no `VACANT` entries, only `OPEN` ones.

- [ ] **Step 1: Write the failing tests**

`tests/test_open_shifts_emit.py`:

```python
"""emit_result is where an unfilled slot becomes an open shift (#174).

Inside the graph it is still the VACANT placeholder, so the node-level
tests (test_preferences_ai_path.py, test_affinity_validation.py, ...) are
unchanged; only what leaves the graph changes.
"""

from backend.scheduling.nodes import emit_result


def _shift(eid, status="ok", name=None):
    return {
        "employee_id": eid, "employee_name": name or eid, "role_id": "r1",
        "role_name": "Cook", "location_id": "loc1", "date": "2026-08-31",
        "start_time": "2026-08-31T09:00:00-04:00",
        "end_time": "2026-08-31T17:00:00-04:00", "status": status,
    }


def _state(shifts):
    return {
        "current_location": {"id": "loc1", "name": "Main"},
        "current_parsed_shifts": shifts,
        "errors": [],
        "draft_schedules": [],
        "completed_location_ids": [],
        "current_location_index": 0,
        "failure_entries": [],
    }


def test_a_staffing_shortage_leaves_the_graph_as_an_open_shift():
    out = emit_result(_state([
        _shift("e1"),
        _shift("VACANT", status="VACANT", name="VACANT-Staffing Shortage"),
    ]))
    result = out["draft_schedules"][0]
    filled, open_ = result["shifts"]
    assert filled["employee_id"] == "e1" and filled["status"] == "ok"
    assert open_["employee_id"] is None
    assert open_["employee_name"] is None
    assert open_["status"] == "OPEN"
    assert (open_["role_id"], open_["date"], open_["start_time"]) == (
        "r1", "2026-08-31", "2026-08-31T09:00:00-04:00",
    )
    assert result["open_count"] == 1
    assert result["status"] == "ok"


def test_a_cap_trimmed_shift_is_open_and_no_longer_names_anyone():
    out = emit_result(_state([
        _shift("VACANT", status="VACANT", name="VACANT-Frequency Cap"),
        _shift("VACANT", status="VACANT", name="VACANT-Preference"),
    ]))
    shifts = out["draft_schedules"][0]["shifts"]
    assert [s["status"] for s in shifts] == ["OPEN", "OPEN"]
    assert all(s["employee_name"] is None for s in shifts)
    assert out["draft_schedules"][0]["open_count"] == 2


def test_a_conflict_stays_a_conflict():
    out = emit_result(_state([_shift("e1", status="CONFLICT")]))
    result = out["draft_schedules"][0]
    assert result["shifts"][0]["status"] == "CONFLICT"
    assert result["shifts"][0]["employee_id"] == "e1"
    assert result["open_count"] == 0
    assert result["status"] == "CONFLICT"


def test_no_vacancies_means_zero():
    out = emit_result(_state([_shift("e1")]))
    assert out["draft_schedules"][0]["open_count"] == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_emit.py -v`
Expected: FAIL: `KeyError: 'open_count'` and `assert 'VACANT' is None`.

- [ ] **Step 3: Convert at the boundary**

In `backend/scheduling/nodes.py`, add to the imports near the top:

```python
from backend.scheduling.open_shifts import OPEN_STATUS, is_open_entry, to_open_entry
```

In `emit_result`, directly after `shifts = state["current_parsed_shifts"]`, add:

```python
    # Unfilled slots leave the graph as open shifts (#174). Inside the graph
    # they stay the legacy VACANT placeholder that every node's checks are
    # written against; this is the one place that shape changes.
    shifts = [to_open_entry(s) if is_open_entry(s) else s for s in shifts]
    open_count = sum(1 for s in shifts if s["status"] == OPEN_STATUS)
```

In the `result: LocationResult = {...}` literal, add after `"signal_config": ...`:

```python
        "open_count": open_count,
```

- [ ] **Step 4: Run the new tests and the scheduler suites**

Run: `pytest tests/test_open_shifts_emit.py tests/test_annotate_preferences_node.py tests/test_multi_location_availability.py tests/test_preferences_ai_path.py tests/test_affinity_validation.py tests/test_avail_local_wallclock.py tests/test_preferences_local_scheduler.py tests/test_local_scheduler_integration.py -v`
Expected: all pass. The VACANT assertions in the existing files test node internals, which are unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/nodes.py tests/test_open_shifts_emit.py
git commit -m "feat: scheduler emits unfilled slots as open shifts with a count (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 3: Approval keeps open shifts; draft save accepts them

**Files:**
- Modify: `backend/routers/schedules.py`: `update_shifts` (~line 317), the approval loops (~lines 741-781 and ~799), `_shift_to_response` (~line 990)
- Modify: `tests/test_schedules_integration.py` (`test_approve_skips_vacant_shifts`, `test_approve_does_not_consume_availability_for_a_trimmed_shift`)
- Test: `tests/test_open_shifts_approval_integration.py`

**Interfaces:**
- Consumes: `is_open_entry`, `to_open_entry` (Task 1).
- Produces: approval writes `Shift(employee_id=None)` for open entries. `_shift_to_response` returns `employee_name=None` for an open shift.

- [ ] **Step 1: Write the failing tests**

`tests/test_open_shifts_approval_integration.py`:

```python
"""Approval keeps vacancies as open shifts (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import json
from datetime import date, datetime, timezone

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Shift, ShiftSchedule
from backend.models.employee_role_minutes import EmployeeRoleMinutes
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"


def _entry(employee_id, status="ok", name="Alice Johnson"):
    return {
        "employee_id": employee_id, "employee_name": name,
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-04-13",
        "start_time": "2026-04-13T09:00:00-04:00",
        "end_time": "2026-04-13T17:00:00-04:00", "status": status,
    }


async def _draft(db: AsyncSession, shifts: list[dict]) -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13), status="draft",
        raw_llm_output=json.dumps(shifts),
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return sid


async def _approve(client: AsyncClient, token: str, sid: str) -> dict:
    resp = await client.post(
        f"{BASE}/{sid}/approve", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_approve_turns_open_entries_into_open_shifts(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [
        _entry(EMPLOYEE1_ID),
        {**_entry(None, status="OPEN", name=None)},
    ])
    data = await _approve(client, manager_token, sid)
    by_employee = sorted(
        ((s["employee_id"], s["employee_name"]) for s in data["shifts"]),
        key=lambda p: p[0] or "",
    )
    assert by_employee == [(None, None), (EMPLOYEE1_ID, "")]
    rows = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == sid)
    )).scalars().all()
    assert sorted(r.employee_id or "" for r in rows) == ["", EMPLOYEE1_ID]


async def test_approve_turns_a_legacy_vacant_entry_into_an_open_shift(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry("VACANT", name="VACANT-Staffing Shortage")])
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]


async def test_approve_turns_a_legacy_trimmed_entry_into_an_open_shift_not_a_hold(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    """A pre-#174 cap trim could leave status VACANT on an entry that still
    names a real employee. It must become an OPEN shift, never a Shift row
    holding that employee's time."""
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID, status="VACANT")])
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]


async def test_approve_still_skips_conflict_entries(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID, status="CONFLICT")])
    data = await _approve(client, manager_token, sid)
    assert data["shifts"] == []


async def test_an_open_shift_books_no_fairness_minutes(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(None, status="OPEN", name=None)])
    await _approve(client, manager_token, sid)
    count = (await db_session.execute(
        select(func.count()).select_from(EmployeeRoleMinutes)
    )).scalar_one()
    assert count == 0


async def test_draft_save_accepts_an_open_entry(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID)])
    resp = await client.put(
        f"{BASE}/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": [_entry(None, status="OPEN", name=None)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["shifts"][0]["employee_id"] is None
    await db_session.refresh(await db_session.get(ShiftSchedule, sid))
    stored = json.loads((await db_session.get(ShiftSchedule, sid)).raw_llm_output)
    assert stored[0]["employee_id"] is None and stored[0]["status"] == "OPEN"


async def test_draft_save_normalises_an_empty_employee_to_open(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID)])
    resp = await client.put(
        f"{BASE}/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": [_entry("", status="ok", name="")]},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["shifts"][0]
    assert saved["employee_id"] is None and saved["status"] == "OPEN"
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_approval_integration.py -v`
Expected: FAIL. Approval returns `[]` for the open entries, and the draft save rejects or keeps `""`.

- [ ] **Step 3: Approval materialises open entries**

In `backend/routers/schedules.py`, add to the imports at the top of the module:

```python
from backend.scheduling.open_shifts import is_open_entry, to_open_entry
```

In `approve_schedule`, replace the head of the first loop:

```python
                for s in shifts_data:
                    # Skip VACANT placeholders and non-ok shifts
                    if s.get("status") != "ok" or s.get("employee_id") == "VACANT":
                        continue
                    # Skip shifts with missing required IDs
                    try:
                        loc_id = s["location_id"]
                        emp_id = s["employee_id"]
                        role_id = s["role_id"]
                    except KeyError:
                        continue
```

with:

```python
                for s in shifts_data:
                    # Open entries (and legacy VACANT ones) become open shifts
                    # (#174); anything else that is not "ok" -- a CONFLICT --
                    # is still never materialised.
                    open_entry = is_open_entry(s)
                    if not open_entry and s.get("status") != "ok":
                        continue
                    # Skip shifts with missing required IDs
                    try:
                        loc_id = s["location_id"]
                        role_id = s["role_id"]
                        emp_id = None if open_entry else s["employee_id"]
                    except KeyError:
                        continue
```

In the `Shift(...)` construction below it, replace:

```python
                        preference_violations=list(s.get("preference_violations") or []),
```

with:

```python
                        preference_violations=(
                            [] if open_entry
                            else list(s.get("preference_violations") or [])
                        ),
```

In the fairness-minutes loop, replace:

```python
            for s in shifts_data:
                if s.get("status") != "ok" or s.get("employee_id") == "VACANT":
                    continue
```

with:

```python
            for s in shifts_data:
                # An open shift is worked by no one: it books no minutes.
                if s.get("status") != "ok" or is_open_entry(s):
                    continue
```

- [ ] **Step 4: Draft save normalises open entries**

In `update_shifts`, replace:

```python
    shifts = [s.model_dump() for s in body.shifts]
```

with:

```python
    # A cleared or null employee is an open shift (#174); store one shape.
    shifts = [
        to_open_entry(d) if is_open_entry(d) else d
        for d in (s.model_dump() for s in body.shifts)
    ]
```

- [ ] **Step 5: The response names no one for an open shift**

In `_shift_to_response`, replace:

```python
    resp = ShiftResponse.model_validate(shift)
    if emp_names:
        resp.employee_name = emp_names.get(shift.employee_id, "")
```

with:

```python
    resp = ShiftResponse.model_validate(shift)
    if shift.employee_id is None:
        # An open shift (#174): the client renders the label, the API never
        # sends a display string for "no one".
        resp.employee_name = None
    elif emp_names:
        resp.employee_name = emp_names.get(shift.employee_id, "")
```

- [ ] **Step 6: Update the two existing approval tests to the new contract**

In `tests/test_schedules_integration.py`:

Rename `test_approve_skips_vacant_shifts` to `test_approve_keeps_a_vacant_placeholder_as_an_open_shift`. Change its docstring to `"""A VACANT placeholder becomes an open shift (#174), not nothing."""`. Replace its final assertion `assert resp.json()["shifts"] == []` with:

```python
    assert [s["employee_id"] for s in resp.json()["shifts"]] == [None]
```

In `test_approve_does_not_consume_availability_for_a_trimmed_shift`, replace:

```python
    # No Shift record for the trimmed assignment.
    assert resp.json()["shifts"] == []
```

with:

```python
    # The trimmed assignment becomes an open shift (#174) naming no one, so
    # it can never be a hold on EMPLOYEE1's time.
    assert [s["employee_id"] for s in resp.json()["shifts"]] == [None]
```

Leave the rest of that test (the availability assertions) unchanged.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_open_shifts_approval_integration.py tests/test_schedules_integration.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/routers/schedules.py tests/test_open_shifts_approval_integration.py tests/test_schedules_integration.py
git commit -m "feat: approval keeps vacancies as open shifts; drafts store them (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 4: Unassign, create and fill open shifts on an approved schedule

**Files:**
- Modify: `backend/routers/schedules.py` (`edit_approved_shifts`, ~lines 450-610)
- Modify: `backend/services/edit_warnings.py` (`_already_exported_warning`, emp_id selection)
- Test: `tests/test_open_shifts_edit_integration.py`

**Interfaces:**
- Consumes: `ApprovedShiftEdit.open`, `EditWarning.employee_id: str | None` (Task 1); `_at_location` (existing, `schedules.py`).
- Produces:
  - `PUT /schedules/{id}/approved-shifts` accepts `{"shift_id": X, "open": true}` (unassign) and `{"open": true, "role_id", "date", "start_time", "end_time"}` (create).
  - `open` together with `employee_id` → 400 `invalid_edit`.

- [ ] **Step 1: Write the failing tests**

`tests/test_open_shifts_edit_integration.py`:

```python
"""Open shifts on an approved schedule (#174): unassign, create, fill."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, EmployeeCheckIn, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"
NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)


@pytest_asyncio.fixture
async def approved(db_session: AsyncSession, seed_employees: list[Employee]) -> str:
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sid


async def _shift(db: AsyncSession, sid: str, employee_id: str | None, **extra) -> str:
    shift_id = _id()
    db.add(Shift(
        id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sid,
        location_id=LOCATION_ID, employee_id=employee_id,
        role_id=ROLE_FLOOR_ID, role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 17, tzinfo=NY), **extra,
    ))
    await db.commit()
    return shift_id


async def _edit(client, token, sid, edits):
    return await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {token}"},
        json={"edits": edits},
    )


async def test_open_true_unassigns_a_shift(client, manager_token, db_session, approved):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []
    db_session.expire_all()
    assert (await db_session.get(Shift, shift_id)).employee_id is None

    week = await client.get(
        f"{BASE}/week/{DAY.isoformat()}?status=approved",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    shift = week.json()[0]["shifts"][0]
    assert shift["employee_id"] is None and shift["employee_name"] is None


async def test_open_true_without_a_shift_id_creates_an_open_shift(
    client, manager_token, db_session, approved,
):
    resp = await _edit(client, manager_token, approved, [{
        "open": True, "role_id": ROLE_FLOOR_ID, "date": DAY.isoformat(),
        "start_time": "2026-08-31T09:00:00-04:00",
        "end_time": "2026-08-31T17:00:00-04:00",
    }])
    assert resp.status_code == 200, resp.text
    rows = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == approved)
    )).scalars().all()
    assert [r.employee_id for r in rows] == [None]


async def test_a_new_shift_without_an_offset_is_read_in_the_location_zone(
    client, manager_token, db_session, approved,
):
    resp = await _edit(client, manager_token, approved, [{
        "open": True, "role_id": ROLE_FLOOR_ID, "date": DAY.isoformat(),
        "start_time": "2026-08-31T09:00:00", "end_time": "2026-08-31T17:00:00",
    }])
    assert resp.status_code == 200, resp.text
    row = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == approved)
    )).scalar_one()
    # 09:00 in New York (EDT, UTC-4) is 13:00 UTC.
    assert row.start_time.astimezone(timezone.utc).hour == 13


async def test_open_with_an_employee_is_rejected(client, manager_token, db_session, approved):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    resp = await _edit(client, manager_token, approved, [
        {"shift_id": shift_id, "open": True, "employee_id": EMPLOYEE1_ID},
    ])
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_edit"


async def test_filling_an_open_shift_assigns_and_warns_as_usual(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(db_session, approved, None)
    resp = await _edit(client, manager_token, approved, [
        {"shift_id": shift_id, "employee_id": EMPLOYEE1_ID},
    ])
    assert resp.status_code == 200, resp.text
    # EMPLOYEE1 has no availability rows in this test.
    assert {w["code"] for w in resp.json()["warnings"]} == {"no_availability"}
    db_session.expire_all()
    assert (await db_session.get(Shift, shift_id)).employee_id == EMPLOYEE1_ID


async def test_unassigning_a_checked_in_shift_is_refused(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    db_session.add(EmployeeCheckIn(
        id=_id(), company_id=COMPANY_ID, location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID, shift_id=shift_id,
        checked_in_at=datetime.now(timezone.utc), local_date=DAY,
        counter=1, status="matched", minutes_from_start=0,
    ))
    await db_session.commit()
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_unassigning_an_exported_shift_warns_with_a_null_employee(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(
        db_session, approved, EMPLOYEE1_ID, exported_at=datetime.now(timezone.utc),
    )
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == [{
        "code": "already_exported", "shift_id": shift_id, "employee_id": None,
        "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
    }]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_edit_integration.py -v`
Expected: FAIL. `open` is ignored, so the shift keeps its employee; create returns 400 (employee_id required); the exported warning carries `"None"`.

- [ ] **Step 3: Edit warnings treat an open edit as "no one"**

In `backend/services/edit_warnings.py`, replace `_already_exported_warning`'s return with:

```python
    occupant = employee_id if employee_id is not None else shift.employee_id
    return {
        "code": "already_exported",
        "shift_id": str(shift.id),
        # None when the edit leaves the shift open (#174).
        "employee_id": str(occupant) if occupant is not None else None,
        "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
    }
```

Replace:

```python
        emp_id = edit.employee_id if edit.employee_id is not None else (
            shift.employee_id if shift is not None else None
        )
```

with:

```python
        # An edit that leaves the shift open (#174) assigns no one, so there
        # is no availability or booking to warn about.
        if getattr(edit, "open", False):
            emp_id = None
        else:
            emp_id = edit.employee_id if edit.employee_id is not None else (
                shift.employee_id if shift is not None else None
            )
```

For an open edit, `_already_exported_warning(shift, emp_id)` must report `None`, not the previous occupant. Replace the final block:

```python
        # 3. already_exported
        if shift is not None and shift.exported_at is not None:
            warnings.append(_already_exported_warning(shift, emp_id))
```

with:

```python
        # 3. already_exported
        if shift is not None and shift.exported_at is not None:
            w = _already_exported_warning(shift, emp_id)
            if getattr(edit, "open", False):
                w["employee_id"] = None
            warnings.append(w)
```

- [ ] **Step 4: The router applies `open`**

In `edit_approved_shifts`, at the top of `for idx, edit in enumerate(body.edits):` (before `if edit.employee_id is not None:`), add:

```python
            if edit.open and edit.employee_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_edit",
                        "index": idx,
                        "shift_id": edit.shift_id,
                        "reason": "open and employee_id are mutually exclusive",
                    },
                )
```

Replace `touched.add(str(shift.employee_id))` with:

```python
                if shift.employee_id is not None:
                    touched.add(str(shift.employee_id))
```

In the modify branch, directly after the `if edit.employee_id is not None:` block that sets `shift.employee_id = edit.employee_id`, add:

```python
                if edit.open:
                    # Unassign (#174): the slot stays, the person goes, and so
                    # do their preference asterisks.
                    shift.employee_id = None
                    shift.preference_violations = []
                    changed = True
```

In the create branch, replace the required-fields check:

```python
                if (
                    edit.employee_id is None
                    or edit.date is None
                    or edit.start_time is None
                    or edit.end_time is None
                ):
```

with:

```python
                if (
                    (edit.employee_id is None and not edit.open)
                    or edit.date is None
                    or edit.start_time is None
                    or edit.end_time is None
                ):
```

Change that block's `"reason"` to `"employee_id (or open), date, start_time and end_time are required to create a shift"`.

Replace the `db.add(Shift(...))` call and the `touched.add(str(edit.employee_id))` line after it with:

```python
                loc_tz = (await db.execute(
                    select(Location.timezone).where(Location.id == schedule.location_id)
                )).scalar_one_or_none()
                zone = ZoneInfo(loc_tz) if loc_tz else None
                db.add(Shift(
                    company_id=current_user.company_id,
                    shift_schedule_id=schedule.id,
                    location_id=schedule.location_id,
                    employee_id=None if edit.open else edit.employee_id,
                    role_id=edit.role_id,
                    role_name=role_name,
                    date=edit.date,
                    # A time without an offset is the location's wall clock,
                    # exactly as for an edit to an existing shift.
                    start_time=_at_location(edit.start_time, zone),
                    end_time=_at_location(edit.end_time, zone),
                ))
                if not edit.open:
                    touched.add(str(edit.employee_id))
```

`_at_location` and `ZoneInfo` are already defined or imported in this module. `Location` is imported at the module top; verify with `grep -n "^from backend.models" backend/routers/schedules.py`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_open_shifts_edit_integration.py tests/test_edit_approved_schedule_integration.py tests/test_edit_approved_warnings_integration.py tests/test_employee_timezone_edit_warnings_integration.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/routers/schedules.py backend/services/edit_warnings.py tests/test_open_shifts_edit_integration.py
git commit -m "feat: unassign, create and fill open shifts on approved schedules (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 5: Readers skip or carry open shifts

**Files:**
- Modify: `backend/scheduling/graph.py` (`_committed_shifts_by_employee` ~line 375, `_load_role_history_minutes` ~line 915)
- Modify: `backend/routers/export_schedules.py` (`ApprovedScheduleShift`, `Export7ShiftsResult`, `list_approved_schedules`, `export_to_7shifts`, `export_jsonl`)
- Test: `tests/test_open_shifts_readers_integration.py`

**Interfaces:**
- Produces:
  - `Export7ShiftsResult.open_shifts_skipped: int = 0`
  - `ApprovedScheduleShift.employee_id: str | None`, `ApprovedScheduleShift.employee_name: str | None`
  - JSONL rows carry `"employee_id": null, "employee_name": null` for open shifts.

- [ ] **Step 1: Write the failing tests**

`tests/test_open_shifts_readers_integration.py`:

```python
"""Every reader of shifts decides about open shifts explicitly (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Shift, ShiftSchedule
from backend.scheduling.graph import _committed_shifts_by_employee, _load_role_history_minutes
from backend.services.check_in import _match_shift
from backend.services.time_entries import _candidate_shifts
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)


@pytest_asyncio.fixture
async def week(db_session: AsyncSession, seed_employees: list[Employee]) -> dict:
    """An approved schedule with one filled and one open shift."""
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    ids = {}
    for key, emp in (("filled", EMPLOYEE1_ID), ("open", None)):
        ids[key] = _id()
        db_session.add(Shift(
            id=ids[key], company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=LOCATION_ID, employee_id=emp, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=DAY,
            start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
            end_time=datetime(2026, 8, 31, 17, tzinfo=NY),
        ))
    await db_session.commit()
    return {"schedule_id": sid, **ids}


async def test_holds_ignore_open_shifts(db_session, week):
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    held = await _committed_shifts_by_employee(
        db_session, COMPANY_ID, start, start + timedelta(days=7),
    )
    assert set(held) == {EMPLOYEE1_ID}


async def test_fairness_history_ignores_open_shifts(db_session, week):
    history = await _load_role_history_minutes(db_session, COMPANY_ID, "2026-09-07")
    assert {emp for emp, _ in history} == {EMPLOYEE1_ID}


async def test_check_in_never_matches_an_open_shift(db_session, week, seed_location):
    at = datetime(2026, 8, 31, 9, tzinfo=NY)
    shift, _ = await _match_shift(db_session, COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, at)
    assert shift is not None and shift.id == week["filled"]


async def test_payroll_derivation_skips_open_shifts(db_session, week):
    candidates = await _candidate_shifts(db_session, COMPANY_ID, DAY, DAY, None)
    assert [c.shift.id for c in candidates] == [week["filled"]]


async def test_7shifts_export_skips_open_shifts_and_says_so(client, manager_token, week):
    resp = await client.post(
        "/api/v1/export/7shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"access_token": "unused", "shift_ids": [week["open"]]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "exported": 0, "failed": 0, "errors": [], "open_shifts_skipped": 1,
    }


async def test_jsonl_export_includes_open_shifts_with_no_employee(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/jsonl/{week['schedule_id']}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    rows = {r["shift_id"]: r for r in map(json.loads, resp.text.strip().splitlines())}
    assert rows[week["open"]]["employee_id"] is None
    assert rows[week["open"]]["employee_name"] is None
    assert rows[week["filled"]]["employee_id"] == EMPLOYEE1_ID


async def test_approved_schedule_list_carries_open_shifts(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/approved-schedules?week_start_date={DAY.isoformat()}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    shifts = {s["id"]: s for s in resp.json()[0]["shifts"]}
    assert shifts[week["open"]]["employee_id"] is None
    assert shifts[week["open"]]["employee_name"] is None
```

If `_candidate_shifts` excludes shifts in the future, the test day 2026-08-31 is in the past relative to 2026-10-16, so it is included. `_Candidate.shift` is the attribute name; confirm with `grep -n "class _Candidate" -A8 backend/services/time_entries.py`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_readers_integration.py -v`
Expected:
- the holds and history tests fail (`'None'` key present)
- 7shifts fails (validation error on `employee_id`, or 400 "no external ID")
- JSONL and the list fail (`"None"` strings)
- check-in and payroll already pass, which pins them.

- [ ] **Step 3: Holds and history skip open shifts**

In `backend/scheduling/graph.py`, `_committed_shifts_by_employee`, add to the `.where(...)`:

```python
            # An open shift (#174) holds no one's time.
            Shift.employee_id.isnot(None),
```

In `_load_role_history_minutes`, add to its `.where(...)`:

```python
            # An open shift (#174) was worked by no one.
            Shift.employee_id.isnot(None),
```

- [ ] **Step 4: Exports**

In `backend/routers/export_schedules.py`:

`ApprovedScheduleShift`: change to `employee_id: str | None` and `employee_name: str | None`.

`Export7ShiftsResult`: add the field:

```python
    # Open shifts (#174) have no one to send; 7shifts needs a user_id.
    open_shifts_skipped: int = 0
```

`list_approved_schedules`, in the `ApprovedScheduleShift(...)` call, replace:

```python
                employee_id=str(s.employee_id),
                employee_name=emp_map.get(s.employee_id, str(s.employee_id)),
```

with:

```python
                employee_id=s.employee_id,
                employee_name=(
                    None if s.employee_id is None
                    else emp_map.get(s.employee_id, str(s.employee_id))
                ),
```

`export_to_7shifts`, directly after the `if not shifts: raise HTTPException(...404...)` block, add:

```python
    open_skipped = sum(1 for s in shifts if s.employee_id is None)
    shifts = [s for s in shifts if s.employee_id is not None]
    if not shifts:
        return Export7ShiftsResult(
            exported=0, failed=0, errors=[], open_shifts_skipped=open_skipped,
        )
```

Change the final return to:

```python
    return Export7ShiftsResult(
        exported=exported, failed=failed, errors=errors,
        open_shifts_skipped=open_skipped,
    )
```

`export_jsonl`, in the row dict, replace:

```python
            "employee_id": str(s.employee_id),
            "employee_name": emp_map.get(s.employee_id, ""),
```

with:

```python
            "employee_id": s.employee_id,
            "employee_name": (
                None if s.employee_id is None else emp_map.get(s.employee_id, "")
            ),
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_open_shifts_readers_integration.py tests/test_payroll_api_integration.py tests/test_check_in_service_integration.py tests/test_availability_holds_integration.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/scheduling/graph.py backend/routers/export_schedules.py tests/test_open_shifts_readers_integration.py
git commit -m "feat: holds, history and exports handle open shifts (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 6: One availability check, shared by edit warnings

**Files:**
- Create: `backend/services/availability_check.py`
- Modify: `backend/services/edit_warnings.py` (coverage block ~lines 150-215)
- Test: `tests/test_availability_check.py`, `tests/test_edit_warnings_blackout_integration.py`

**Interfaces:**
- Produces:
  - `window_dict(row) -> dict[str, str]`: an `EmployeeAvailability`-like object (attributes `start_time`, `end_time`) as `{"start": iso, "end": iso}`. Naive values are tagged UTC; an end at or before the start becomes 23:59 the same day.
  - `blackout_dicts(rows) -> list[dict[str, str]]`: `EmployeeDayBlackout`-like objects (attributes `day_of_week`, `start_time`, `end_time`) as `{"day": "Monday", "start": "HH:MM", "end": "HH:MM"}`; invalid weekdays are dropped.
  - `is_available(*, windows, other_spans, blackouts, span_start, span_end, emp_tz, loc_tz) -> bool`. `span_start`/`span_end` and `other_spans` are naive wall-clock faces at the location; `windows` are window dicts in the employee's frame; `blackouts` are blackout dicts in the employee's frame.

- [ ] **Step 1: Write the failing unit tests**

`tests/test_availability_check.py`:

```python
"""The one definition of "available" (#174), used by edit warnings and the
candidate ranking, matching the scheduler."""

from datetime import datetime
from types import SimpleNamespace
from datetime import timezone

from backend.services.availability_check import blackout_dicts, is_available, window_dict

SPAN = (datetime(2026, 8, 31, 9), datetime(2026, 8, 31, 17))  # a Monday


def _win(start_h, end_h, day=31):
    return {
        "start": datetime(2026, 8, day, start_h, tzinfo=timezone.utc).isoformat(),
        "end": datetime(2026, 8, day, end_h, tzinfo=timezone.utc).isoformat(),
    }


def _check(windows, other=(), blackouts=(), emp_tz=None, loc_tz=None):
    return is_available(
        windows=list(windows), other_spans=list(other), blackouts=list(blackouts),
        span_start=SPAN[0], span_end=SPAN[1], emp_tz=emp_tz, loc_tz=loc_tz,
    )


def test_a_window_covering_the_span_is_available():
    assert _check([_win(8, 18)])


def test_a_window_covering_part_of_the_span_is_not():
    assert not _check([_win(10, 18)])


def test_another_shift_inside_the_window_makes_it_unavailable():
    assert not _check([_win(8, 18)], other=[(datetime(2026, 8, 31, 12), datetime(2026, 8, 31, 14))])


def test_an_adjacent_shift_does_not():
    assert _check([_win(8, 18)], other=[(datetime(2026, 8, 31, 17), datetime(2026, 8, 31, 20))])


def test_an_overlapping_blackout_makes_it_unavailable():
    assert not _check([_win(8, 18)], blackouts=[{"day": "Monday", "start": "12:00", "end": "13:00"}])


def test_a_blackout_on_another_weekday_does_not():
    assert _check([_win(8, 18)], blackouts=[{"day": "Tuesday", "start": "12:00", "end": "13:00"}])


def test_a_blackout_touching_the_span_does_not():
    assert _check([_win(8, 18)], blackouts=[{"day": "Monday", "start": "17:00", "end": "19:00"}])


def test_employee_zone_windows_are_converted_to_the_location():
    # 06:00-14:00 in Los Angeles is 09:00-17:00 in New York.
    la = [_win(6, 14)]
    assert _check(la, emp_tz="America/Los_Angeles", loc_tz="America/New_York")
    assert not _check(la)


def test_employee_zone_blackouts_are_converted_to_the_location():
    # 06:00-07:00 in Los Angeles is 09:00-10:00 in New York: inside the span.
    blackout = [{"day": "Monday", "start": "06:00", "end": "07:00"}]
    assert not _check(
        [_win(0, 23)], blackouts=blackout,
        emp_tz="America/Los_Angeles", loc_tz="America/New_York",
    )


def test_window_dict_reads_a_midnight_end_as_end_of_day():
    row = SimpleNamespace(
        start_time=datetime(2026, 8, 31, 9), end_time=datetime(2026, 8, 31, 0),
    )
    assert window_dict(row) == {
        "start": "2026-08-31T09:00:00+00:00", "end": "2026-08-31T23:59:00+00:00",
    }


def test_blackout_dicts_names_the_weekday_and_drops_bad_ones():
    rows = [
        SimpleNamespace(day_of_week=0, start_time="20:00", end_time="22:00"),
        SimpleNamespace(day_of_week=9, start_time="20:00", end_time="22:00"),
    ]
    assert blackout_dicts(rows) == [{"day": "Monday", "start": "20:00", "end": "22:00"}]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_availability_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.availability_check'`.

- [ ] **Step 3: Write `backend/services/availability_check.py`**

```python
"""Whether an employee can work a span: the one definition (#174).

Used by the approved-edit `no_availability` warning and the open-shift
candidate ranking, and matching the scheduler (graph._load_employee_availability
+ nodes.validate_schedule): an availability window, minus the employee's other
shifts, must cover the whole span, and the span must miss every blackout.

Every datetime here is a naive wall-clock face at the shift's location. For
an employee with a timezone, their windows and blackouts are converted from
their zone into the location's (scheduling/employee_time.py) first.
"""

from datetime import datetime, timezone
from typing import Any, Iterable

from backend.scheduling.employee_time import (
    availability_to_location,
    blackouts_to_location,
    needs_conversion,
)
from backend.scheduling.nodes import _subtract_consumed, _wall_clock

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def window_dict(row: Any) -> dict[str, str]:
    """An availability row as the scheduler reads it.

    Availability is local wall-clock TAGGED UTC (#61): a naive value (SQLite)
    gets that tag back, never a conversion. An end at or before the start was
    stored as 00:00 meaning "end of day", so it becomes 23:59.
    """
    start, end = row.start_time, row.end_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        end = start.replace(hour=23, minute=59, second=0, microsecond=0)
    return {"start": start.isoformat(), "end": end.isoformat()}


def blackout_dicts(rows: Iterable[Any]) -> list[dict[str, str]]:
    """Blackout rows in the scheduler's {"day", "start", "end"} shape."""
    out: list[dict[str, str]] = []
    for row in rows:
        dow = int(row.day_of_week)
        if 0 <= dow <= 6:
            out.append({"day": DAY_NAMES[dow], "start": row.start_time, "end": row.end_time})
    return out


def _blocked_by_blackout(
    blackouts: list[dict[str, str]],
    span_start: datetime,
    span_end: datetime,
    emp_tz: str | None,
    loc_tz: str | None,
) -> bool:
    if needs_conversion(emp_tz, loc_tz):
        blackouts = blackouts_to_location(
            blackouts, span_start.date().isoformat(), 1, emp_tz, loc_tz,
        )
    day = DAY_NAMES[span_start.weekday()]
    s_hm, e_hm = span_start.strftime("%H:%M"), span_end.strftime("%H:%M")
    for bo in blackouts:
        if bo.get("day") != day:
            continue
        bo_s, bo_e = bo.get("start", ""), bo.get("end", "")
        # Same strict HH:MM overlap as nodes.validate_schedule step 5b.
        if bo_s and bo_e and s_hm < bo_e and bo_s < e_hm:
            return True
    return False


def is_available(
    *,
    windows: list[dict[str, str]],
    other_spans: list[tuple[datetime, datetime]],
    blackouts: list[dict[str, str]],
    span_start: datetime,
    span_end: datetime,
    emp_tz: str | None,
    loc_tz: str | None,
) -> bool:
    """True when the employee can work [span_start, span_end)."""
    if _blocked_by_blackout(blackouts, span_start, span_end, emp_tz, loc_tz):
        return False
    for piece in availability_to_location(windows, emp_tz, loc_tz):
        w_start, w_end = _wall_clock(piece["start"]), _wall_clock(piece["end"])
        for free_start, free_end in _subtract_consumed(w_start, w_end, other_spans):
            if free_start <= span_start and free_end >= span_end:
                return True
    return False
```

- [ ] **Step 4: Run the unit tests**

Run: `pytest tests/test_availability_check.py -v`
Expected: 11 passed.

- [ ] **Step 5: Write the failing edit-warnings blackout test**

`tests/test_edit_warnings_blackout_integration.py`:

```python
"""The no_availability warning now honours blackouts, like the scheduler (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from backend.models import EmployeeAvailability, EmployeeDayBlackout, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")


async def test_assigning_into_a_blackout_warns_no_availability(
    client, manager_token, db_session, seed_employees,
):
    sid, shift_id = _id(), _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 8, 31), status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    db_session.add_all([
        Shift(
            id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=LOCATION_ID, employee_id=None, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=date(2026, 8, 31),
            start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
            end_time=datetime(2026, 8, 31, 17, tzinfo=NY),
        ),
        EmployeeAvailability(
            id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
            year=2026, month=8, day=31,
            start_time=datetime(2026, 8, 31, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
        ),
        EmployeeDayBlackout(
            id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
            day_of_week=0, start_time="12:00", end_time="13:00",
        ),
    ])
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/schedules/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": EMPLOYEE1_ID}]},
    )
    assert resp.status_code == 200, resp.text
    assert {w["code"] for w in resp.json()["warnings"]} == {"no_availability"}
```

Run: `pytest tests/test_edit_warnings_blackout_integration.py -v`
Expected: FAIL. No warnings yet, because blackouts are ignored.

- [ ] **Step 6: Edit warnings use the shared check**

In `backend/services/edit_warnings.py`:

Change the model import to `from backend.models import Employee, EmployeeAvailability, EmployeeDayBlackout, Location, Shift`.

Replace `from backend.scheduling.employee_time import availability_to_location, needs_conversion` with `from backend.scheduling.employee_time import needs_conversion`.

Replace `from backend.scheduling.nodes import _subtract_consumed, _wall_clock` with `from backend.scheduling.nodes import _wall_clock`.

Add `from backend.services.availability_check import blackout_dicts, is_available, window_dict`.

Replace the comment and statement:

```python
            # 1. no_availability -- the employee's windows, minus their other
            #    shifts, must cover the span. Same definition the pipeline
            #    uses.
            windows = (await db.execute(
                select(EmployeeAvailability).where(
                    EmployeeAvailability.company_id == company_id,
                    EmployeeAvailability.employee_id == emp_id,
                )
            )).scalars().all()
```

with:

```python
            # 1. no_availability -- the employee's windows, minus their other
            #    shifts, must cover the span, and the span must miss their
            #    blackouts. The one definition, shared with the scheduler and
            #    the candidate ranking (services/availability_check.py).
            windows = [
                window_dict(w) for w in (await db.execute(
                    select(EmployeeAvailability).where(
                        EmployeeAvailability.company_id == company_id,
                        EmployeeAvailability.employee_id == emp_id,
                    )
                )).scalars().all()
            ]
            blackouts = blackout_dicts((await db.execute(
                select(EmployeeDayBlackout).where(
                    EmployeeDayBlackout.company_id == company_id,
                    EmployeeDayBlackout.employee_id == emp_id,
                )
            )).scalars().all())
```

Replace the whole block from `covered = False` down to and including `if not covered:` with:

```python
            if not is_available(
                windows=windows,
                other_spans=other_spans,
                blackouts=blackouts,
                span_start=span_start,
                span_end=span_end,
                # Without conversion, windows and blackouts are already this
                # location's wall clock: pass no zones so nothing converts.
                emp_tz=emp_tz if convert else None,
                loc_tz=zone_location.timezone if convert else None,
            ):
```

Keep the `warnings.append({"code": "no_availability", ...})` body that followed `if not covered:` as the body of this `if`.

- [ ] **Step 7: Run every edit-warning suite**

Run: `pytest tests/test_availability_check.py tests/test_edit_warnings_blackout_integration.py tests/test_edit_approved_warnings_integration.py tests/test_employee_timezone_edit_warnings_integration.py tests/test_open_shifts_edit_integration.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/services/availability_check.py backend/services/edit_warnings.py tests/test_availability_check.py tests/test_edit_warnings_blackout_integration.py
git commit -m "refactor: one availability check; edit warnings now honour blackouts (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 7: Candidate ranking service

**Files:**
- Create: `backend/services/candidates.py`
- Test: `tests/test_open_shifts_candidates_integration.py` (service-level part, extended in Task 8)

**Interfaces:**
- Consumes: `is_available`, `window_dict`, `blackout_dicts` (Task 6); `_shift_local_face` (`scheduling/graph.py`); `needs_conversion`; `is_valid_timezone` (`backend/utils/tz.py`).
- Produces:
  - `Candidate` (frozen dataclass): `employee_id: str, full_name: str, tier: int, available: bool, has_role: bool, at_location: bool`. `rank_candidates` returns only tiers 1–3, so every returned candidate has `at_location=True`.
  - `tier_for(*, available: bool, has_role: bool, at_location: bool) -> int`
  - `slot_span(day: date, start_hm: str, end_hm: str) -> tuple[datetime, datetime]`
  - `async rank_candidates(db, company_id: str, location: Location, role_id: str, span_start: datetime, span_end: datetime, exclude_shift_id: str | None = None) -> list[Candidate]`

- [ ] **Step 1: Write the failing tests**

`tests/test_open_shifts_candidates_integration.py`:

```python
"""Ranking who fits an open shift (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeAvailability, EmployeeDayBlackout, EmployeeRole,
    Location, Shift, ShiftSchedule,
)
from backend.services.candidates import rank_candidates, slot_span, tier_for
from tests.conftest import COMPANY_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)  # a Monday
SPAN = (datetime(2026, 8, 31, 9), datetime(2026, 8, 31, 17))


def test_tiers_follow_the_agreed_order():
    assert tier_for(available=True, has_role=True, at_location=True) == 1
    assert tier_for(available=False, has_role=True, at_location=True) == 2
    assert tier_for(available=True, has_role=False, at_location=True) == 3
    assert tier_for(available=False, has_role=False, at_location=True) == 4
    assert tier_for(available=True, has_role=True, at_location=False) == 4


def test_slot_span_rolls_an_overnight_end_to_the_next_day():
    start, end = slot_span(DAY, "22:00", "06:00")
    assert start == datetime(2026, 8, 31, 22)
    assert end == datetime(2026, 9, 1, 6)


def _availability(emp_id: str, start_h: int = 0, end_h: int = 23) -> EmployeeAvailability:
    """Local wall-clock tagged UTC (#61)."""
    return EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=emp_id,
        year=2026, month=8, day=31,
        start_time=datetime(2026, 8, 31, start_h, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, end_h, 59 if end_h == 23 else 0, tzinfo=timezone.utc),
    )


async def _employee(
    db: AsyncSession, name: str, *, at_location=True, role=True,
    available=True, timezone_name: str | None = None,
) -> str:
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name=name,
        location_ids=[LOCATION_ID] if at_location else [],
        timezone=timezone_name,
    )
    db.add(emp)
    await db.flush()
    if role:
        db.add(EmployeeRole(
            id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
            role_id=ROLE_FLOOR_ID, skill_level=3,
        ))
    if available:
        db.add(_availability(emp.id))
    await db.flush()
    return emp.id


@pytest_asyncio.fixture
async def roster(db_session: AsyncSession, seed_location: Location, seed_roles) -> dict:
    ids = {
        "ana": await _employee(db_session, "Ana Best"),
        "ben": await _employee(db_session, "Ben Unavailable", available=False),
        "cara": await _employee(db_session, "Cara NoRole", role=False),
        "dan": await _employee(db_session, "Dan Elsewhere", at_location=False),
        "eve": await _employee(db_session, "Eve Blackout"),
        "finn": await _employee(db_session, "Finn Booked"),
    }
    db_session.add(EmployeeDayBlackout(
        id=_id(), company_id=COMPANY_ID, employee_id=ids["eve"],
        day_of_week=0, start_time="12:00", end_time="13:00",
    ))
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    ids["finn_shift"] = _id()
    db_session.add(Shift(
        id=ids["finn_shift"], company_id=COMPANY_ID, shift_schedule_id=sid,
        location_id=LOCATION_ID, employee_id=ids["finn"], role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 8, 31, 10, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 14, tzinfo=NY),
    ))
    await db_session.commit()
    return ids


async def _rank(db, exclude=None):
    location = await db.get(Location, LOCATION_ID)
    return await rank_candidates(
        db, COMPANY_ID, location, ROLE_FLOOR_ID, *SPAN, exclude_shift_id=exclude,
    )


async def test_each_tier_gets_the_right_people_in_order(db_session, roster):
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [
        ("Ana Best", 1),
        ("Ben Unavailable", 2),
        ("Eve Blackout", 2),
        ("Finn Booked", 2),
        ("Cara NoRole", 3),
    ]
    # Dan is not at the location: tier 4 is not offered.
    assert "Dan Elsewhere" not in {c.full_name for c in ranked}
    cara = next(c for c in ranked if c.full_name == "Cara NoRole")
    assert (cara.available, cara.has_role, cara.at_location) == (True, False, True)


async def test_exclude_shift_id_stops_a_shift_clashing_with_itself(db_session, roster):
    ranked = await _rank(db_session, exclude=roster["finn_shift"])
    finn = next(c for c in ranked if c.full_name == "Finn Booked")
    assert finn.tier == 1


async def test_employee_timezone_is_converted(db_session, seed_location, seed_roles):
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name="Lou LA",
        location_ids=[LOCATION_ID], timezone="America/Los_Angeles",
    )
    db_session.add(emp)
    await db_session.flush()
    db_session.add_all([
        EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                     role_id=ROLE_FLOOR_ID, skill_level=3),
        # 06:00-14:00 Los Angeles = 09:00-17:00 New York.
        _availability(emp.id, 6, 14),
    ])
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [("Lou LA", 1)]


async def test_invalid_employee_timezone_degrades_to_no_timezone(
    db_session, seed_location, seed_roles,
):
    await _employee(db_session, "Zed BadZone", timezone_name="Not/AZone")
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [("Zed BadZone", 1)]


async def test_another_companys_employees_never_appear(
    db_session, roster, other_company_employee_id,
):
    ranked = await _rank(db_session)
    assert other_company_employee_id not in {c.employee_id for c in ranked}
```

`other_company_employee_id` is an existing fixture in `tests/conftest.py:497`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_candidates_integration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.candidates'`.

- [ ] **Step 3: Write `backend/services/candidates.py`**

```python
"""Who fits an open shift, in the order a manager should see them (#174).

Tiers:
  1. available, has the role, at the location
  2. has the role, at the location, not available
  3. available, at the location, no role
Alphabetical within a tier. Everyone else (tier 4) is not offered: the
picker shows only people who fit the location. "Available" is services/availability_check.py --
the same definition as the scheduler and the no_availability edit warning.

Bulk-loaded in five queries regardless of company size: employees, employee
roles, availability windows, blackouts, and nearby shifts. Scoring is Python.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee,
    EmployeeAvailability,
    EmployeeDayBlackout,
    EmployeeRole,
    Location,
    Shift,
)
from backend.scheduling.employee_time import needs_conversion
from backend.scheduling.graph import _shift_local_face
from backend.services.availability_check import blackout_dicts, is_available, window_dict
from backend.utils.tz import is_valid_timezone


@dataclass(frozen=True)
class Candidate:
    employee_id: str
    full_name: str
    tier: int
    available: bool
    has_role: bool
    at_location: bool


def tier_for(*, available: bool, has_role: bool, at_location: bool) -> int:
    if at_location and has_role and available:
        return 1
    if at_location and has_role:
        return 2
    if at_location and available:
        return 3
    return 4


def slot_span(day: date, start_hm: str, end_hm: str) -> tuple[datetime, datetime]:
    """A slot's naive wall-clock span at its location. An end at or before
    the start is the next day (an overnight shift)."""
    start = datetime.combine(day, time.fromisoformat(start_hm))
    end = datetime.combine(day, time.fromisoformat(end_hm))
    if end <= start:
        end += timedelta(days=1)
    return start, end


async def rank_candidates(
    db: AsyncSession,
    company_id: str,
    location: Location,
    role_id: str,
    span_start: datetime,
    span_end: datetime,
    exclude_shift_id: str | None = None,
) -> list[Candidate]:
    day = span_start.date()
    # A day either side: an employee-zone window or a shift instant can sit
    # on the neighbouring UTC date while landing on this one locally.
    lo = datetime.combine(day - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(day + timedelta(days=2), time.min, tzinfo=timezone.utc)

    employees = (await db.execute(
        select(Employee).where(Employee.company_id == company_id)
    )).scalars().all()

    with_role = {
        str(eid) for eid in (await db.execute(
            select(EmployeeRole.employee_id).where(
                EmployeeRole.company_id == company_id,
                EmployeeRole.role_id == role_id,
            )
        )).scalars().all()
    }

    windows: dict[str, list[dict[str, str]]] = {}
    for row in (await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.company_id == company_id,
            EmployeeAvailability.start_time >= lo,
            EmployeeAvailability.start_time < hi,
        )
    )).scalars().all():
        windows.setdefault(str(row.employee_id), []).append(window_dict(row))

    blackouts: dict[str, list[dict[str, str]]] = {}
    for row in (await db.execute(
        select(EmployeeDayBlackout).where(EmployeeDayBlackout.company_id == company_id)
    )).scalars().all():
        blackouts.setdefault(str(row.employee_id), []).extend(blackout_dicts([row]))

    shift_query = (
        select(Shift, Location)
        .join(Location, Shift.location_id == Location.id)
        .where(
            Shift.company_id == company_id,
            Location.company_id == company_id,
            Shift.employee_id.isnot(None),
            Shift.start_time >= lo,
            Shift.start_time < hi,
        )
    )
    if exclude_shift_id:
        shift_query = shift_query.where(Shift.id != exclude_shift_id)
    shifts: dict[str, list[tuple[Shift, Location]]] = {}
    for shift, loc in (await db.execute(shift_query)).all():
        shifts.setdefault(str(shift.employee_id), []).append((shift, loc))

    ranked: list[Candidate] = []
    for emp in employees:
        eid = str(emp.id)
        emp_tz = emp.timezone if is_valid_timezone(emp.timezone) else None
        convert = needs_conversion(emp_tz, location.timezone)
        # Same faces as edit_warnings: converted into this location's zone
        # for an employee with a timezone, else each shift's own location.
        other_spans = [
            face for face in (
                _shift_local_face(s, location if convert else loc)
                for s, loc in shifts.get(eid, [])
            ) if face is not None
        ]
        available = is_available(
            windows=windows.get(eid, []),
            other_spans=other_spans,
            blackouts=blackouts.get(eid, []),
            span_start=span_start,
            span_end=span_end,
            emp_tz=emp_tz if convert else None,
            loc_tz=location.timezone if convert else None,
        )
        has_role = eid in with_role
        at_location = str(location.id) in {str(x) for x in (emp.location_ids or [])}
        ranked.append(Candidate(
            employee_id=eid,
            full_name=emp.full_name,
            tier=tier_for(available=available, has_role=has_role, at_location=at_location),
            available=available,
            has_role=has_role,
            at_location=at_location,
        ))

    # Tier 4 -- not at the location, or at it with neither the role nor
    # availability -- is not offered for an open shift.
    ranked = [c for c in ranked if c.tier < 4]
    ranked.sort(key=lambda c: (c.tier, c.full_name.lower()))
    return ranked
```

`is_valid_timezone(None)` must return False. Check with `grep -n "def is_valid_timezone" -A12 backend/utils/tz.py`. If it raises on None, use `emp_tz = emp.timezone if emp.timezone and is_valid_timezone(emp.timezone) else None`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_open_shifts_candidates_integration.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/candidates.py tests/test_open_shifts_candidates_integration.py
git commit -m "feat: rank candidates for an open shift in four tiers (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 8: Candidates and open-shift-count endpoints

**Files:**
- Create: `backend/routers/open_shifts.py`
- Modify: `backend/main.py:113` (register before `schedules.router`)
- Test: append to `tests/test_open_shifts_candidates_integration.py`

**Interfaces:**
- Consumes: `rank_candidates`, `slot_span` (Task 7).
- Produces:
  - `POST /api/v1/schedules/candidates`, body `{location_id, role_id, date, start_time: "HH:MM", end_time: "HH:MM", exclude_shift_id?}`, returns `[{employee_id, full_name, tier, available, has_role, at_location}]`.
  - `GET /api/v1/schedules/open-shifts/count` returns `{"count": int}`.

- [ ] **Step 1: Append the failing endpoint tests**

Append to `tests/test_open_shifts_candidates_integration.py`:

```python
from datetime import timedelta

from sqlalchemy import event

from tests.conftest import COMPANY2_ID, _get_test_engine


async def _post_candidates(client, token, **overrides):
    body = {
        "location_id": LOCATION_ID, "role_id": ROLE_FLOOR_ID,
        "date": DAY.isoformat(), "start_time": "09:00", "end_time": "17:00",
        **overrides,
    }
    return await client.post(
        "/api/v1/schedules/candidates",
        headers={"Authorization": f"Bearer {token}"}, json=body,
    )


async def test_candidates_endpoint_returns_the_ranking(client, manager_token, roster):
    resp = await _post_candidates(client, manager_token)
    assert resp.status_code == 200, resp.text
    first = resp.json()[0]
    assert first == {
        "employee_id": roster["ana"], "full_name": "Ana Best", "tier": 1,
        "available": True, "has_role": True, "at_location": True,
    }


async def test_candidates_for_another_companys_location_is_404(
    client, manager_token, roster, db_session,
):
    resp = await _post_candidates(client, manager_token, location_id="nosuchlc")
    assert resp.status_code == 404


async def test_candidates_reject_a_malformed_time(client, manager_token, roster):
    resp = await _post_candidates(client, manager_token, start_time="9am")
    assert resp.status_code == 422


async def test_candidates_are_manager_only(client, employee_token, roster):
    resp = await _post_candidates(client, employee_token)
    assert resp.status_code == 403


async def test_query_count_does_not_grow_with_the_roster(
    client, manager_token, db_session, roster,
):
    engine = _get_test_engine()
    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        await _post_candidates(client, manager_token)
        small = len(statements)
        for i in range(10):
            await _employee(db_session, f"Extra {i:02d}")
        await db_session.commit()
        statements.clear()
        await _post_candidates(client, manager_token)
        large = len(statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
    assert large == small


async def _open_shift(db, sid, start: datetime, company_id=COMPANY_ID, location_id=LOCATION_ID):
    db.add(Shift(
        id=_id(), company_id=company_id, shift_schedule_id=sid,
        location_id=location_id, employee_id=None, role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=start.date(),
        start_time=start, end_time=start + timedelta(hours=8),
    ))


async def test_open_shift_count_covers_the_next_14_days_of_approved_schedules(
    client, manager_token, db_session, seed_location, seed_roles,
):
    now = datetime.now(timezone.utc)
    approved, draft = _id(), _id()
    for sid, status_ in ((approved, "approved"), (draft, "draft")):
        db_session.add(ShiftSchedule(
            id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
            week_start_date=now.date(), status=status_, created_at=now,
        ))
    await db_session.flush()
    await _open_shift(db_session, approved, now + timedelta(days=1))    # counted
    await _open_shift(db_session, approved, now + timedelta(days=13))   # counted
    await _open_shift(db_session, approved, now + timedelta(days=20))   # too far
    await _open_shift(db_session, approved, now - timedelta(days=1))    # past
    await _open_shift(db_session, draft, now + timedelta(days=1))       # not approved
    await db_session.commit()

    resp = await client.get(
        "/api/v1/schedules/open-shifts/count",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"count": 2}
```

The count's company scoping is covered by the `Shift.company_id` filter. Seeding a second company's full location chain here would add a lot of fixture code for little value.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_open_shifts_candidates_integration.py -v`
Expected: the new endpoint tests FAIL with 404/405 (no route); the Task 7 tests still pass.

- [ ] **Step 3: Write `backend/routers/open_shifts.py`**

```python
"""Open shifts (#174): who fits one, and how many are coming up."""

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, Shift, ShiftSchedule, User
from backend.services.candidates import rank_candidates, slot_span

router = APIRouter(prefix="/schedules", tags=["schedules"])

# How far ahead the Sidebar badge looks for open shifts.
OPEN_SHIFT_BADGE_DAYS = 14

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class CandidatesRequest(BaseModel):
    location_id: str
    role_id: str
    date: date
    # Local wall-clock at the location. An end at or before the start is the
    # next day.
    start_time: str = Field(pattern=_HHMM)
    end_time: str = Field(pattern=_HHMM)
    # The shift being edited, so its own assignment is not a clash.
    exclude_shift_id: str | None = None


class CandidateResponse(BaseModel):
    employee_id: str
    full_name: str
    tier: int
    available: bool
    has_role: bool
    at_location: bool


class OpenShiftCount(BaseModel):
    count: int


@router.post("/candidates", response_model=list[CandidateResponse])
async def list_candidates(
    body: CandidatesRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateResponse]:
    location = (await db.execute(
        select(Location).where(
            Location.id == body.location_id,
            Location.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    span_start, span_end = slot_span(body.date, body.start_time, body.end_time)
    ranked = await rank_candidates(
        db, str(current_user.company_id), location, body.role_id,
        span_start, span_end, body.exclude_shift_id,
    )
    return [CandidateResponse(**asdict(c)) for c in ranked]


@router.get("/open-shifts/count", response_model=OpenShiftCount)
async def open_shift_count(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> OpenShiftCount:
    now = datetime.now(timezone.utc)
    count = (await db.execute(
        select(func.count())
        .select_from(Shift)
        .join(ShiftSchedule, Shift.shift_schedule_id == ShiftSchedule.id)
        .where(
            Shift.company_id == current_user.company_id,
            ShiftSchedule.status == "approved",
            Shift.employee_id.is_(None),
            Shift.start_time >= now,
            Shift.start_time < now + timedelta(days=OPEN_SHIFT_BADGE_DAYS),
        )
    )).scalar_one()
    return OpenShiftCount(count=count)
```

- [ ] **Step 4: Register the router**

In `backend/main.py`, add `open_shifts` to the routers import list (the same import that brings in `schedules`). Insert directly before `app.include_router(schedules.router, prefix=api_prefix)`:

```python
    # Before schedules.router: its /{schedule_id} routes must not swallow
    # /candidates or /open-shifts/count.
    app.include_router(open_shifts.router, prefix=api_prefix)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_open_shifts_candidates_integration.py tests/test_schedules_integration.py -v`
Expected: all pass.

- [ ] **Step 6: Run the whole backend suite once**

Run: `pytest tests/ -x -q`
Expected: all pass. Fix any test that compared an approved-shift `employee_id` to `str(...)` of a value now `None`.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/open_shifts.py backend/main.py tests/test_open_shifts_candidates_integration.py
git commit -m "feat: candidates and open-shift-count endpoints (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 9: Frontend foundation: types, helpers, API, strings

**Files:**
- Create: `frontend/src/utils/openShifts.ts`, `frontend/src/utils/openShifts.test.ts`, `frontend/src/api/openShifts.ts`
- Modify: `frontend/src/types/index.ts:302-324`, `frontend/src/api/approvedSchedules.ts`, `frontend/src/api/exportSchedules.ts`
- Modify: all 19 `frontend/src/i18n/*.ts` (`schedule` and `exportSchedules` blocks)

**Interfaces:**
- Produces (TypeScript):
  - `ShiftAssignment.employee_id: string | null`, `ShiftAssignment.employee_name: string | null`, `LocationResult.open_count?: number`
  - `WeekShift.employee_id: string | null`, `WeekShift.employee_name: string | null`
  - `ApprovedShiftEdit.open?: boolean`; `EditWarning.employee_id: string | null`
  - `Export7ShiftsResult.open_shifts_skipped: number`; the export shift type's `employee_id`/`employee_name: string | null`
  - `utils/openShifts.ts`:
    - `type Tier = 1 | 2 | 3`
    - `interface Candidate { employee_id: string; full_name: string; tier: Tier; available: boolean; has_role: boolean; at_location: boolean }`
    - `isOpenShift(s: { employee_id: string | null; status?: string }): boolean`
    - `countOpen(shifts): number`
    - `toOpenAssignment(s: ShiftAssignment): ShiftAssignment`
    - `interface CandidateGroup { tier: Tier; candidates: Candidate[] }`
    - `groupCandidates(c: Candidate[], query: string): CandidateGroup[]`
    - `type MissingFlag = "availability" | "role"`
    - `missingFlags(c: Candidate): MissingFlag[]`
  - `api/openShifts.ts`:
    - `interface CandidateSlot { location_id: string; role_id: string; date: string; start_time: string; end_time: string; exclude_shift_id?: string | null }`
    - `fetchCandidates(slot): Promise<Candidate[]>`
    - `getOpenShiftCount(): Promise<number>`
    - `OPEN_SHIFTS_CHANGED = "wiz:open-shifts-changed"`
    - `notifyOpenShiftsChanged(): void`
  - i18n keys `t.schedule.{openShift, openShiftsBanner, openShiftsBannerOne, addOpenShift, addOpenShiftTitle, leaveOpen, tierBestFit, tierRoleNotAvailable, tierAvailableNoRole, missingAvailability, missingRole, candidatesError}` and `t.exportSchedules.openShiftsSkipped`.

- [ ] **Step 1: Write the failing helper tests**

`frontend/src/utils/openShifts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ShiftAssignment } from "../types";
import {
  countOpen,
  groupCandidates,
  isOpenShift,
  missingFlags,
  toOpenAssignment,
  type Candidate,
} from "./openShifts";

const shift = (over: Partial<ShiftAssignment> = {}): ShiftAssignment => ({
  employee_id: "e1",
  employee_name: "Ana",
  role_id: "r1",
  role_name: "Cook",
  location_id: "l1",
  date: "2026-08-31",
  start_time: "2026-08-31T09:00:00-04:00",
  end_time: "2026-08-31T17:00:00-04:00",
  status: "ok",
  ...over,
});

const cand = (name: string, tier: 1 | 2 | 3, over: Partial<Candidate> = {}): Candidate => ({
  employee_id: name.toLowerCase(),
  full_name: name,
  tier,
  available: true,
  has_role: true,
  at_location: true,
  ...over,
});

describe("isOpenShift", () => {
  it("is true for a null employee", () => {
    expect(isOpenShift(shift({ employee_id: null }))).toBe(true);
  });
  it("is true for a legacy VACANT draft entry", () => {
    expect(isOpenShift(shift({ employee_id: "VACANT", status: "VACANT" }))).toBe(true);
  });
  it("is true for status OPEN", () => {
    expect(isOpenShift(shift({ employee_id: null, status: "OPEN" }))).toBe(true);
  });
  it("is false for a filled shift", () => {
    expect(isOpenShift(shift())).toBe(false);
  });
  it("is never true for a conflict", () => {
    expect(isOpenShift(shift({ employee_id: "VACANT", status: "CONFLICT" }))).toBe(false);
  });
});

describe("countOpen", () => {
  it("counts only open shifts", () => {
    expect(countOpen([shift(), shift({ employee_id: null }), shift({ employee_id: "VACANT" })])).toBe(2);
  });
});

describe("toOpenAssignment", () => {
  it("keeps the slot and clears the person", () => {
    const out = toOpenAssignment(shift({ preference_violations: [{ kind: "day" } as never] }));
    expect(out).toMatchObject({ employee_id: null, employee_name: null, status: "OPEN", preference_violations: [] });
    expect(out.role_id).toBe("r1");
    expect(out.start_time).toBe("2026-08-31T09:00:00-04:00");
  });
});

describe("groupCandidates", () => {
  const all = [cand("Ana", 1), cand("Ben", 2), cand("Bea", 2), cand("Dan", 3)];
  it("groups by tier in tier order and drops empty tiers", () => {
    expect(groupCandidates(all, "").map((g) => [g.tier, g.candidates.map((c) => c.full_name)])).toEqual([
      [1, ["Ana"]],
      [2, ["Ben", "Bea"]],
      [3, ["Dan"]],
    ]);
  });
  it("filters by name within groups, case-insensitively", () => {
    expect(groupCandidates(all, "be").map((g) => [g.tier, g.candidates.map((c) => c.full_name)])).toEqual([
      [2, ["Ben", "Bea"]],
    ]);
  });
});

describe("missingFlags", () => {
  it("lists what keeps someone out of tier 1", () => {
    expect(missingFlags(cand("X", 2, { available: false }))).toEqual(["availability"]);
    expect(missingFlags(cand("Z", 3, { has_role: false }))).toEqual(["role"]);
    expect(missingFlags(cand("Y", 1))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && npx vitest run src/utils/openShifts.test.ts`
Expected: FAIL. The module `./openShifts` is not found.

- [ ] **Step 3: Write `frontend/src/utils/openShifts.ts`**

```ts
import type { ShiftAssignment } from "../types";

/** Open shifts (#174): a shift with no one on it yet.
 *
 *  An open shift has `employee_id === null`. Drafts saved before #174 hold
 *  the legacy `"VACANT"` entry instead, so it is read as open too. A
 *  CONFLICT is an error, never a vacancy. */
export const LEGACY_VACANT = "VACANT";

/** 1 = available + role + location, 2 = role + location, 3 = available +
 *  location. Nobody else is offered (#174). */
export type Tier = 1 | 2 | 3;

/** One row of `POST /schedules/candidates`. The server ranks; the client
 *  never evaluates availability itself. */
export interface Candidate {
  employee_id: string;
  full_name: string;
  tier: Tier;
  available: boolean;
  has_role: boolean;
  at_location: boolean;
}

export function isOpenShift(s: { employee_id: string | null; status?: string }): boolean {
  if (s.status === "CONFLICT") return false;
  if (s.status === "OPEN" || s.status === LEGACY_VACANT) return true;
  return s.employee_id === null || s.employee_id === "" || s.employee_id === LEGACY_VACANT;
}

export function countOpen(shifts: { employee_id: string | null; status?: string }[]): number {
  return shifts.filter(isOpenShift).length;
}

export function toOpenAssignment(s: ShiftAssignment): ShiftAssignment {
  return { ...s, employee_id: null, employee_name: null, status: "OPEN", preference_violations: [] };
}

export interface CandidateGroup {
  tier: Tier;
  candidates: Candidate[];
}

/** Candidates grouped by tier (server order kept within a tier), filtered
 *  by name. Empty tiers are dropped. */
export function groupCandidates(candidates: Candidate[], query: string): CandidateGroup[] {
  const q = query.trim().toLowerCase();
  return ([1, 2, 3] as const)
    .map((tier) => ({
      tier,
      candidates: candidates.filter(
        (c) => c.tier === tier && (!q || c.full_name.toLowerCase().includes(q)),
      ),
    }))
    .filter((g) => g.candidates.length > 0);
}

/** Every offered candidate is at the location, so only these can be missing. */
export type MissingFlag = "availability" | "role";

export function missingFlags(c: Candidate): MissingFlag[] {
  const out: MissingFlag[] = [];
  if (!c.available) out.push("availability");
  if (!c.has_role) out.push("role");
  return out;
}
```

- [ ] **Step 4: Update the shared types**

`frontend/src/types/index.ts`, in `ShiftAssignment`:

```ts
  /** null = an open shift (#174). */
  employee_id: string | null;
  employee_name: string | null;
```

In `LocationResult`, add after `signal_config?`:

```ts
  /** Open shifts in this location's result (#174). */
  open_count?: number;
```

`frontend/src/api/approvedSchedules.ts`:
- In `WeekShift`, change to `employee_id: string | null;` and `employee_name: string | null;`.
- In `EditWarning`, change to `employee_id: string | null;`.
- In `ApprovedShiftEdit`, add after `deleted?: boolean;`:

```ts
  /** true unassigns `shift_id`, or with no `shift_id` creates an open shift
   *  (#174). Never sent together with `employee_id`. */
  open?: boolean;
```

`frontend/src/api/exportSchedules.ts`:
- In `Export7ShiftsResult`, add `open_shifts_skipped: number;`.
- In the approved-shift interface that declares `employee_name: string;` (line 6), change that field to `employee_name: string | null;` and its `employee_id` to `string | null`.

- [ ] **Step 5: Write `frontend/src/api/openShifts.ts`**

```ts
import { apiFetch } from "./client";
import type { Candidate } from "../utils/openShifts";

/** The slot to rank people for. Times are local "HH:MM" at the location. */
export interface CandidateSlot {
  location_id: string;
  role_id: string;
  date: string;
  start_time: string;
  end_time: string;
  exclude_shift_id?: string | null;
}

export function fetchCandidates(slot: CandidateSlot): Promise<Candidate[]> {
  return apiFetch<Candidate[]>("/schedules/candidates", {
    method: "POST",
    body: JSON.stringify(slot),
  });
}

export async function getOpenShiftCount(): Promise<number> {
  const res = await apiFetch<{ count: number }>("/schedules/open-shifts/count");
  return res.count;
}

/** Fired after anything that can change the number of open approved shifts
 *  (an approved-shift edit, an approval), so the Sidebar badge refetches. */
export const OPEN_SHIFTS_CHANGED = "wiz:open-shifts-changed";

export function notifyOpenShiftsChanged(): void {
  window.dispatchEvent(new Event(OPEN_SHIFTS_CHANGED));
}
```

- [ ] **Step 6: Add the strings to all 19 locales**

In every `frontend/src/i18n/<lang>.ts`:
- Insert the `schedule` keys directly after that file's `rosterThinAtGeneration:` line.
- Insert `openShiftsSkipped` directly after that file's `shiftsTo7Shifts:` line in `exportSchedules`.

`{n}` is replaced at render time.

**en**
```ts
    openShift: "Open shift",
    openShiftsBanner: "{n} open shifts this week — click to show them",
    openShiftsBannerOne: "1 open shift this week — click to show it",
    addOpenShift: "+ Add open shift",
    addOpenShiftTitle: "Add open shift",
    leaveOpen: "Leave open (no one assigned)",
    tierBestFit: "Best fit",
    tierRoleNotAvailable: "Has the role, not available",
    tierAvailableNoRole: "Available, no role",
    missingAvailability: "not available",
    missingRole: "no role",
    candidatesError: "Couldn't rank employees — showing everyone.",
```
```ts
    openShiftsSkipped: "{n} open shift(s) skipped — 7shifts needs an employee on each shift.",
```

**zh**
```ts
    openShift: "空缺班次",
    openShiftsBanner: "本周有 {n} 个空缺班次 — 点击显示",
    openShiftsBannerOne: "本周有 1 个空缺班次 — 点击显示",
    addOpenShift: "+ 添加空缺班次",
    addOpenShiftTitle: "添加空缺班次",
    leaveOpen: "保持空缺（不指派）",
    tierBestFit: "最合适",
    tierRoleNotAvailable: "有该岗位，但没空",
    tierAvailableNoRole: "有空，但无该岗位",
    missingAvailability: "没空",
    missingRole: "无该岗位",
    candidatesError: "无法对员工排序 — 显示全部员工。",
```
```ts
    openShiftsSkipped: "已跳过 {n} 个空缺班次 — 7shifts 要求每个班次都有员工。",
```

**hi**
```ts
    openShift: "खुली शिफ्ट",
    openShiftsBanner: "इस सप्ताह {n} खुली शिफ्टें — दिखाने के लिए क्लिक करें",
    openShiftsBannerOne: "इस सप्ताह 1 खुली शिफ्ट — दिखाने के लिए क्लिक करें",
    addOpenShift: "+ खुली शिफ्ट जोड़ें",
    addOpenShiftTitle: "खुली शिफ्ट जोड़ें",
    leaveOpen: "खुली छोड़ें (किसी को नहीं सौंपा)",
    tierBestFit: "सबसे उपयुक्त",
    tierRoleNotAvailable: "भूमिका है, उपलब्ध नहीं",
    tierAvailableNoRole: "उपलब्ध, भूमिका नहीं",
    missingAvailability: "उपलब्ध नहीं",
    missingRole: "भूमिका नहीं",
    candidatesError: "कर्मचारियों को क्रमबद्ध नहीं कर सके — सभी दिखाए जा रहे हैं।",
```
```ts
    openShiftsSkipped: "{n} खुली शिफ्टें छोड़ी गईं — 7shifts को हर शिफ्ट पर एक कर्मचारी चाहिए।",
```

**ar**
```ts
    openShift: "وردية شاغرة",
    openShiftsBanner: "{n} ورديات شاغرة هذا الأسبوع — انقر لعرضها",
    openShiftsBannerOne: "وردية شاغرة واحدة هذا الأسبوع — انقر لعرضها",
    addOpenShift: "+ إضافة وردية شاغرة",
    addOpenShiftTitle: "إضافة وردية شاغرة",
    leaveOpen: "اتركها شاغرة (بدون تعيين)",
    tierBestFit: "الأنسب",
    tierRoleNotAvailable: "لديه الدور، غير متاح",
    tierAvailableNoRole: "متاح، بدون الدور",
    missingAvailability: "غير متاح",
    missingRole: "بدون الدور",
    candidatesError: "تعذّر ترتيب الموظفين — يتم عرض الجميع.",
```
```ts
    openShiftsSkipped: "تم تخطي {n} من الورديات الشاغرة — يتطلب 7shifts موظفًا لكل وردية.",
```

**fr**
```ts
    openShift: "Poste à pourvoir",
    openShiftsBanner: "{n} postes à pourvoir cette semaine — cliquez pour les afficher",
    openShiftsBannerOne: "1 poste à pourvoir cette semaine — cliquez pour l'afficher",
    addOpenShift: "+ Ajouter un poste à pourvoir",
    addOpenShiftTitle: "Ajouter un poste à pourvoir",
    leaveOpen: "Laisser à pourvoir (personne)",
    tierBestFit: "Meilleur choix",
    tierRoleNotAvailable: "A le rôle, indisponible",
    tierAvailableNoRole: "Disponible, sans le rôle",
    missingAvailability: "indisponible",
    missingRole: "sans le rôle",
    candidatesError: "Impossible de classer les employés — affichage de tous.",
```
```ts
    openShiftsSkipped: "{n} poste(s) à pourvoir ignoré(s) — 7shifts exige un employé par poste.",
```

**es**
```ts
    openShift: "Turno abierto",
    openShiftsBanner: "{n} turnos abiertos esta semana — haz clic para verlos",
    openShiftsBannerOne: "1 turno abierto esta semana — haz clic para verlo",
    addOpenShift: "+ Añadir turno abierto",
    addOpenShiftTitle: "Añadir turno abierto",
    leaveOpen: "Dejar abierto (sin asignar)",
    tierBestFit: "Mejor opción",
    tierRoleNotAvailable: "Tiene el puesto, no disponible",
    tierAvailableNoRole: "Disponible, sin el puesto",
    missingAvailability: "no disponible",
    missingRole: "sin el puesto",
    candidatesError: "No se pudo ordenar a los empleados — se muestran todos.",
```
```ts
    openShiftsSkipped: "{n} turno(s) abierto(s) omitido(s) — 7shifts necesita un empleado en cada turno.",
```

**pt**
```ts
    openShift: "Turno em aberto",
    openShiftsBanner: "{n} turnos em aberto nesta semana — clique para vê-los",
    openShiftsBannerOne: "1 turno em aberto nesta semana — clique para vê-lo",
    addOpenShift: "+ Adicionar turno em aberto",
    addOpenShiftTitle: "Adicionar turno em aberto",
    leaveOpen: "Deixar em aberto (sem ninguém)",
    tierBestFit: "Melhor opção",
    tierRoleNotAvailable: "Tem a função, indisponível",
    tierAvailableNoRole: "Disponível, sem a função",
    missingAvailability: "indisponível",
    missingRole: "sem a função",
    candidatesError: "Não foi possível ordenar os funcionários — mostrando todos.",
```
```ts
    openShiftsSkipped: "{n} turno(s) em aberto ignorado(s) — o 7shifts exige um funcionário em cada turno.",
```

**bn**
```ts
    openShift: "খালি শিফট",
    openShiftsBanner: "এই সপ্তাহে {n}টি খালি শিফট — দেখতে ক্লিক করুন",
    openShiftsBannerOne: "এই সপ্তাহে ১টি খালি শিফট — দেখতে ক্লিক করুন",
    addOpenShift: "+ খালি শিফট যোগ করুন",
    addOpenShiftTitle: "খালি শিফট যোগ করুন",
    leaveOpen: "খালি রাখুন (কাউকে দেওয়া হয়নি)",
    tierBestFit: "সবচেয়ে উপযুক্ত",
    tierRoleNotAvailable: "ভূমিকা আছে, সময় নেই",
    tierAvailableNoRole: "সময় আছে, ভূমিকা নেই",
    missingAvailability: "সময় নেই",
    missingRole: "ভূমিকা নেই",
    candidatesError: "কর্মীদের ক্রম সাজানো যায়নি — সবাইকে দেখানো হচ্ছে।",
```
```ts
    openShiftsSkipped: "{n}টি খালি শিফট বাদ দেওয়া হয়েছে — 7shifts-এ প্রতিটি শিফটে একজন কর্মী লাগে।",
```

**ru**
```ts
    openShift: "Свободная смена",
    openShiftsBanner: "Свободных смен на этой неделе: {n} — нажмите, чтобы показать",
    openShiftsBannerOne: "1 свободная смена на этой неделе — нажмите, чтобы показать",
    addOpenShift: "+ Добавить свободную смену",
    addOpenShiftTitle: "Добавить свободную смену",
    leaveOpen: "Оставить свободной (без сотрудника)",
    tierBestFit: "Лучше всего подходят",
    tierRoleNotAvailable: "Есть роль, но недоступен",
    tierAvailableNoRole: "Доступен, но нет роли",
    missingAvailability: "недоступен",
    missingRole: "нет роли",
    candidatesError: "Не удалось упорядочить сотрудников — показаны все.",
```
```ts
    openShiftsSkipped: "Пропущено свободных смен: {n} — 7shifts требует сотрудника для каждой смены.",
```

**ur**
```ts
    openShift: "خالی شفٹ",
    openShiftsBanner: "اس ہفتے {n} خالی شفٹیں — دیکھنے کے لیے کلک کریں",
    openShiftsBannerOne: "اس ہفتے 1 خالی شفٹ — دیکھنے کے لیے کلک کریں",
    addOpenShift: "+ خالی شفٹ شامل کریں",
    addOpenShiftTitle: "خالی شفٹ شامل کریں",
    leaveOpen: "خالی چھوڑیں (کسی کو تفویض نہیں)",
    tierBestFit: "سب سے موزوں",
    tierRoleNotAvailable: "کردار ہے، دستیاب نہیں",
    tierAvailableNoRole: "دستیاب، کردار نہیں",
    missingAvailability: "دستیاب نہیں",
    missingRole: "کردار نہیں",
    candidatesError: "ملازمین کی درجہ بندی نہیں ہو سکی — سب دکھائے جا رہے ہیں۔",
```
```ts
    openShiftsSkipped: "{n} خالی شفٹیں چھوڑ دی گئیں — 7shifts کو ہر شفٹ پر ایک ملازم درکار ہے۔",
```

**id**
```ts
    openShift: "Shift kosong",
    openShiftsBanner: "{n} shift kosong minggu ini — klik untuk menampilkan",
    openShiftsBannerOne: "1 shift kosong minggu ini — klik untuk menampilkan",
    addOpenShift: "+ Tambah shift kosong",
    addOpenShiftTitle: "Tambah shift kosong",
    leaveOpen: "Biarkan kosong (tanpa karyawan)",
    tierBestFit: "Paling cocok",
    tierRoleNotAvailable: "Punya peran, tidak tersedia",
    tierAvailableNoRole: "Tersedia, tanpa peran",
    missingAvailability: "tidak tersedia",
    missingRole: "tanpa peran",
    candidatesError: "Tidak dapat mengurutkan karyawan — menampilkan semua.",
```
```ts
    openShiftsSkipped: "{n} shift kosong dilewati — 7shifts memerlukan karyawan di setiap shift.",
```

**de**
```ts
    openShift: "Offene Schicht",
    openShiftsBanner: "{n} offene Schichten diese Woche — zum Anzeigen klicken",
    openShiftsBannerOne: "1 offene Schicht diese Woche — zum Anzeigen klicken",
    addOpenShift: "+ Offene Schicht hinzufügen",
    addOpenShiftTitle: "Offene Schicht hinzufügen",
    leaveOpen: "Offen lassen (niemand zugewiesen)",
    tierBestFit: "Passt am besten",
    tierRoleNotAvailable: "Hat die Rolle, nicht verfügbar",
    tierAvailableNoRole: "Verfügbar, ohne Rolle",
    missingAvailability: "nicht verfügbar",
    missingRole: "ohne Rolle",
    candidatesError: "Mitarbeitende konnten nicht sortiert werden — alle werden angezeigt.",
```
```ts
    openShiftsSkipped: "{n} offene Schicht(en) übersprungen — 7shifts braucht für jede Schicht eine Person.",
```

**pcm**
```ts
    openShift: "Shift wey no get person",
    openShiftsBanner: "{n} shift wey no get person dis week — click make you see dem",
    openShiftsBannerOne: "1 shift wey no get person dis week — click make you see am",
    addOpenShift: "+ Add shift wey no get person",
    addOpenShiftTitle: "Add shift wey no get person",
    leaveOpen: "Leave am open (nobody)",
    tierBestFit: "Fit pass",
    tierRoleNotAvailable: "Get di role, no dey available",
    tierAvailableNoRole: "Dey available, no get di role",
    missingAvailability: "no dey available",
    missingRole: "no get di role",
    candidatesError: "We no fit arrange di workers — we dey show everybody.",
```
```ts
    openShiftsSkipped: "We skip {n} shift wey no get person — 7shifts need worker for every shift.",
```

**te**
```ts
    openShift: "ఖాళీ షిఫ్ట్",
    openShiftsBanner: "ఈ వారం {n} ఖాళీ షిఫ్ట్‌లు — చూపించడానికి క్లిక్ చేయండి",
    openShiftsBannerOne: "ఈ వారం 1 ఖాళీ షిఫ్ట్ — చూపించడానికి క్లిక్ చేయండి",
    addOpenShift: "+ ఖాళీ షిఫ్ట్ జోడించండి",
    addOpenShiftTitle: "ఖాళీ షిఫ్ట్ జోడించండి",
    leaveOpen: "ఖాళీగా ఉంచండి (ఎవరికీ కేటాయించలేదు)",
    tierBestFit: "అత్యంత సరిపోయేవారు",
    tierRoleNotAvailable: "పాత్ర ఉంది, అందుబాటులో లేరు",
    tierAvailableNoRole: "అందుబాటులో ఉన్నారు, పాత్ర లేదు",
    missingAvailability: "అందుబాటులో లేరు",
    missingRole: "పాత్ర లేదు",
    candidatesError: "ఉద్యోగులను క్రమబద్ధీకరించలేకపోయాం — అందరినీ చూపిస్తున్నాం.",
```
```ts
    openShiftsSkipped: "{n} ఖాళీ షిఫ్ట్‌లు దాటవేయబడ్డాయి — 7shiftsకి ప్రతి షిఫ్ట్‌కు ఒక ఉద్యోగి అవసరం.",
```

**tr**
```ts
    openShift: "Açık vardiya",
    openShiftsBanner: "Bu hafta {n} açık vardiya — göstermek için tıklayın",
    openShiftsBannerOne: "Bu hafta 1 açık vardiya — göstermek için tıklayın",
    addOpenShift: "+ Açık vardiya ekle",
    addOpenShiftTitle: "Açık vardiya ekle",
    leaveOpen: "Açık bırak (kimse atanmadı)",
    tierBestFit: "En uygun",
    tierRoleNotAvailable: "Rolü var, müsait değil",
    tierAvailableNoRole: "Müsait, rolü yok",
    missingAvailability: "müsait değil",
    missingRole: "rolü yok",
    candidatesError: "Çalışanlar sıralanamadı — herkes gösteriliyor.",
```
```ts
    openShiftsSkipped: "{n} açık vardiya atlandı — 7shifts her vardiyada bir çalışan gerektirir.",
```

**ta**
```ts
    openShift: "காலியான ஷிஃப்ட்",
    openShiftsBanner: "இந்த வாரம் {n} காலியான ஷிஃப்ட்கள் — காட்ட கிளிக் செய்யவும்",
    openShiftsBannerOne: "இந்த வாரம் 1 காலியான ஷிஃப்ட் — காட்ட கிளிக் செய்யவும்",
    addOpenShift: "+ காலியான ஷிஃப்ட் சேர்",
    addOpenShiftTitle: "காலியான ஷிஃப்ட் சேர்",
    leaveOpen: "காலியாக விடு (யாருக்கும் ஒதுக்கவில்லை)",
    tierBestFit: "மிகப் பொருத்தமானவர்கள்",
    tierRoleNotAvailable: "பணிப்பங்கு உள்ளது, கிடைக்கவில்லை",
    tierAvailableNoRole: "கிடைக்கிறார், பணிப்பங்கு இல்லை",
    missingAvailability: "கிடைக்கவில்லை",
    missingRole: "பணிப்பங்கு இல்லை",
    candidatesError: "ஊழியர்களை வரிசைப்படுத்த முடியவில்லை — அனைவரும் காட்டப்படுகிறார்கள்.",
```
```ts
    openShiftsSkipped: "{n} காலியான ஷிஃப்ட்கள் தவிர்க்கப்பட்டன — 7shifts க்கு ஒவ்வொரு ஷிஃப்டிலும் ஒரு ஊழியர் தேவை.",
```

**vi**
```ts
    openShift: "Ca trống",
    openShiftsBanner: "{n} ca trống trong tuần này — nhấn để hiển thị",
    openShiftsBannerOne: "1 ca trống trong tuần này — nhấn để hiển thị",
    addOpenShift: "+ Thêm ca trống",
    addOpenShiftTitle: "Thêm ca trống",
    leaveOpen: "Để trống (chưa phân công)",
    tierBestFit: "Phù hợp nhất",
    tierRoleNotAvailable: "Có vai trò, không rảnh",
    tierAvailableNoRole: "Rảnh, không có vai trò",
    missingAvailability: "không rảnh",
    missingRole: "không có vai trò",
    candidatesError: "Không thể xếp hạng nhân viên — đang hiển thị tất cả.",
```
```ts
    openShiftsSkipped: "Đã bỏ qua {n} ca trống — 7shifts yêu cầu mỗi ca có một nhân viên.",
```

**ja**
```ts
    openShift: "空きシフト",
    openShiftsBanner: "今週の空きシフト：{n}件 — クリックで表示",
    openShiftsBannerOne: "今週の空きシフト：1件 — クリックで表示",
    addOpenShift: "+ 空きシフトを追加",
    addOpenShiftTitle: "空きシフトを追加",
    leaveOpen: "空きのままにする（未割り当て）",
    tierBestFit: "最適",
    tierRoleNotAvailable: "役割あり・都合つかず",
    tierAvailableNoRole: "都合つく・役割なし",
    missingAvailability: "都合つかず",
    missingRole: "役割なし",
    candidatesError: "従業員を並べ替えられませんでした — 全員を表示しています。",
```
```ts
    openShiftsSkipped: "空きシフト {n}件をスキップしました — 7shifts では各シフトに従業員が必要です。",
```

**mr**
```ts
    openShift: "रिक्त शिफ्ट",
    openShiftsBanner: "या आठवड्यात {n} रिक्त शिफ्ट — दाखवण्यासाठी क्लिक करा",
    openShiftsBannerOne: "या आठवड्यात 1 रिक्त शिफ्ट — दाखवण्यासाठी क्लिक करा",
    addOpenShift: "+ रिक्त शिफ्ट जोडा",
    addOpenShiftTitle: "रिक्त शिफ्ट जोडा",
    leaveOpen: "रिक्त ठेवा (कोणालाही दिलेली नाही)",
    tierBestFit: "सर्वात योग्य",
    tierRoleNotAvailable: "भूमिका आहे, उपलब्ध नाही",
    tierAvailableNoRole: "उपलब्ध, भूमिका नाही",
    missingAvailability: "उपलब्ध नाही",
    missingRole: "भूमिका नाही",
    candidatesError: "कर्मचाऱ्यांची क्रमवारी लावता आली नाही — सर्वजण दाखवले जात आहेत.",
```
```ts
    openShiftsSkipped: "{n} रिक्त शिफ्ट वगळल्या — 7shifts ला प्रत्येक शिफ्टवर एक कर्मचारी लागतो.",
```

- [ ] **Step 7: Run the helper tests and type-check**

Run: `cd frontend && npx vitest run src/utils/openShifts.test.ts`
Expected: all pass.

Run: `cd frontend && npx tsc --noEmit`
Expected: errors only where shift `employee_id` is now `string | null`: `components/shared/EditShiftModal.tsx:38`, `pages/manager/Schedule.tsx:55`, and possibly `ScheduleGrid.tsx:149`/`ExportSchedules.tsx:510`. Tasks 10–12 rewrite those exact spots. Do not patch them here; note the list.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/utils/openShifts.ts frontend/src/utils/openShifts.test.ts frontend/src/api/openShifts.ts frontend/src/types/index.ts frontend/src/api/approvedSchedules.ts frontend/src/api/exportSchedules.ts frontend/src/i18n/
git commit -m "feat(frontend): open-shift types, helpers, API and strings (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 10: Shared components: open card, banner, picker, add form

**Files:**
- Modify: `frontend/src/components/shared/ScheduleGrid.tsx`
- Create: `frontend/src/components/shared/OpenShiftBanner.tsx`
- Create: `frontend/src/components/shared/CandidatePicker.tsx`
- Create: `frontend/src/components/shared/AddOpenShiftModal.tsx`

**Interfaces:**
- Consumes: `isOpenShift`, `groupCandidates`, `missingFlags`, `Candidate` (Task 9); `fetchCandidates`, `CandidateSlot` (Task 9).
- Produces:
  - `ScheduleGrid` gains the optional prop `highlightOpen?: boolean`. Open cards render with `data-open-shift="true"`.
  - `<OpenShiftBanner count={number} onShow={() => void} />`, which renders nothing at 0.
  - `<CandidatePicker employees={Employee[]} slot={CandidateSlot | null} value={string | null} onChange={(id: string | null) => void} />`. `value === null` means "Leave open". With `slot === null`, it shows the plain list.
  - `<AddOpenShiftModal roles={{ id: string; name: string }[]} dates={string[]} onSubmit={(v: { role_id: string; role_name: string; date: string; start: string; end: string }) => Promise<void> | void} onClose={() => void} />`.

There is no DOM test environment in this repo (vitest runs pure tests only). Components are verified by `tsc`, `npm run build` and the manual check in Task 12, Step 7. The behaviour that matters is in the tested helpers.

- [ ] **Step 1: The open card in `ScheduleGrid.tsx`**

Add the import:

```ts
import { isOpenShift } from "../../utils/openShifts";
```

Add `highlightOpen?: boolean;` to `ScheduleGridProps`, and `highlightOpen = false,` to the destructured props.

Replace the per-shift card (the `<div key={i} onClick=...>` through its closing `</div>`) with:

```tsx
                        {cellShifts.map((s, i) => {
                          const open = isOpenShift(s);
                          return (
                            <div
                              key={i}
                              data-open-shift={open ? "true" : undefined}
                              onClick={
                                editable && onEditShift
                                  ? () => onEditShift(cellIndices[i])
                                  : undefined
                              }
                              className={`rounded-lg border px-2.5 py-1.5 ${
                                open
                                  ? "border-dashed border-amber-500/70 bg-amber-500/10 text-amber-900"
                                  : roleColorMap[role]
                              } ${open && highlightOpen ? "ring-2 ring-amber-500" : ""} ${
                                editable
                                  ? "cursor-pointer hover:ring-2 hover:ring-accent/40 transition-shadow"
                                  : ""
                              }`}
                            >
                              <div className="text-sm font-medium text-inherit">
                                {open ? t.schedule.openShift : s.employee_name}
                                {!open && (s.preference_violations?.length ?? 0) > 0 && (
                                  <PreferenceMark
                                    label={t.schedule.prefAsteriskLabel}
                                    lines={describeViolations(s.preference_violations, t.schedule)}
                                  />
                                )}
                              </div>
                              <div className="text-xs opacity-75">
                                {formatTime(s.start_time)} &ndash;{" "}
                                {formatTime(s.end_time)}
                              </div>
                            </div>
                          );
                        })}
```

- [ ] **Step 2: `OpenShiftBanner.tsx`**

```tsx
import { useLanguage } from "../../i18n/LanguageContext";

interface Props {
  count: number;
  onShow: () => void;
}

/** "N open shifts this week" (#174). Clicking highlights them in the grid. */
export default function OpenShiftBanner({ count, onShow }: Props) {
  const { t } = useLanguage();
  if (count <= 0) return null;
  const label =
    count === 1
      ? t.schedule.openShiftsBannerOne
      : t.schedule.openShiftsBanner.replace("{n}", String(count));
  return (
    <button
      type="button"
      onClick={onShow}
      className="glass-alert-warning w-full text-start mb-3 text-sm font-medium"
    >
      {label}
    </button>
  );
}
```

- [ ] **Step 3: `CandidatePicker.tsx`**

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import type { Employee } from "../../types";
import { fetchCandidates, type CandidateSlot } from "../../api/openShifts";
import { groupCandidates, missingFlags, type Candidate, type MissingFlag, type Tier } from "../../utils/openShifts";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

interface Props {
  employees: Employee[];
  /** The slot to rank for; null shows the plain list. */
  slot: CandidateSlot | null;
  /** null = "Leave open". */
  value: string | null;
  onChange: (id: string | null) => void;
}

/** The employee picker for a shift (#174): "Leave open" first, then people
 *  grouped by how well they fit, as ranked by the server. Falls back to the
 *  plain list if ranking fails. */
export default function CandidatePicker({ employees, slot, value, onChange }: Props) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [failed, setFailed] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const slotKey = slot ? JSON.stringify(slot) : "";
  useEffect(() => {
    if (!slot) {
      setCandidates(null);
      return;
    }
    let cancelled = false;
    setFailed(false);
    fetchCandidates(slot)
      .then((c) => !cancelled && setCandidates(c))
      .catch(() => {
        if (!cancelled) {
          setCandidates(null);
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // slotKey captures every field of slot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotKey]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const selectedLabel =
    value === null
      ? t.schedule.leaveOpen
      : employees.find((e) => e.id === value)?.full_name ??
        candidates?.find((c) => c.employee_id === value)?.full_name ??
        "";

  const tierLabel: Record<Tier, string> = {
    1: t.schedule.tierBestFit,
    2: t.schedule.tierRoleNotAvailable,
    3: t.schedule.tierAvailableNoRole,
  };
  const flagLabel: Record<MissingFlag, string> = {
    availability: t.schedule.missingAvailability,
    role: t.schedule.missingRole,
  };

  const groups = useMemo(() => {
    if (candidates) return groupCandidates(candidates, query);
    // Fallback when ranking failed: everyone, one list with no heading. There
    // is nothing to rank by, so nobody is hidden.
    const q = query.trim().toLowerCase();
    const all: Candidate[] = employees
      .filter((e) => !q || e.full_name.toLowerCase().includes(q))
      .map((e) => ({
        employee_id: e.id, full_name: e.full_name, tier: 3,
        available: true, has_role: true, at_location: true,
      }));
    return all.length ? [{ tier: 3 as Tier, candidates: all }] : [];
  }, [candidates, employees, query]);

  const pick = (id: string | null) => {
    onChange(id);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={open ? query : selectedLabel}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        placeholder={t.schedule.searchEmployee}
        className="w-full glass-input-sm"
        autoComplete="off"
      />
      {failed && <p className={`mt-1 text-xs ${text.muted}`}>{t.schedule.candidatesError}</p>}
      {open && (
        <ul className="absolute z-20 mt-1 w-full max-h-64 overflow-auto bg-cream/95 backdrop-blur-xl border border-sage/25 rounded-lg shadow-2xl text-sm">
          <li
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => pick(null)}
            className={`px-3 py-2 cursor-pointer border-b border-sage/20 hover:bg-white/[0.08] ${
              value === null ? "bg-accent/10 text-accent-dark font-medium" : "text-amber-900"
            }`}
          >
            {t.schedule.leaveOpen}
          </li>
          {groups.map((g) => (
            <li key={g.tier}>
              {candidates && (
                <div className={`px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wide ${text.muted}`}>
                  {tierLabel[g.tier]}
                </div>
              )}
              <ul>
                {g.candidates.map((c) => (
                  <li
                    key={c.employee_id}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(c.employee_id)}
                    className={`px-3 py-1.5 cursor-pointer hover:bg-white/[0.08] ${
                      c.employee_id === value ? "bg-accent/10 text-accent-dark font-medium" : text.body
                    }`}
                  >
                    <span>{c.full_name}</span>
                    {candidates &&
                      missingFlags(c).map((f) => (
                        <span
                          key={f}
                          className="ms-1.5 inline-block px-1.5 py-0.5 rounded text-[10px] bg-sage/15 text-sage-dark"
                        >
                          {flagLabel[f]}
                        </span>
                      ))}
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {groups.length === 0 && (
            <li className={`px-3 py-2 ${text.muted}`}>{t.search.noMatches}</li>
          )}
        </ul>
      )}
    </div>
  );
}
```

If `text-sage-dark` or `bg-cream/95` are not Tailwind classes in this project, copy the equivalent classes from `EmployeeSearchBox.tsx`'s list. `bg-cream/95` is taken from there verbatim.

- [ ] **Step 4: `AddOpenShiftModal.tsx`**

```tsx
import { useState } from "react";
import { useLanguage } from "../../i18n/LanguageContext";
import { getDayLabel } from "./ScheduleGrid";
import { text, border } from "../../theme";

interface Props {
  roles: { id: string; name: string }[];
  /** The week's dates ("YYYY-MM-DD") to choose from. */
  dates: string[];
  onSubmit: (v: { role_id: string; role_name: string; date: string; start: string; end: string }) =>
    | Promise<void>
    | void;
  onClose: () => void;
}

/** Add a shift with no one on it yet (#174). */
export default function AddOpenShiftModal({ roles, dates, onSubmit, onClose }: Props) {
  const { t } = useLanguage();
  const [roleId, setRoleId] = useState(roles[0]?.id ?? "");
  const [date, setDate] = useState(dates[0] ?? "");
  const [start, setStart] = useState("09:00");
  const [end, setEnd] = useState("17:00");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const role = roles.find((r) => r.id === roleId);
    if (!role || !date) return;
    setSaving(true);
    setError("");
    try {
      await onSubmit({ role_id: role.id, role_name: role.name, date, start, end });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-md mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h3 className={`text-lg font-semibold ${text.body}`}>{t.schedule.addOpenShiftTitle}</h3>
          <button type="button" onClick={onClose} className={`${text.muted} text-xl leading-none`}>
            &times;
          </button>
        </div>
        <div className="px-6 py-4 space-y-4">
          {error && <div className="glass-alert-error">{error}</div>}
          <div>
            <label className="glass-label">{t.common.role}</label>
            <select value={roleId} onChange={(e) => setRoleId(e.target.value)} className="glass-input w-full">
              {roles.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="glass-label">{t.common.date}</label>
            <select value={date} onChange={(e) => setDate(e.target.value)} className="glass-input w-full">
              {dates.map((d) => (
                <option key={d} value={d}>{getDayLabel(d)} ({d})</option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="glass-label">{t.common.startTime}</label>
              <input type="time" value={start} onChange={(e) => setStart(e.target.value)} className="glass-input w-full" />
            </div>
            <div>
              <label className="glass-label">{t.common.endTime}</label>
              <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} className="glass-input w-full" />
            </div>
          </div>
        </div>
        <div className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}>
          <button type="button" onClick={onClose} className="glass-btn-secondary text-sm font-medium">
            {t.common.cancel}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={saving || !roleId || !date}
            className="glass-btn-primary text-sm font-medium disabled:opacity-50"
          >
            {saving ? t.common.saving : t.common.save}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Type-check and run the direction test**

Run: `cd frontend && npx tsc --noEmit`
Expected: the only remaining errors are in `EditShiftModal.tsx`, `Schedule.tsx` and `ExportSchedules.tsx` (fixed in Tasks 11–12).

Run: `cd frontend && npx vitest run src/utils/logicalDirection.test.ts`
Expected: PASS (no `ml-`/`text-left`/`left-0` in the new files).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/shared/ScheduleGrid.tsx frontend/src/components/shared/OpenShiftBanner.tsx frontend/src/components/shared/CandidatePicker.tsx frontend/src/components/shared/AddOpenShiftModal.tsx
git commit -m "feat(frontend): open-shift card, banner, ranked picker, add form (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 11: Draft review wiring (`Schedule.tsx`)

**Files:**
- Modify: `frontend/src/pages/manager/Schedule.tsx` (`EditShiftModal` at lines 35-175, `allRoles` ~line 772, the per-location block ~lines 1545-1560, the modal render ~line 1348)

**Interfaces:**
- Consumes: `CandidatePicker`, `OpenShiftBanner`, `AddOpenShiftModal` (Task 10); `isOpenShift`, `toOpenAssignment`, `countOpen` (Task 9); `extractTime`, `extractOffset` (existing).

- [ ] **Step 1: The draft modal uses the ranked picker**

In `Schedule.tsx`'s local `EditShiftModal`:
- Add `roleIdByName: Record<string, string>;` to `EditShiftModalProps` and destructure it.
- Replace the `EmployeeSearchBox` import usage inside this modal with `CandidatePicker`. Add `import CandidatePicker from "../../components/shared/CandidatePicker";` and `import { isOpenShift, toOpenAssignment } from "../../utils/openShifts";` at the top. Keep the `EmployeeSearchBox` import only if still used elsewhere in the file (`grep -n EmployeeSearchBox`).

Replace the state line and `handleSave`:

```tsx
  const [employeeId, setEmployeeId] = useState<string | null>(
    isOpenShift(shift) ? null : shift.employee_id
  );
```

```tsx
  const roleId = roleIdByName[roleName] ?? shift.role_id;
  const slot = {
    location_id: shift.location_id,
    role_id: roleId,
    date: shift.date,
    start_time: startTime,
    end_time: endTime,
  };

  const handleSave = () => {
    const datePrefix = shift.date;
    const base: ShiftAssignment = {
      ...shift,
      role_id: roleId,
      role_name: roleName,
      // Reattach the offset the shift arrived with: dropping it silently
      // re-anchors the shift to a different instant (issue #92).
      start_time: `${datePrefix}T${startTime}:00${extractOffset(shift.start_time)}`,
      end_time: `${datePrefix}T${endTime}:00${extractOffset(shift.end_time)}`,
    };
    if (employeeId === null) {
      onSave(toOpenAssignment(base));
      return;
    }
    onSave({
      ...base,
      employee_id: employeeId,
      employee_name: selectedEmployee?.full_name ?? shift.employee_name,
      status: isOpenShift(shift) ? "ok" : shift.status,
    });
  };
```

Replace the `<EmployeeSearchBox ... />` element in this modal with:

```tsx
            <CandidatePicker
              employees={employees}
              slot={slot}
              value={employeeId}
              onChange={setEmployeeId}
            />
```

Change the Save button's `disabled={!employeeId}` to `disabled={employeeId === ""}`.

- [ ] **Step 2: Role ids by name, for the modal and the add form**

Next to `allRoles` (the `useMemo` that collects role names), add:

```tsx
  // role name -> role id across all results, so the draft modal can rank
  // candidates by role id and save the matching id with a renamed role.
  const roleIdByName = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of results) {
      const shifts = editedShifts[r.location_id] ?? r.shifts;
      shifts.forEach((s) => {
        if (s.role_id) map[s.role_name] = s.role_id;
      });
    }
    return map;
  }, [results, editedShifts]);
```

Pass it to the modal render: `<EditShiftModal ... roleIdByName={roleIdByName} />`.

- [ ] **Step 3: Banner, highlight and add-open-shift per location**

Add the imports `OpenShiftBanner`, `AddOpenShiftModal` and `countOpen`. Add state near the other `useState`s:

```tsx
  const [highlightOpenFor, setHighlightOpenFor] = useState<string | null>(null);
  const [addingOpenFor, setAddingOpenFor] = useState<string | null>(null);
```

Add the handler next to `handleDeleteShift`:

```tsx
  const handleAddOpenShift = async (
    locationId: string,
    v: { role_id: string; role_name: string; date: string; start: string; end: string },
  ) => {
    const result = results.find((r) => r.location_id === locationId);
    const current = editedShifts[locationId] ?? result?.shifts ?? [];
    // Borrow the location's offset from any existing shift (#92).
    const offset = current.length ? extractOffset(current[0].start_time) : "";
    const added: ShiftAssignment = {
      employee_id: null,
      employee_name: null,
      role_id: v.role_id,
      role_name: v.role_name,
      location_id: locationId,
      date: v.date,
      start_time: `${v.date}T${v.start}:00${offset}`,
      end_time: `${v.date}T${v.end}:00${offset}`,
      status: "OPEN",
      preference_violations: [],
    };
    const next = [...current, added];
    setEditedShifts((prev) => ({ ...prev, [locationId]: next }));
    if (result?.schedule_id) {
      await persistDraft(locationId, result.schedule_id, next);
    }
  };
```

In the per-location block, replace:

```tsx
              <RosterThinBanner summary={locationResult.preference_summary} />

              {currentShifts.length > 0 && (
                <ScheduleGrid
                  shifts={currentShifts}
                  editable={!decided}
                  employees={employees}
```

with:

```tsx
              <RosterThinBanner summary={locationResult.preference_summary} />

              <div className="px-4 pt-3">
                <OpenShiftBanner
                  count={countOpen(currentShifts)}
                  onShow={() => {
                    setHighlightOpenFor(locationResult.location_id);
                    requestAnimationFrame(() =>
                      document
                        .querySelector(`[data-location="${locationResult.location_id}"] [data-open-shift="true"]`)
                        ?.scrollIntoView({ behavior: "smooth", block: "center" })
                    );
                  }}
                />
                {!decided && (
                  <button
                    type="button"
                    onClick={() => setAddingOpenFor(locationResult.location_id)}
                    className="glass-btn-secondary text-xs font-medium mb-3"
                  >
                    {t.schedule.addOpenShift}
                  </button>
                )}
              </div>

              {currentShifts.length > 0 && (
                <div data-location={locationResult.location_id}>
                <ScheduleGrid
                  shifts={currentShifts}
                  editable={!decided}
                  employees={employees}
                  highlightOpen={highlightOpenFor === locationResult.location_id}
```

Close the new `<div>` right after the `ScheduleGrid`'s closing `/>`:

```tsx
                />
                </div>
              )}
```

`currentShifts` comes from the banner's count, not `locationResult.open_count`, so it stays right after hand edits. `open_count` is the generation-time value and is not needed for display.

Render the add modal next to the edit modal render:

```tsx
      {addingOpenFor && (
        <AddOpenShiftModal
          roles={Object.entries(roleIdByName).map(([name, id]) => ({ id, name }))}
          dates={Array.from(
            new Set(
              (editedShifts[addingOpenFor] ??
                results.find((r) => r.location_id === addingOpenFor)?.shifts ??
                []).map((s) => s.date)
            )
          ).sort()}
          onSubmit={(v) => handleAddOpenShift(addingOpenFor, v)}
          onClose={() => setAddingOpenFor(null)}
        />
      )}
```

The draft offers the dates already in that location's draft. A location with no shifts at all has none to offer, and is not the case this feature serves.

- [ ] **Step 4: Type-check, test, build**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: no errors in `Schedule.tsx`. The remaining tsc errors are only in `EditShiftModal.tsx` and `ExportSchedules.tsx` (Task 12). All tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/manager/Schedule.tsx
git commit -m "feat(frontend): draft review shows, counts, adds and fills open shifts (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 12: Approved schedules, export page and the Sidebar badge

**Files:**
- Modify: `frontend/src/components/shared/EditShiftModal.tsx`
- Modify: `frontend/src/pages/manager/ApprovedSchedules.tsx`
- Modify: `frontend/src/pages/manager/Schedule.tsx` (approval → change event)
- Modify: `frontend/src/pages/manager/ExportSchedules.tsx:383-402,510`
- Modify: `frontend/src/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: `CandidatePicker`, `OpenShiftBanner`, `AddOpenShiftModal` (Task 10); `editApprovedShifts`, `ApprovedShiftEdit.open` (Task 9); `getOpenShiftCount`, `OPEN_SHIFTS_CHANGED`, `notifyOpenShiftsChanged` (Task 9).

- [ ] **Step 1: The approved editor uses the ranked picker**

In `EditShiftModal.tsx`:
- Add `import CandidatePicker from "./CandidatePicker";` and `import { notifyOpenShiftsChanged } from "../../api/openShifts";`.
- Remove the `EmployeeSearchBox` import.

Replace the employee state:

```tsx
  const [employeeId, setEmployeeId] = useState<string | null>(shift.employee_id);
```

In `submit`, after `onApplied();`, add `notifyOpenShiftsChanged();`.

In `handleSave`, replace the `submit({...}, false)` argument with:

```tsx
        {
          shift_id: shift.id,
          ...(employeeId === null ? { open: true } : { employee_id: employeeId }),
          role_id: roleId,
          start_time: `${shift.date}T${startTime}:00${extractOffset(shift.start_time)}`,
          end_time: `${shift.date}T${endTime}:00${extractOffset(shift.end_time)}`,
        },
```

Replace the `<EmployeeSearchBox ... />` with:

```tsx
                <CandidatePicker
                  employees={employees}
                  slot={{
                    location_id: shift.location_id,
                    role_id: roleId,
                    date: shift.date,
                    start_time: startTime,
                    end_time: endTime,
                    exclude_shift_id: shift.id,
                  }}
                  value={employeeId}
                  onChange={setEmployeeId}
                />
```

Change the Save button's `disabled={submitting || deleting || !employeeId || !roleId}` to `disabled={submitting || deleting || employeeId === "" || !roleId}`.

- [ ] **Step 2: Approved week: banner, highlight, add**

In `ApprovedSchedules.tsx`:
- Add the imports `OpenShiftBanner`, `AddOpenShiftModal`, `countOpen` (from `../../utils/openShifts`), `extractOffset` (from `../../utils/shiftTime`) and `notifyOpenShiftsChanged` (from `../../api/openShifts`).
- Add state:

```tsx
  const [highlightOpenFor, setHighlightOpenFor] = useState<string | null>(null);
  const [addingOpenFor, setAddingOpenFor] = useState<WeekSchedule | null>(null);
```

Inside `schedules.map((sched) => { ... })`, replace:

```tsx
                  <RosterThinBanner summary={sched.preference_summary} atGeneration />
                  <ScheduleGrid
                    shifts={approvedApi.toAssignments(sched.shifts)}
                    editable={editable}
                    employees={[]}
```

with:

```tsx
                  <RosterThinBanner summary={sched.preference_summary} atGeneration />
                  <OpenShiftBanner
                    count={countOpen(sched.shifts)}
                    onShow={() => {
                      setHighlightOpenFor(sched.id);
                      requestAnimationFrame(() =>
                        document
                          .querySelector(`[data-schedule="${sched.id}"] [data-open-shift="true"]`)
                          ?.scrollIntoView({ behavior: "smooth", block: "center" })
                      );
                    }}
                  />
                  {editable && (
                    <button
                      type="button"
                      onClick={() => setAddingOpenFor(sched)}
                      className="glass-btn-secondary text-xs font-medium mb-3"
                    >
                      {t.schedule.addOpenShift}
                    </button>
                  )}
                  <div data-schedule={sched.id}>
                  <ScheduleGrid
                    shifts={approvedApi.toAssignments(sched.shifts)}
                    editable={editable}
                    employees={[]}
                    highlightOpen={highlightOpenFor === sched.id}
```

Close the new `<div>` after the `ScheduleGrid`'s closing `/>` with `</div>`.

In `toAssignments` (`api/approvedSchedules.ts`), the mapped status is `"ok"`. That is correct: `isOpenShift` treats a null `employee_id` as open regardless of status.

Before the final `</div>` of the page, add:

```tsx
      {addingOpenFor && (
        <AddOpenShiftModal
          roles={roles.map((r) => ({ id: r.id, name: r.name }))}
          dates={Array.from({ length: 7 }, (_, i) => {
            const d = new Date(addingOpenFor.week_start_date + "T00:00:00");
            d.setDate(d.getDate() + i);
            return toDateStr(d);
          })}
          onSubmit={async (v) => {
            const offset = addingOpenFor.shifts.length
              ? extractOffset(addingOpenFor.shifts[0].start_time)
              : "";
            await approvedApi.editApprovedShifts(addingOpenFor.id, [{
              open: true,
              role_id: v.role_id,
              date: v.date,
              start_time: `${v.date}T${v.start}:00${offset}`,
              end_time: `${v.date}T${v.end}:00${offset}`,
            }]);
            notifyOpenShiftsChanged();
            await loadWeek(selectedWeek);
          }}
          onClose={() => setAddingOpenFor(null)}
        />
      )}
```

`toDateStr` already exists in this file (it is used for `weekDays`). If the schedule has no shifts, the times go without an offset and the server reads them in the location's zone (Task 4).

- [ ] **Step 3: Approval announces the change**

In `Schedule.tsx`, find where `schedulesApi.approveSchedule(` resolves successfully (`grep -n "approveSchedule(" frontend/src/pages/manager/Schedule.tsx`). Directly after the awaited call succeeds, add `notifyOpenShiftsChanged();` and import it from `../../api/openShifts`.

- [ ] **Step 4: The export page**

In `ExportSchedules.tsx`:
- Add `import { isOpenShift } from "../../utils/openShifts";`.
- At line ~510, replace `{shift.employee_name}` with `{isOpenShift(shift) ? t.schedule.openShift : shift.employee_name}`.
- In the export result block, after the `</p>` that shows the exported count, add:

```tsx
          {exportResult.open_shifts_skipped > 0 && (
            <p className="mt-1 text-xs">
              {t.exportSchedules.openShiftsSkipped.replace(
                "{n}",
                String(exportResult.open_shifts_skipped)
              )}
            </p>
          )}
```

- [ ] **Step 5: The Sidebar badge**

In `Sidebar.tsx`, add `import { getOpenShiftCount, OPEN_SHIFTS_CHANGED } from "../../api/openShifts";`.

Add state and an effect after the `hasEmployees` effect:

```tsx
  const [openShiftCount, setOpenShiftCount] = useState(0);

  // Open approved shifts in the next 14 days (#174). Refetched on every
  // navigation and whenever something announces a change.
  useEffect(() => {
    if (!isManager) return;
    let cancelled = false;
    const load = () =>
      getOpenShiftCount()
        .then((n) => !cancelled && setOpenShiftCount(n))
        .catch(() => !cancelled && setOpenShiftCount(0));
    load();
    window.addEventListener(OPEN_SHIFTS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(OPEN_SHIFTS_CHANGED, load);
    };
  }, [isManager, pathname]);

  const badge = (count: number) =>
    count > 0 ? (
      <span className="ms-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-amber-500 text-white text-[11px] font-semibold">
        {count}
      </span>
    ) : null;
```

In the group render, change the group button's `<span>{label(entry.labelKey)}</span>` to:

```tsx
                <span>
                  {label(entry.labelKey)}
                  {!isOpen &&
                    entry.children.some((c) => c.to === "/manager/approved-schedules") &&
                    badge(openShiftCount)}
                </span>
```

In the children map, change `{label(child.labelKey)}` to:

```tsx
                      {label(child.labelKey)}
                      {child.to === "/manager/approved-schedules" && badge(openShiftCount)}
```

- [ ] **Step 6: Type-check, test, build**

Run: `cd frontend && npm test && npm run build`
Expected: all tests pass (including `logicalDirection.test.ts`); `tsc` reports no errors; the Vite build succeeds.

- [ ] **Step 7: Manual check in the running app**

Run the backend (`uvicorn main:app --reload` from `backend/`, after `alembic upgrade head`) and the frontend (`npm run dev`). As a manager:
1. Generate a schedule whose template needs more people than are available. The draft shows amber "Open shift" cards and the banner. Clicking the banner rings them.
2. Click an open card. The picker shows "Leave open", then up to three tier headings with chips; nobody from outside the location appears. Pick a tier-1 person and save; the card fills.
3. "+ Add open shift" adds a card. Approve the schedule. The Sidebar badge shows the open count for shifts in the next 14 days.
4. On Approved Schedules, unassign a shift via "Leave open". The badge goes up by one without a reload.
5. Switch the language to Arabic: the cards, picker and badge sit correctly right-to-left.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/shared/EditShiftModal.tsx frontend/src/pages/manager/ApprovedSchedules.tsx frontend/src/pages/manager/Schedule.tsx frontend/src/pages/manager/ExportSchedules.tsx frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(frontend): open shifts on approved schedules, exports and a nav badge (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

---

### Task 13: Documentation

**Files:**
- Modify: `README.md:33,94,100`
- Modify: `CLAUDE.md` (Conventions)

- [ ] **Step 1: README**

Replace `README.md` line 33's last sentence, `The manager sees "Saturday, 2 servers required, 1 filled" instead of a schedule that looks complete and isn't.`, with:

```markdown
Those placeholders leave the graph as **open shifts** (`employee_id: null`): labelled cards in the draft that survive approval, counted in a per-location banner and a Sidebar badge, and filled from a picker that ranks people by availability, role and location. The manager sees exactly which slots are still empty instead of a schedule that looks complete and isn't.
```

In line 94, replace `a manager can fill three `VACANT` slots by hand in two minutes.` with `a manager can fill three open shifts by hand in two minutes, from a picker that puts the people who fit at the top.`

In line 100, replace `while under-scheduling is a visible `VACANT` slot a manager fixes in seconds.` with `while under-scheduling is a visible open shift a manager fixes in seconds.`

- [ ] **Step 2: CLAUDE.md**

Add to the Conventions list, after the "Preference asterisks report, never act" bullet:

```markdown
- **An open shift is a `Shift` with `employee_id IS NULL`** (draft JSON:
  `employee_id: null, status: "OPEN"`; legacy drafts may still say
  `"VACANT"` — read them through `scheduling/open_shifts.is_open_entry`).
  Inside the scheduling graph an unfilled slot stays the `VACANT`
  placeholder; `emit_result` is the only place it becomes open. Any new
  reader of shifts must decide explicitly whether it includes open shifts.
  "Available" has one definition, `services/availability_check.is_available`,
  shared by the candidate ranking and the `no_availability` edit warning;
  the frontend never evaluates availability itself.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: describe open shifts in the README and conventions (#174)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi"
```

- [ ] **Step 4: Full verification before the PR**

Run: `pytest tests/ -q` (repo root) and `cd frontend && npm test && npm run build`.
Expected: everything passes. Then follow the project's Git Workflow: push `feat/open-shifts`, open a PR to `main` that references #174, and wait for CI.

Follow-up, not part of this plan: check the marketing site's scheduling feature pages (separate repo) for vacancy claims once this ships.
