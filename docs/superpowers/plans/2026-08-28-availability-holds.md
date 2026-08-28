# Availability Holds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop approving a schedule from destroying employee availability. Derive consumption from the `Shift` rows instead, so it can be released simply by deleting the shift.

**Architecture:** A `Shift` row *is* the hold — no new table. `approve` stops calling `_subtract_availability_for_shifts`; `graph.py` subtracts existing shifts from availability at load time using `_subtract_consumed`, the interval arithmetic built for #85. Because `graph.py` is the only place availability is loaded, both scheduling paths inherit this with no per-path work.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x async, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-28-editing-approved-schedules-design.md` (Stage 1)

## Global Constraints

- **Run tests with `backend/.venv/bin/python -m pytest`** from the repo root. The system Anaconda python lacks this project's dependencies and fails on imports, which looks like broken code. Never bare `pytest`.
- **The suite is at 660 passed.** Report the exact count after each task.
- **Do not install new dependencies.**
- **No migration.** This stage adds no table and no column.
- **Never raise inside the scheduling graph.** It degrades to a status; it does not throw.
- **Times are wall-clock and must never be `.astimezone()`d on read.** Availability is stored as local wall-clock tagged UTC (`09:00:00+00:00`) while shifts carry the location's real offset (`09:00:00-04:00`). Comparing them as instants is the bug #85 fixed; compare their wall-clock faces via `_wall_clock`.
- **Multi-tenancy:** every query filters by `company_id`.
- **This stage ships alone.** Editing approved schedules is a separate plan; `schedules.py:356` still blocks it when this lands.

---

### Task 1: The no-op gate

Before changing anything, pin the behaviour that must not change. Approving currently carves availability; after this plan the pipeline subtracts shifts instead. The **net result must be identical** — otherwise the change has altered scheduling rather than relocating where it is computed.

This task writes that gate first so the rest of the plan is safe to review.

**Files:**
- Test: `tests/test_availability_holds.py`

**Interfaces:**
- Consumes: nothing
- Produces: nothing later tasks import. It is a gate that must stay green through Tasks 2 and 3.

- [ ] **Step 1: Write the characterisation test**

```python
"""Availability holds: approving must not destroy availability (#84 stage 1).

The gate for this change is that generation output does not move. Approve
stops carving employee_availability and the pipeline starts subtracting Shift
rows instead; if the schedules that come out differ, the change has altered
scheduling behaviour rather than relocating where consumption is computed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability

pytestmark = pytest.mark.asyncio


async def test_approving_leaves_availability_rows_intact(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The point of the whole change.

    Before: approve deleted the covering window and re-inserted leftovers, so
    an employee available 09:00-17:00 who worked 13:00-21:00 ended with NO
    availability rows for that date. After: the rows are untouched and
    consumption lives in the Shift row.
    """
    emp_id, work_date = approved_schedule_fixture

    rows = (await db_session.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.employee_id == emp_id,
        )
    )).scalars().all()

    same_day = [r for r in rows if r.start_time.date() == work_date]
    assert same_day, (
        "approving must not delete the employee's availability for the day "
        "they worked — consumption is derived from the Shift row instead"
    )
    assert len(same_day) == 1, "the window should be untouched, not split"
```

- [ ] **Step 2: Build the fixture**

Add to the same file. It approves a schedule through the real code path, so it exercises whatever `approve` currently does.

```python
@pytest.fixture
async def approved_schedule_fixture(db_session: AsyncSession, seed_company):
    """Approve one shift for one employee and return (employee_id, date).

    Uses the real approve path rather than inserting Shift rows directly, so
    the test reflects what approving actually does to availability.
    """
    from backend.models import Shift, ShiftSchedule

    emp_id = seed_company["employee_id"]
    work_date = (datetime.now(timezone.utc) + timedelta(days=7)).date()

    # Availability 09:00-17:00, stored as local wall-clock tagged UTC.
    db_session.add(EmployeeAvailability(
        id="avholds1",
        company_id=seed_company["company_id"],
        employee_id=emp_id,
        start_time=datetime(work_date.year, work_date.month, work_date.day, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(work_date.year, work_date.month, work_date.day, 17, 0, tzinfo=timezone.utc),
    ))
    await db_session.commit()
    return emp_id, work_date
```

Reuse whatever company/employee fixture `tests/test_schedules.py` already uses; name it `seed_company` here and adapt to the existing fixture's actual name and shape when you write it. If no such fixture exists, build the minimal `Company` + `Employee` + `Location` rows inline.

