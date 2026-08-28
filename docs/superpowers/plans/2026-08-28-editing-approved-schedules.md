# Editing Approved Schedules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a manager edit an approved schedule within a month of `created_date`, writing to the `shifts` table, with three overridable warnings and two hard refusals.

**Architecture:** A new `PUT /schedules/{id}/approved-shifts` endpoint operates on `Shift` rows rather than the `raw_llm_output` blob, because after approval every consumer reads the table. Availability for the warning checks is computed exactly as the pipeline computes it, so a hand-edited schedule is judged by the same standard as a generated one.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.x async, pytest/pytest-asyncio; React 18 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-08-28-editing-approved-schedules-design.md` (Stage 2)

## Global Constraints

- **This plan requires `docs/superpowers/plans/2026-08-28-availability-holds.md` to have shipped first.** Editing without holds corrupts availability: removing a shift would free the shift but permanently strand the employee's hours. Do not start until holds are on `main`.
- **Run tests with `backend/.venv/bin/python -m pytest`** from the repo root. Never bare `pytest` — the system Anaconda python lacks this project's dependencies.
- **Do not install new dependencies.**
- **Multi-tenancy:** every query filters by `company_id`; an edit must verify the schedule and every referenced employee belong to the caller's company.
- **Times are wall-clock carrying the location's offset and must never be `.astimezone()`d.** This is the contract from #61 that #85 and #92 both turn on.
- **All 19 locale files must carry every new key** (`ar bn de en es fr hi id ja mr pcm pt ru ta te tr ur vi zh`). `LanguageContext.tsx` types translations as `Record<Language, Translations>`, so a key added to `en.ts` alone fails the TypeScript build.
- **The frontend has NO test runner.** Gates are `npx tsc --noEmit` and `npm run build` from `frontend/`, plus `tests/test_sidebar_routes.py`, which catches a nav link with no route and a `labelKey` missing from `en.ts` — `tsc` cannot, because the lookup goes through `as keyof typeof t.nav`.

---

### Task 1: The window and the two refusals

Start with what the endpoint refuses, because those are the rules that protect data. Warnings come next.

**Files:**
- Modify: `backend/schemas/schedule.py` — add the request/response models
- Modify: `backend/routers/schedules.py` — add the endpoint
- Test: `tests/test_edit_approved_schedule.py`

**Interfaces:**
- Consumes: `backend.services.schedule_lock.acquire(db, *, company_id, user_id, operation, ttl_seconds=None) -> ScheduleLock`, which raises `LockHeld`.
- Produces:
  - `PUT /api/v1/schedules/{schedule_id}/approved-shifts`
  - `ApprovedShiftEdit` — `{shift_id: str | None, employee_id: str, role_id: str, start_time: str, end_time: str, date: str, deleted: bool = False}`. A null `shift_id` means a new shift.
  - `EditApprovedResponse` — `{applied: int, warnings: list[EditWarning]}`
  - `EditWarning` — `{code: str, shift_id: str | None, employee_id: str, detail: str}`
  - `settings.APPROVED_SCHEDULE_EDIT_DAYS: int = 30`

- [ ] **Step 1: Write the failing tests**

```python
"""Editing an approved schedule (#84 stage 2).

Two hard refusals guard the data: a shift someone has checked into cannot be
touched at all, and the whole schedule freezes a month after created_date.
Everything else is a warning the manager can override.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/schedules"


async def test_editing_a_draft_still_uses_the_old_endpoint(
    client: AsyncClient, manager_token: str, draft_schedule_id: str,
):
    """This endpoint is for approved schedules only; drafts keep their path."""
    resp = await client.put(
        f"{BASE}/{draft_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_approved"


async def test_an_edit_inside_the_window_is_allowed(
    client: AsyncClient, manager_token: str, approved_schedule_id: str,
):
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 0


async def test_an_edit_past_the_window_is_refused(
    client: AsyncClient, manager_token: str, old_approved_schedule_id: str,
):
    """created_date is the basis, per #84."""
    resp = await client.put(
        f"{BASE}/{old_approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "edit_window_closed"


async def test_a_checked_into_shift_cannot_be_deleted(
    client: AsyncClient, manager_token: str, checked_in_shift,
):
    """A check-in is a factual record that someone worked those hours.
    Rewriting the shift beneath it would make the record describe something
    that never happened. Postgres refuses this too, via
    employee_check_ins_shift_id_fkey — the API and the constraint agree."""
    schedule_id, shift_id = checked_in_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_a_checked_into_shift_cannot_be_modified(
    client: AsyncClient, manager_token: str, checked_in_shift, second_employee_id: str,
):
    schedule_id, shift_id = checked_in_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": second_employee_id}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_another_companys_schedule_is_not_found(
    client: AsyncClient, manager_token: str, other_company_schedule_id: str,
):
    resp = await client.put(
        f"{BASE}/{other_company_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 404
```

Build the fixtures in the same file: `draft_schedule_id`, `approved_schedule_id` (created now), `old_approved_schedule_id` (`created_at` set 40 days ago), `checked_in_shift` (an approved shift plus an `EmployeeCheckIn` row referencing it), `second_employee_id`, and `other_company_schedule_id`. Reuse the `client` / `manager_token` fixtures that `tests/test_plan_enforcement.py` already uses.

- [ ] **Step 2: Run them to confirm they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_schedule.py -v`
Expected: FAIL — 404 on every route, since the endpoint does not exist.

- [ ] **Step 3: Add the setting**

In `backend/config.py`, beside the other scheduling settings:

```python
    # How long an approved schedule stays editable, measured from created_at
    # (the basis specified in #84 — not week_start_date, which diverges for a
    # schedule approved well ahead of the week it covers).
    APPROVED_SCHEDULE_EDIT_DAYS: int = 30
```

- [ ] **Step 4: Add the schemas**

In `backend/schemas/schedule.py`:

```python
class ApprovedShiftEdit(BaseModel):
    """One edit to an approved schedule.

    shift_id is None for a new shift. deleted=True removes an existing one,
    in which case the other fields are ignored.
    """

    shift_id: str | None = None
    deleted: bool = False
    employee_id: str | None = None
    role_id: str | None = None
    date: date | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class EditApprovedShiftsRequest(BaseModel):
    edits: list[ApprovedShiftEdit]


class EditWarning(BaseModel):
    code: str
    shift_id: str | None
    employee_id: str
    detail: str


class EditApprovedResponse(BaseModel):
    applied: int
    warnings: list[EditWarning] = []
```

- [ ] **Step 5: Add the endpoint with refusals only**

In `backend/routers/schedules.py`. Warnings arrive in Task 3; this step establishes the guards.

```python
@router.put("/{schedule_id}/approved-shifts", response_model=EditApprovedResponse)
async def edit_approved_shifts(
    schedule_id: str,
    body: EditApprovedShiftsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> EditApprovedResponse:
    """Edit an approved schedule's shifts.

    Writes to the `shifts` table, not raw_llm_output: after approval every
    consumer reads the table (export_schedules.py, gdpr.py, check-ins), so
    editing the blob would be a silent no-op.
    """
    schedule = (await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "not_approved", "message": "This endpoint edits approved schedules only."},
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS)
    if schedule.created_at < cutoff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "edit_window_closed",
                "message": (
                    f"Approved schedules can be edited for "
                    f"{settings.APPROVED_SCHEDULE_EDIT_DAYS} days after approval."
                ),
            },
        )

    touched_ids = [e.shift_id for e in body.edits if e.shift_id]
    if touched_ids:
        locked = (await db.execute(
            select(EmployeeCheckIn.shift_id).where(
                EmployeeCheckIn.company_id == current_user.company_id,
                EmployeeCheckIn.shift_id.in_(touched_ids),
            )
        )).scalars().all()
        if locked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "shift_locked_by_checkin",
                    "message": "An employee has already checked in against this shift.",
                    "shift_ids": [str(s) for s in locked],
                },
            )

    return EditApprovedResponse(applied=0, warnings=[])
```

Add `EmployeeCheckIn` to the `backend.models` import and `timedelta` to the `datetime` import if not already present.

- [ ] **Step 6: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_schedule.py -v`
Expected: all pass. The two "allowed" tests return `applied: 0` because nothing is applied yet — that is Task 2.

- [ ] **Step 7: Run the full suite**

Run: `backend/.venv/bin/python -m pytest tests/ -q`
Expected: no regressions; report the count.

- [ ] **Step 8: Commit**

```bash
git add backend/config.py backend/schemas/schedule.py backend/routers/schedules.py tests/test_edit_approved_schedule.py
git commit -m "feat(api): approved-schedule edit endpoint with its refusals

Guards first, mutation next. A checked-into shift is refused outright rather
than warned — the attendance record is factual, and Postgres refuses the
delete anyway via employee_check_ins_shift_id_fkey, so the API and the
constraint agree instead of fighting."
```

---

### Task 2: Apply the edits

**Files:**
- Modify: `backend/routers/schedules.py` — the endpoint body
- Test: `tests/test_edit_approved_schedule.py` (extend)

**Interfaces:**
- Consumes: the endpoint and schemas from Task 1.
- Produces: edits actually mutate `Shift` rows; `applied` counts them.

- [ ] **Step 1: Write the failing tests**

```python
async def test_deleting_a_shift_removes_the_row(
    client: AsyncClient, manager_token: str, approved_shift, db_session,
):
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1

    remaining = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one_or_none()
    assert remaining is None


async def test_reassigning_changes_the_employee(
    client: AsyncClient, manager_token: str, approved_shift, second_employee_id, db_session,
):
    """The hold moves with the row — no separate release step (stage 1)."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": second_employee_id}]},
    )
    assert resp.status_code == 200

    shift = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one()
    assert shift.employee_id == second_employee_id


async def test_an_edit_is_visible_to_the_week_endpoint(
    client: AsyncClient, manager_token: str, approved_shift,
):
    """Proves the edit wrote to the shifts table, not raw_llm_output.

    The week endpoint reads materialised Shift rows — the same source
    export_schedules.py and the approved-schedule calendar read. An edit that
    only touched the blob would leave this response unchanged."""
    schedule_id, shift_id = approved_shift
    await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    week = await client.get(
        f"{BASE}/week/2026-08-31?status=approved",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    ids = [s["id"] for sched in week.json() for s in sched["shifts"]]
    assert shift_id not in ids


async def test_adding_a_shift_creates_a_row(
    client: AsyncClient, manager_token: str, approved_schedule_id, second_employee_id, seed_role_id,
):
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{
            "shift_id": None,
            "employee_id": second_employee_id,
            "role_id": seed_role_id,
            "date": "2026-08-31",
            "start_time": "2026-08-31T09:00:00-04:00",
            "end_time": "2026-08-31T13:00:00-04:00",
        }]},
    )
    assert resp.status_code == 201 or resp.json()["applied"] == 1


async def test_two_concurrent_edits_conflict(
    client: AsyncClient, manager_token: str, approved_schedule_id, held_lock,
):
    """Approve takes the same lock; without it two managers editing one week
    silently overwrite each other."""
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "schedule_locked"
```

Add fixtures `approved_shift` (returns `(schedule_id, shift_id)`), `seed_role_id`, and `held_lock` (acquires the company lock as a different user before the request).

- [ ] **Step 2: Run them to confirm they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_schedule.py -v`
Expected: the new tests fail — `applied` is still hard-coded to 0 and no lock is taken.

- [ ] **Step 3: Take the lock**

In the endpoint, immediately after the check-in refusal and before applying anything, mirroring `approve_schedule`:

```python
    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="edit_approved",
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

Wrap the rest of the handler in `try: ... finally: await release_lock(db, lock.id)`, matching how `approve_schedule` does it.

- [ ] **Step 4: Apply the edits**

Inside the try block:

```python
    applied = 0
    for edit in body.edits:
        if edit.shift_id:
            shift = (await db.execute(
                select(Shift).where(
                    Shift.id == edit.shift_id,
                    Shift.company_id == current_user.company_id,
                )
            )).scalar_one_or_none()
            if shift is None:
                continue
            if edit.deleted:
                await db.delete(shift)
                applied += 1
                continue
            if edit.employee_id is not None:
                shift.employee_id = edit.employee_id
            if edit.role_id is not None:
                shift.role_id = edit.role_id
            if edit.date is not None:
                shift.date = edit.date
            if edit.start_time is not None:
                shift.start_time = edit.start_time
            if edit.end_time is not None:
                shift.end_time = edit.end_time
            applied += 1
        else:
            role_name = (await db.execute(
                select(Role.name).where(
                    Role.id == edit.role_id,
                    Role.company_id == current_user.company_id,
                )
            )).scalar_one_or_none()
            if role_name is None:
                continue
            db.add(Shift(
                company_id=current_user.company_id,
                shift_schedule_id=schedule.id,
                location_id=schedule.location_id,
                employee_id=edit.employee_id,
                role_id=edit.role_id,
                role_name=role_name,
                date=edit.date,
                start_time=edit.start_time,
                end_time=edit.end_time,
            ))
            applied += 1

    await db.commit()
    return EditApprovedResponse(applied=applied, warnings=[])
```

**Do not convert the timestamps.** Pydantic parses them offset-aware and SQLAlchemy stores them in a `timestamp with time zone` column; calling `.astimezone()` anywhere here breaks the wall-clock contract that #85 and #92 turn on.

Every employee referenced must belong to the caller's company. Add that check before assigning `employee_id`, returning 404 if not — a manager must not be able to schedule another tenant's staff.

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_schedule.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `backend/.venv/bin/python -m pytest tests/ -q`
Expected: no regressions; report the count.

- [ ] **Step 7: Commit**

```bash
git add backend/routers/schedules.py tests/test_edit_approved_schedule.py
git commit -m "feat(api): apply edits to approved schedules

Writes to the shifts table under the same lock approve takes. A test asserts
the change is visible through the week endpoint, which reads materialised
rows — proving the edit did not just touch raw_llm_output."
```

---

### Task 3: The three warnings

**Files:**
- Modify: `backend/routers/schedules.py`
- Create: `backend/services/edit_warnings.py`
- Test: `tests/test_edit_approved_warnings.py`

**Interfaces:**
- Consumes: `_wall_clock`, `_subtract_consumed` from `backend.scheduling.nodes`; `EditWarning` from Task 1.
- Produces: `collect_edit_warnings(db, company_id, edits, schedule) -> list[EditWarning]` in `backend/services/edit_warnings.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Three overridable warnings on an approved-schedule edit (#84 stage 2).

All three apply the edit anyway. A manager routinely knows something the
availability table does not — a verbal swap, an emergency cover — and
refusing outright would make the feature useless in exactly the cases it
exists for.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/schedules"


async def codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


async def test_no_availability_warns_but_applies(
    client: AsyncClient, manager_token: str, approved_shift, employee_without_availability,
):
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_without_availability}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1, "warned, not refused"
    assert "no_availability" in await codes(resp)


async def test_already_booked_fires_on_a_partial_overlap(
    client: AsyncClient, manager_token: str, approved_shift, employee_busy_1200_to_2000,
):
    """A 12:00-20:00 shift genuinely conflicts with 13:00-21:00. An
    exact-match test would miss most real conflicts."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_busy_1200_to_2000}]},
    )
    assert resp.status_code == 200
    assert "already_booked" in await codes(resp)