- [ ] **Step 3: Run it to see it FAIL against current behaviour**

Run: `backend/.venv/bin/python -m pytest tests/test_availability_holds.py -v`
Expected: **FAIL.** With today's code, approve carves the window, so `same_day` is empty or the window has been split. That failure is the bug this plan fixes — record the exact output in your report.

- [ ] **Step 4: Commit the failing gate**

```bash
git add tests/test_availability_holds.py
git commit -m "test(scheduling): pin that approving must not destroy availability

Currently fails: approve deletes the covering window. Fixed in the next
commits, where consumption moves to the Shift rows."
```

Committing a red test deliberately, so the diff shows the bug being closed rather than a test appearing already-green.

---

### Task 2: Approve stops mutating availability

**Files:**
- Modify: `backend/routers/schedules.py` — remove the call at ~line 468 and the function at line 24

**Interfaces:**
- Consumes: nothing
- Produces: `approve` no longer touches `EmployeeAvailability`. Task 3 relies on this — if both the carve and the subtraction ran, availability would be consumed twice.

- [ ] **Step 1: Delete the call**

In `approve_schedule`, remove this block (it sits just after the `json.JSONDecodeError` handler and just before the `EmployeeRoleMinutes` accumulation):

```python
            # Subtract consumed hours from employee availability.
            # For each approved shift, find the overlapping availability window
            # and split it around the shift (removing only the scheduled hours).
            await _subtract_availability_for_shifts(
                db, current_user.company_id, shifts_data,
            )
```

- [ ] **Step 2: Delete the function**

Remove `_subtract_availability_for_shifts` entirely — it begins at `backend/routers/schedules.py:24` and is the only definition. Confirm it has no other caller first:

```bash
grep -rn "_subtract_availability_for_shifts" backend/ --include="*.py" | grep -v "\.venv"
```

Expected after removal: no hits.

Drop any imports that become unused as a result (`EmployeeAvailability` may still be used elsewhere in the file — check before removing it).

- [ ] **Step 3: Run the gate test**

Run: `backend/.venv/bin/python -m pytest tests/test_availability_holds.py -v`
Expected: **PASS.** Availability rows now survive approval.

- [ ] **Step 4: Run the full suite and expect failures**

Run: `backend/.venv/bin/python -m pytest tests/ -q`

Expected: **some tests fail.** Availability is no longer consumed anywhere, so anything asserting that a second generation avoids an already-scheduled employee will now double-book. That is the point of Task 3.

**Record which tests fail and their exact messages** — they are your acceptance criteria for Task 3. Do not weaken or delete them.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/schedules.py
git commit -m "refactor(scheduling): approving no longer carves availability

Deletes _subtract_availability_for_shifts. Approving now materialises Shift
rows and nothing else; consumption moves to read time in the next commit.

Some scheduling tests fail at this commit by design — availability is not yet
subtracted anywhere. The next commit restores that from the Shift rows."
```

---

### Task 3: The pipeline subtracts shifts at load time

**Files:**
- Modify: `backend/scheduling/graph.py` — add `Shift` to the model imports (~line 8-22), and insert the subtraction after `emp_avail_map` is built (~line 486)
- Test: `tests/test_availability_holds.py` (extend)

**Interfaces:**
- Consumes: `backend.scheduling.nodes._wall_clock(ts: str) -> datetime` and `backend.scheduling.nodes._subtract_consumed(window_start: datetime, window_end: datetime, consumed: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]`. Both already exist, added by #85.
- Produces: `emp_avail_map` values already have committed shifts carved out before the pipeline sees them. Nothing downstream changes shape — still `list[dict]` with `"start"` / `"end"` ISO strings.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_availability_holds.py`:

```python
async def test_an_approved_shift_removes_those_hours_from_availability(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """Consumption still happens — it is just computed at read time now."""
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    # Available 09:00-17:00, worked 13:00-21:00 -> 09:00-13:00 remains.
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T09:00:00+00:00")
    assert windows[0]["end"].endswith("T13:00:00+00:00")


async def test_deleting_the_shift_releases_the_hold(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """Releasing a hold and deleting a shift are the same act."""
    from sqlalchemy import delete
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    await db_session.execute(delete(Shift).where(Shift.employee_id == emp_id))
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    assert len(windows) == 1
    assert windows[0]["end"].endswith("T17:00:00+00:00"), (
        "the full window returns once nothing references those hours"
    )


async def test_the_same_span_subtracted_twice_is_a_no_op(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """There is no unique constraint on (location_id, week_start_date), so a
    manager who regenerates and re-approves produces two approved schedules
    covering the same hours. Availability must not be double-consumed."""
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    original = (await db_session.execute(
        __import__("sqlalchemy").select(Shift).where(Shift.employee_id == emp_id)
    )).scalars().first()

    db_session.add(Shift(
        id="dupshft1",
        company_id=original.company_id,
        shift_schedule_id=original.shift_schedule_id,
        location_id=original.location_id,
        employee_id=original.employee_id,
        role_id=original.role_id,
        role_name=original.role_name,
        date=original.date,
        start_time=original.start_time,
        end_time=original.end_time,
    ))
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    assert len(windows) == 1
    assert windows[0]["end"].endswith("T13:00:00+00:00"), (
        "two identical shifts consume the same hours once, not twice"
    )


async def test_a_malformed_shift_timestamp_does_not_raise(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The scheduling graph degrades; it never throws."""
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    # Should not raise even if a row is unparseable.
    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    assert isinstance(avail, dict)
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_availability_holds.py -v`
Expected: FAIL with `ImportError: cannot import name '_load_employee_availability'`.

- [ ] **Step 3: Extract the availability load into a testable function**

The subtraction has to be reachable from a test, and the current code is inline inside a long function. Extract lines ~460-495 of `backend/scheduling/graph.py` (from `avail_result = await db.execute(` through the empty-availability fill) into a module-level function, then call it from where the inline code was:

```python
async def _load_employee_availability(
    db: AsyncSession,
    company_id: str,
    week_start_date: str,
    num_days: int,
    all_employee_ids: set[str] | None = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Employee availability for the week, minus hours already committed.

    A Shift row IS a hold: rather than carving employee_availability at
    approve time (which destroyed it irreversibly), consumption is derived
    here at read time. Deleting a shift therefore releases its hold with no
    separate release step, and the two cannot disagree.

    Both scheduling paths load availability through this one function, so
    neither can bypass it.
    """
    week_start = datetime.strptime(week_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=num_days)

    avail_result = await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.company_id == company_id,
            EmployeeAvailability.start_time >= week_start,
            EmployeeAvailability.start_time < week_end,
        )
    )
    emp_avail_map: Dict[str, List[Dict[str, str]]] = {}
    for av in avail_result.scalars().all():
        eid = str(av.employee_id)
        emp_avail_map.setdefault(eid, [])

        start_dt = av.start_time
        end_dt = av.end_time
        # Fix midnight end times: if end <= start, the end_time was stored as
        # 00:00:00 on the same date (meaning "end of day"), so bump it to 23:59.
        if start_dt and end_dt and end_dt <= start_dt:
            end_dt = start_dt.replace(hour=23, minute=59, second=0, microsecond=0)

        emp_avail_map[eid].append({
            "start": start_dt.isoformat() if hasattr(start_dt, "isoformat") else str(start_dt),
            "end": end_dt.isoformat() if hasattr(end_dt, "isoformat") else str(end_dt),
        })

    emp_avail_map = _subtract_committed_shifts(
        emp_avail_map,
        await _committed_shifts_by_employee(db, company_id, week_start, week_end),
    )

    # Employees with NO availability records are treated as "not available".
    # They get an empty list so the LLM and validator will not schedule them
    # until explicit availability is provided.
    for eid in (all_employee_ids or set()) - set(emp_avail_map.keys()):
        emp_avail_map[eid] = []

    return emp_avail_map
```

Replace the inline block in the calling function with:

```python
    emp_avail_map = await _load_employee_availability(
        db, company_id, week_start_date, num_days,
        all_employee_ids={str(emp.id) for emp in employees_orm},
    )
```

- [ ] **Step 4: Add the two helpers**

Place them immediately above `_load_employee_availability`:

```python
async def _committed_shifts_by_employee(
    db: AsyncSession,
    company_id: str,
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """Already-committed shift spans for the week, per employee.

    Returned as naive wall-clock pairs. Shift timestamps carry the location's
    real offset while availability is wall-clock tagged UTC, so they are only
    comparable face-to-face — see _wall_clock (#85).
    """
    rows = (await db.execute(
        select(Shift).where(
            Shift.company_id == company_id,
            Shift.start_time >= week_start,
            Shift.start_time < week_end,
        )
    )).scalars().all()

    by_emp: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for s in rows:
        try:
            start = _wall_clock(s.start_time.isoformat())
            end = _wall_clock(s.end_time.isoformat())
        except (AttributeError, ValueError, TypeError):
            # A row we cannot read is skipped rather than raised on: the
            # scheduling graph degrades, it never throws.
            continue
        by_emp.setdefault(str(s.employee_id), []).append((start, end))
    return by_emp


def _subtract_committed_shifts(
    emp_avail_map: Dict[str, List[Dict[str, str]]],
    committed: Dict[str, List[Tuple[datetime, datetime]]],
) -> Dict[str, List[Dict[str, str]]]:
    """Carve each employee's committed shifts out of their availability.

    Output timestamps are rebuilt with each window's own original offset, so
    the shape handed to the pipeline is unchanged.
    """
    out: Dict[str, List[Dict[str, str]]] = {}
    for eid, windows in emp_avail_map.items():
        spans = committed.get(eid, [])
        if not spans:
            out[eid] = windows
            continue

        rebuilt: List[Dict[str, str]] = []
        for w in windows:
            try:
                start_aware = datetime.fromisoformat(w["start"])
                end_aware = datetime.fromisoformat(w["end"])
            except (KeyError, ValueError, TypeError):
                rebuilt.append(w)
                continue

            tz = start_aware.tzinfo
            for p_start, p_end in _subtract_consumed(
                start_aware.replace(tzinfo=None), end_aware.replace(tzinfo=None), spans,
            ):
                rebuilt.append({
                    "start": p_start.replace(tzinfo=tz).isoformat(),
                    "end": p_end.replace(tzinfo=tz).isoformat(),
                })
        out[eid] = rebuilt
    return out
```

- [ ] **Step 5: Add the imports**

At the top of `backend/scheduling/graph.py`, add `Shift` to the `backend.models` import list (alphabetically, before `ShiftTemplate`), add `Tuple` to the `typing` import, and add:

```python
from backend.scheduling.nodes import _subtract_consumed, _wall_clock
```

Check for a circular import: `graph.py` already imports from `nodes.py`, so this is safe. Confirm with:

```bash
backend/.venv/bin/python -c "import backend.scheduling.graph; print('imports ok')"
```

- [ ] **Step 6: Run the new tests**

Run: `backend/.venv/bin/python -m pytest tests/test_availability_holds.py -v`
Expected: all pass.

- [ ] **Step 7: Run the full suite — the tests that broke in Task 2 must be green again**

Run: `backend/.venv/bin/python -m pytest tests/ -q`

Expected: **660 + your new tests, zero failures.** Specifically, every test you recorded as failing in Task 2 Step 4 must now pass without having been modified. If any still fails, the subtraction is not equivalent to the carve it replaced — investigate rather than adjusting the test.

- [ ] **Step 8: Verify the no-behaviour-change claim end to end**

```bash
export DATABASE_URL="postgresql+asyncpg://shiftsync:shiftsync@localhost:5433/shiftsync"
backend/.venv/bin/python -m backend.seed
backend/.venv/bin/python -m uvicorn backend.main:app --port 8000 &
```

Log in as `abc@example.com` / `example`, generate a two-location week with `{"use_local": true, "strategy": "rotation"}`, approve both schedules, then generate the **same week again**. The second generation must not re-offer employees who are already committed — that is the behaviour the old carve provided and this change must preserve. Record both outputs in your report.

Note: docker-compose maps Postgres to host port **5433**, not 5432. Do not edit `.env`.

- [ ] **Step 9: Commit**

```bash
git add backend/scheduling/graph.py tests/test_availability_holds.py
git commit -m "feat(scheduling): derive availability holds from shift rows

Consumption moves from approve-time destruction to read-time subtraction.
graph.py is the only place availability is loaded, so both scheduling paths
inherit this with no per-path work.

Releasing a hold is deleting a shift — there is no separate release step, so
the two cannot disagree. Reassignment moves the hold for free: changing
employee_id frees one employee and commits the other."
```

---

### Task 4: Prove the release paths

Task 3 proved the arithmetic. This proves the two behaviours the whole design rests on, through the real endpoints rather than helper calls.

**Files:**
- Test: `tests/test_availability_holds.py` (extend)

**Interfaces:**
- Consumes: `_load_employee_availability` from Task 3.
- Produces: nothing.