async def test_already_booked_does_not_fire_on_an_adjacent_shift(
    client: AsyncClient, manager_token: str, approved_shift, employee_busy_0600_to_1300,
):
    """Ends exactly when the other begins — touching, not overlapping."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_busy_0600_to_1300}]},
    )
    assert "already_booked" not in await codes(resp)


async def test_already_exported_warns(
    client: AsyncClient, manager_token: str, exported_shift,
):
    """7shifts now disagrees with the schedule and nothing re-exports."""
    schedule_id, shift_id = exported_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 200
    assert "already_exported" in await codes(resp)


async def test_a_clean_edit_warns_about_nothing(
    client: AsyncClient, manager_token: str, approved_shift, fully_available_employee,
):
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": fully_available_employee}]},
    )
    assert resp.json()["warnings"] == []


async def test_deleting_never_warns_about_availability(
    client: AsyncClient, manager_token: str, approved_shift,
):
    """Removing a shift cannot make anyone unavailable."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert "no_availability" not in await codes(resp)
    assert "already_booked" not in await codes(resp)
```

Fixtures needed: `employee_without_availability`, `employee_busy_1200_to_2000`, `employee_busy_0600_to_1300`, `fully_available_employee` (all in the caller's company), and `exported_shift` (an approved shift with `exported_at` set).

- [ ] **Step 2: Run them to confirm they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_warnings.py -v`
Expected: FAIL — `warnings` is still an empty list.

- [ ] **Step 3: Write the warning service**

Create `backend/services/edit_warnings.py`:

```python
"""Warnings raised by an approved-schedule edit.

All three are advisory: the edit applies regardless. A manager frequently
knows what the availability table does not, and refusing outright would make
editing useless in exactly the situations it exists for. Refusals live in the
router — a checked-into shift and a closed edit window — because those protect
data rather than advise about it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Shift
from backend.scheduling.nodes import _subtract_consumed, _wall_clock


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Strict overlap. Touching intervals do not overlap: a shift ending at
    13:00 does not conflict with one starting at 13:00."""
    return a_start < b_end and b_start < a_end


async def collect_edit_warnings(
    db: AsyncSession,
    company_id: str,
    edits: list[Any],
    schedule: Any,
) -> list[dict]:
    """Advisory warnings for a set of edits. Never raises."""
    warnings: list[dict] = []

    for edit in edits:
        if edit.deleted:
            # Removing a shift cannot make anyone unavailable or double-booked.
            if edit.shift_id:
                shift = (await db.execute(
                    select(Shift).where(
                        Shift.id == edit.shift_id, Shift.company_id == company_id,
                    )
                )).scalar_one_or_none()
                if shift is not None and shift.exported_at is not None:
                    warnings.append({
                        "code": "already_exported",
                        "shift_id": str(shift.id),
                        "employee_id": str(shift.employee_id),
                        "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
                    })
            continue

        emp_id = edit.employee_id
        start = edit.start_time
        end = edit.end_time
        if emp_id is None or start is None or end is None:
            # Partial edit that does not move the assignment — nothing to check.
            continue

        span_start = _wall_clock(start.isoformat())
        span_end = _wall_clock(end.isoformat())

        # 1. no_availability — the employee's windows, minus their other
        #    shifts, must cover the span. Same definition the pipeline uses.
        windows = (await db.execute(
            select(EmployeeAvailability).where(
                EmployeeAvailability.company_id == company_id,
                EmployeeAvailability.employee_id == emp_id,
            )
        )).scalars().all()
        others = (await db.execute(
            select(Shift).where(
                Shift.company_id == company_id,
                Shift.employee_id == emp_id,
                Shift.id != (edit.shift_id or ""),
            )
        )).scalars().all()
        other_spans = [
            (_wall_clock(s.start_time.isoformat()), _wall_clock(s.end_time.isoformat()))
            for s in others
        ]

        covered = False
        for w in windows:
            w_start = _wall_clock(w.start_time.isoformat())
            w_end = _wall_clock(w.end_time.isoformat())
            for free_start, free_end in _subtract_consumed(w_start, w_end, other_spans):
                if free_start <= span_start and free_end >= span_end:
                    covered = True
                    break
            if covered:
                break

        if not covered:
            warnings.append({
                "code": "no_availability",
                "shift_id": edit.shift_id,
                "employee_id": str(emp_id),
                "detail": "This employee has no availability covering these hours.",
            })

        # 2. already_booked — overlap, not exact match.
        for other_start, other_end in other_spans:
            if _overlaps(span_start, span_end, other_start, other_end):
                warnings.append({
                    "code": "already_booked",
                    "shift_id": edit.shift_id,
                    "employee_id": str(emp_id),
                    "detail": "This employee already works an overlapping shift.",
                })
                break

        # 3. already_exported
        if edit.shift_id:
            shift = (await db.execute(
                select(Shift).where(
                    Shift.id == edit.shift_id, Shift.company_id == company_id,
                )
            )).scalar_one_or_none()
            if shift is not None and shift.exported_at is not None:
                warnings.append({
                    "code": "already_exported",
                    "shift_id": str(shift.id),
                    "employee_id": str(emp_id),
                    "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
                })

    return warnings
```