- [ ] **Step 1: Write the tests**

```python
async def test_rejecting_a_schedule_frees_the_hours(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The live bug this stage fixes, independent of editing.

    Before: rejecting consumed availability permanently, because approve had
    already destroyed it. After: no Shift rows means no hold.
    """
    from sqlalchemy import delete
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    await db_session.execute(delete(Shift).where(Shift.employee_id == emp_id))
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    assert avail[emp_id][0]["end"].endswith("T17:00:00+00:00")


async def test_reassigning_moves_the_hold(
    db_session: AsyncSession, approved_schedule_fixture, second_employee_id,
):
    """Changing employee_id frees one employee and commits the other, with no
    explicit release step — the hold IS the row."""
    from sqlalchemy import select
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    shift = (await db_session.execute(
        select(Shift).where(Shift.employee_id == emp_id)
    )).scalars().first()
    shift.employee_id = second_employee_id
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id="comp0001", week_start_date=work_date.isoformat(), num_days=7,
    )
    assert avail[emp_id][0]["end"].endswith("T17:00:00+00:00"), "original employee freed"
    assert avail[second_employee_id][0]["end"].endswith("T13:00:00+00:00"), "new employee committed"
```

Add a `second_employee_id` fixture giving a second employee in the same company with the same `09:00–17:00` availability on `work_date`.

- [ ] **Step 2: Run them**

Run: `backend/.venv/bin/python -m pytest tests/test_availability_holds.py -v`
Expected: all pass.

- [ ] **Step 3: Verify the check-in lock is real**

The spec claims a hold becomes non-releasable when the employee checks in, enforced by `employee_check_ins_shift_id_fkey`. Confirm rather than assume — against Postgres, since SQLite enforces foreign keys only with a PRAGMA the suite does not set:

```bash
docker exec wiz_scheduler-postgres-1 psql -U shiftsync -d shiftsync -c "
select conname, confdeltype from pg_constraint
where conname = 'employee_check_ins_shift_id_fkey';"
```

Expected: one row, `confdeltype = a` (NO ACTION). Record it. If it is `c` (cascade) the spec's guarantee is wrong and the design needs revisiting — stop and raise it.

- [ ] **Step 4: Run the full suite**

Run: `backend/.venv/bin/python -m pytest tests/ -q`
Expected: all pass; report the count.

- [ ] **Step 5: Commit**

```bash
git add tests/test_availability_holds.py
git commit -m "test(scheduling): prove holds release on delete and follow reassignment"
```

---

## Self-Review

**Spec coverage.** Stage 1 of the spec requires: a Shift row is the hold with no new table (Task 3); approve stops mutating availability (Task 2); the pipeline subtracts at `graph.py`'s single load point so both paths inherit it (Task 3); releasing a hold is deleting a shift (Task 4); reassignment moves the hold for free (Task 4); the check-in FK makes a hold non-releasable (Task 4 Step 3); duplicate approved schedules do not double-subtract (Task 3); existing carved data does not double-count — no migration (covered by the no-op gate in Task 1 plus Task 3 Step 7, since the seeded database already contains carved availability); malformed timestamps degrade rather than raise (Task 3). The spec's Stage 2 has no tasks here, correctly — it is a separate plan.

**Placeholder scan.** No TBDs. Two steps require judgement rather than transcription and say so explicitly: Task 1 Step 2 adapts to whichever company/employee fixture `tests/test_schedules.py` already provides, and Task 3 Step 3 extracts a block whose exact line numbers will have shifted. Both name the file, the boundaries, and the resulting signature.

**Type consistency.** `_wall_clock(ts: str) -> datetime` and `_subtract_consumed(window_start, window_end, consumed) -> list[tuple]` are used with the signatures they already have in `nodes.py`. `_committed_shifts_by_employee` returns `Dict[str, List[Tuple[datetime, datetime]]]`, which is exactly what `_subtract_committed_shifts` accepts as `committed` and what `_subtract_consumed` accepts as `consumed`. `_load_employee_availability` returns `Dict[str, List[Dict[str, str]]]` — the same shape the inline code produced, so nothing downstream changes.

**One risk worth naming.** Task 2 deliberately leaves the suite red, and Task 3 turns it green. An implementer who stops between them has a tree where availability is consumed nowhere. The commit message at Task 2 Step 5 says so, and Task 3 Step 7 makes restoring those exact tests the acceptance criterion.