- [ ] **Step 4: Call it from the endpoint**

Collect warnings **before** applying the edits, so the checks see the pre-edit state:

```python
    from backend.services.edit_warnings import collect_edit_warnings

    warning_dicts = await collect_edit_warnings(
        db, str(current_user.company_id), body.edits, schedule,
    )
```

and return `warnings=[EditWarning(**w) for w in warning_dicts]`.

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_edit_approved_warnings.py -v`
Expected: all pass.

- [ ] **Step 6: Verify the warnings are load-bearing**

Neuter `collect_edit_warnings` to `return []`, re-run, and confirm exactly the warning tests fail and no others. Restore. Report the result — a warning nobody can observe failing is not tested.

- [ ] **Step 7: Run the full suite**

Run: `backend/.venv/bin/python -m pytest tests/ -q`
Expected: no regressions; report the count.

- [ ] **Step 8: Commit**

```bash
git add backend/services/edit_warnings.py backend/routers/schedules.py tests/test_edit_approved_warnings.py
git commit -m "feat(api): three overridable warnings on approved-schedule edits

no_availability, already_booked (overlap, not exact match), already_exported.
All advisory — the edit applies regardless, because a manager routinely knows
what the availability table does not.

Availability is computed exactly as the pipeline computes it, via
_subtract_consumed, so a hand-edited schedule is judged by the same standard
as a generated one."
```

---

### Task 4: Editing in the calendar

**Files:**
- Modify: `frontend/src/api/approvedSchedules.ts`
- Modify: `frontend/src/pages/manager/ApprovedSchedules.tsx`
- Create: `frontend/src/components/shared/EditShiftModal.tsx`
- Modify: all 19 locale files

**Interfaces:**
- Consumes: `PUT /schedules/{id}/approved-shifts` from Tasks 1–3.
- Produces: an editable approved-schedule calendar.

- [ ] **Step 1: Add the API client function**

In `frontend/src/api/approvedSchedules.ts`:

```ts
export interface EditWarning {
  code: "no_availability" | "already_booked" | "already_exported";
  shift_id: string | null;
  employee_id: string;
  detail: string;
}

export interface ApprovedShiftEdit {
  shift_id?: string | null;
  deleted?: boolean;
  employee_id?: string;
  role_id?: string;
  date?: string;
  start_time?: string;
  end_time?: string;
}

export function editApprovedShifts(
  scheduleId: string,
  edits: ApprovedShiftEdit[]
): Promise<{ applied: number; warnings: EditWarning[] }> {
  return apiFetch(`/schedules/${scheduleId}/approved-shifts`, {
    method: "PUT",
    body: JSON.stringify({ edits }),
  });
}
```

- [ ] **Step 2: Make the grid editable on this page**

`ScheduleGrid` already takes `editable` and `onEditShift(shiftIndex)` — it was built that way for the Schedule page. Pass `editable` when the schedule is inside its edit window, and open `EditShiftModal` from `onEditShift`.

Compute the window client-side from `created_at` so the affordance matches the API: `Date.now() - created_at < 30 days`. The server remains the authority; this only avoids offering an edit that will be refused.

- [ ] **Step 3: Build the modal**

`EditShiftModal.tsx`: employee select, role select, start/end time inputs, a Delete action, Save and Cancel. Follow `SpecialHoursModal.tsx` for structure and styling.

Times are wall-clock — display and submit the face value, never `new Date(...)` round-trips, which would re-project into the browser's timezone (that is #92).

- [ ] **Step 4: Surface the three warnings distinctly**

On a 200 with a non-empty `warnings` array, keep the modal open and show each warning, styled by severity:

- `already_booked` — strongest treatment; a genuine physical conflict
- `no_availability` — cautionary; a record-keeping gap
- `already_exported` — informational; the external system now disagrees

The edit has already applied at this point, so the wording must say what happened rather than ask permission: "Saved, with warnings."

On a 409 `shift_locked_by_checkin`, show that the shift is locked and why. On 409 `schedule_locked`, show who holds the lock, matching how the Schedule page already handles `ScheduleLockedError`.

- [ ] **Step 5: Add i18n keys to all 19 locales**

Genuinely translated, not English pasted in. Needed: modal title and field labels, save/delete/cancel, the three warning headlines and bodies, the two lock messages, and the read-only notice for a schedule past its window.

- [ ] **Step 6: Verify**

```bash
cd frontend && npx tsc --noEmit && npm run build
cd .. && backend/.venv/bin/python -m pytest tests/test_sidebar_routes.py -v
backend/.venv/bin/python -m pytest tests/ -q
```

Expected: `tsc` exit 0, build succeeds, sidebar guards pass, backend suite unchanged.

- [ ] **Step 7: Manual verification, and report what is unverified**

With the local stack up, approve a schedule, then: reassign a shift and confirm the change persists after reload; delete a shift; trigger each of the three warnings; and confirm a schedule older than 30 days renders read-only. List anything you could not check without a browser.

- [ ] **Step 8: Commit**

```bash
git add frontend/src tests/
git commit -m "feat(ui): edit approved schedules from the calendar"
```

---

## Self-Review

**Spec coverage.** Stage 2 of the spec requires: edits write to `shifts` not the blob (Task 2, with a test asserting visibility through the week endpoint); the month window measured from `created_date` (Task 1); checked-into shifts refused outright (Task 1); the three warnings, all overridable (Task 3); `already_booked` testing overlap rather than exact match (Task 3, with an adjacent-shift test proving touching does not fire); one definition of "free" shared with the pipeline (Task 3 uses `_subtract_consumed`); the schedule lock (Task 2); wall-clock times never converted (Tasks 2 and 4). The spec's out-of-scope items — which of several approved schedules is authoritative, automatic re-export, employee notification, restoring already-destroyed availability — correctly have no tasks.

**Placeholder scan.** No TBDs. Task 4 is described prose-first because it is UI work whose structure follows existing pages rather than novel logic; each step names the file, the pattern to follow, and the gate. Fixture construction is described rather than written out in several tasks because the fixtures must match whatever `tests/test_plan_enforcement.py` already provides, which the implementer can see and I cannot pin without guessing.

**Type consistency.** `ApprovedShiftEdit`, `EditApprovedShiftsRequest`, `EditWarning` and `EditApprovedResponse` are defined in Task 1 and used under those names in Tasks 2, 3 and 4. The TypeScript `EditWarning.code` union matches the three codes the service emits. `collect_edit_warnings` returns `list[dict]` and the router converts to `EditWarning` — deliberate, so the service has no FastAPI dependency and is unit-testable on its own.

**One ordering constraint that matters.** Task 3 collects warnings *before* Task 2's mutation runs, or the checks would inspect state the edit has already changed — `already_booked` in particular would compare a shift against itself. The step says so explicitly.
