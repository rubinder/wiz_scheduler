# Archive Instead of Delete (#126) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Removing an employee, location or role — by a manager, by GDPR erasure or by a 7shifts/Deputy re-import — archives the row instead of deleting it, so it never 500s on a foreign key and never destroys past approved schedules or pay records.

**Architecture:** `archived_at` on `employees`/`locations`/`roles` (and `erased_at` on `employees`) marks a row inactive; one service (`backend/services/archive.py`) owns archiving and restoring, and one helper (`active(Model)`) is the only way readers filter. Pay records snapshot the employee/location names they were worked under. Importers call the archive service instead of their hand-written delete cascades. Built on #174: "becomes an open shift" means nulling `Shift.employee_id`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Postgres (pytest + testcontainers), httpx `MockTransport` (ships with httpx — no new dependency); React 18 + TypeScript + Vite + Tailwind, vitest (pure tests only).

**Spec:** `docs/superpowers/specs/2026-10-14-archive-instead-of-delete-design.md`

## Global Constraints

- Migration is `0048`, `down_revision = "0047"`, file `backend/alembic/versions/0048_archive_not_delete.py`; plain `op.*` (no `batch_alter_table`).
- Only `services/billing.delete_og_and_dependents` may hard-delete an `Employee`, `Location` or `Role` (plus `seed.py`'s demo pruning). Every other path archives.
- "Today"/"now" is UTC: `datetime.now(timezone.utc)`, never `date.today()` (enforced by `tests/test_utc_today.py`).
- Open shift = `Shift.employee_id IS NULL` (#174). Converting a shift to open nulls `employee_id` and clears `preference_violations` to `[]`; it never changes time, role or location.
- A shift that has a check-in or a time entry is never modified or deleted by archiving.
- Roles are never hardcoded; no role-name string literals outside `seed.py` (tests may create roles with any name).
- Tailwind: logical utilities only (`ms-`, `text-start`, …). i18n: every new key in all 19 `frontend/src/i18n/*.ts` files (tsc enforces parity).
- No new dependencies.
- Every commit ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Tth1ftYwbLKMYmjgLSmWfi
  ```
- Backend tests: `pytest tests/<file> -v` from repo root (Docker must be running for testcontainers). Frontend: `cd frontend && npm test && npm run build`.

## Decisions taken while planning (spec silent; flagged for review)

1. **Billing follows the plan count.** `count_employees_for_group` also drives the paid per-employee charge; making it active-only (spec §2) means archived employees are not billed.
2. **Deputy archives employees only.** Today Deputy never removes locations or roles; this plan keeps that scope rather than adding new removals.
3. **A failed upstream fetch archives nothing.** Today a failed 7shifts `/roles` or Deputy `/supervise/employee` fetch leaves an empty list and the importer removes *every* existing row of that kind. The archive pass for an entity runs only when its fetch succeeded.
4. **Editing an archived row is allowed** (`PUT /employees/{id}` etc. include archived) so a manager can fix data before restoring.
5. **Bulk CSV upload** dedups against archived rows too (employees by email, locations/roles by name) and reports the match as "exists (archived)" instead of creating a duplicate.
6. **Approval of a draft whose role was archived** skips that role's entries (the valid-role set is active-only), matching "the scheduler ignores the role". Approving a draft at an archived location returns 409 `location_archived`.
7. **GDPR manager deletion** needs every `users.id` foreign key resolved, not only the three in the spec: token/lock tables cascade, attribution columns `SET NULL`.
8. **Historical reports include archived rows:** the check-in report, attestation rates, condensed-role name lookup, `graph.py`'s committed-shift timezone join, storage estimate.

## Review Focus

1. A re-import where one upstream fetch fails (non-200) — nothing of that kind may be archived. → Task 7 tests `test_7shifts_failed_roles_fetch_archives_no_role` and `test_deputy_failed_employee_fetch_archives_no_employee`.
2. A time-only edit of an approved shift whose existing assignee has since been archived — must succeed; only *assigning* an archived employee is refused. → Task 5 test `test_time_only_edit_keeps_archived_assignee`.
3. Archiving the same employee twice, or restoring an active one — idempotent 204/200, never a second conversion pass or error. → Task 3 tests `test_archive_twice_is_idempotent`, `test_restore_active_is_noop`.
4. Restoring on a free plan at the limit — 402 from `assert_can_add`, row stays archived. → Task 4 test `test_restore_employee_respects_free_limit`.
5. Payroll export after renaming an employee and location — the export shows the names as worked. → Task 6 test `test_export_uses_names_as_worked`.

---

## File Structure

**Create**
- `tests/fake_upstream.py` — `install_fake_upstream(monkeypatch, routes)`: replaces the importers' `httpx` with a `MockTransport`-backed client.
- `tests/test_import_archive_simulation_integration.py` — end-to-end 7shifts and Deputy import simulation (Task 1; un-xfailed in Task 7).
- `backend/alembic/versions/0048_archive_not_delete.py`
- `backend/services/archive.py` — `active()`, `archive_employee`, `archive_location`, `archive_role`, `restore_employee`, `restore_location`, `restore_role`, `erase_employee`, `ArchiveCounts`.
- `tests/test_archive_service_integration.py`, `tests/test_archive_endpoints_integration.py`, `tests/test_archive_readers_integration.py`, `tests/test_pay_record_snapshots_integration.py`, `tests/test_gdpr_archive_integration.py`, `tests/test_users_fk_ondelete.py`
- `frontend/src/utils/archived.ts` + `archived.test.ts`

**Modify (backend)** — models `employee.py`, `location.py`, `role.py`, `time_entry.py`, `payroll_export.py`, `employee_check_in.py`, `manager_invite.py`, `feedback_submission.py`, `gdpr_export_log.py`, `schedule_lock.py`, `password_reset_token.py`, `email_verification_token.py`; schemas for employee/location/role responses; routers `employees.py`, `locations.py`, `roles.py`, `schedules.py`, `check_ins.py`, `invites.py`, `affinities.py`, `scheduling_preferences.py`, `special_hours.py`, `open_shifts.py`, `manager_locations.py`, `import_7shifts.py`, `import_deputy.py`, `gdpr.py`; services `billing.py` (counts), `candidates.py`, `location_quota.py`, `manager_permissions.py`, `time_entries.py`, `payroll_export.py`; `scheduling/graph.py`; `tests/test_migrations.py`.

**Modify (frontend)** — `types/index.ts`, `api/employees.ts`, `api/locations.ts`, `api/roles.ts`, `api/import7shifts.ts`, `api/importDeputy.ts`, `components/shared/DataTable.tsx`, `components/shared/Import7ShiftsModal.tsx`, `components/shared/ImportDeputyModal.tsx`, `pages/manager/Employees.tsx`, `Locations.tsx`, `Roles.tsx`, all 19 `i18n/*.ts`.

**Docs** — `CLAUDE.md` (convention bullet), `README.md`.

---

### Task 1: Fake upstream harness and the failing import simulation

**Files:**
- Create: `tests/fake_upstream.py`
- Create: `tests/test_import_archive_simulation_integration.py`

**Interfaces:**
- Produces: `install_fake_upstream(monkeypatch, routes: dict[str, object]) -> list[httpx.Request]` — `routes` maps a URL **path** to a JSON-able body (served with 200), an `httpx.Response` (served as-is), or a callable `(httpx.Request) -> httpx.Response`. The dict is read on every request, so a test mutates it between imports. Unknown paths answer 404.
- Produces (fixtures in the test module): `paid_tenant` → `{"og_id", "company_id", "region_id", "user_id", "headers"}`.

Today both importers hard-delete the removed employee's shifts, which the time entry references, so the re-import raises an `IntegrityError`. The tests are committed as `xfail(strict=True)`; Task 7 removes the marks. `strict=True` means the suite fails the moment they start passing early, so nobody forgets.

- [ ] **Step 1: Write the harness**

```python
# tests/fake_upstream.py
"""A fake 7shifts/Deputy upstream for end-to-end importer tests (#126).

The importers build `httpx.AsyncClient(timeout=30.0)` inside each handler
from a module-level `import httpx`. Replacing that module reference with a
namespace whose `AsyncClient` mounts an `httpx.MockTransport` sends every
upstream call to `routes` instead of the network, while the real router,
database and response model all run.
"""

import types
from typing import Any

import httpx


def install_fake_upstream(monkeypatch, routes: dict[str, Any]) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": f"no fake for {request.url.path}"})
        if isinstance(answer, httpx.Response):
            return answer
        if callable(answer):
            return answer(request)
        return httpx.Response(200, json=answer)

    real_client = httpx.AsyncClient
    fake = types.SimpleNamespace(
        AsyncClient=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        RequestError=httpx.RequestError,
    )
    monkeypatch.setattr("backend.routers.import_7shifts.httpx", fake)
    monkeypatch.setattr("backend.routers.import_deputy.httpx", fake)
    return seen
```

- [ ] **Step 2: Write the simulation tests**

```python
# tests/test_import_archive_simulation_integration.py
"""End-to-end 7shifts and Deputy re-imports against a fake upstream (#126).

A re-import that no longer returns an employee, location or role must archive
it -- never delete it -- so past approved shifts, check-ins and pay records
survive, and the employee's future shifts become open shifts (#174).
"""

import copy
from datetime import date, datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, OwnershipGroup, PayrollExport,
    Region, Role, Shift, ShiftSchedule, SpecialHoursDay, TimeEntry, User,
)
from backend.tenant import tenant_scope_disabled
from tests.conftest import _id, _make_token
from tests.fake_upstream import install_fake_upstream

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)
PAST_DAY = (NOW - timedelta(days=21)).date()
FUTURE_DAY = (NOW + timedelta(days=7)).date()

V2 = "/v2"
NO_MORE = {"cursor": {"next": None}}
SEVEN_SHIFTS = {
    f"{V2}/companies": {"data": [{"id": 100, "name": "Acme"}]},
    f"{V2}/company/100/locations": {"data": [
        {"id": 11, "name": "Downtown", "timezone": "America/New_York"},
        {"id": 12, "name": "Uptown", "timezone": "America/Chicago"},
    ], "meta": NO_MORE},
    f"{V2}/company/100/departments": {"data": [], "meta": NO_MORE},
    f"{V2}/company/100/roles": {"data": [{"id": 21, "name": "Cook"}, {"id": 22, "name": "Server"}]},
    f"{V2}/company/100/users": {"data": [
        {"id": 31, "user_id": 9031, "first_name": "Ann", "last_name": "A", "email": "ann@x.test", "active": True},
        {"id": 32, "user_id": 9032, "first_name": "Ben", "last_name": "B", "email": "ben@x.test", "active": True},
    ], "meta": NO_MORE},
    f"{V2}/company/100/users/31/assignments": {"data": {
        "locations": [{"id": 11}, {"id": 12}], "roles": [{"id": 21, "skill_level": 2}, {"id": 22, "skill_level": 2}]}},
    f"{V2}/company/100/users/32/assignments": {"data": {
        "locations": [{"id": 12}], "roles": [{"id": 22, "skill_level": 1}]}},
}

DEPUTY = {
    "/api/v1/resource/Company": [
        {"Id": 1, "CompanyName": "Downtown", "Active": True, "Timezone": "America/New_York"},
        {"Id": 2, "CompanyName": "Uptown", "Active": True, "Timezone": "America/Chicago"},
    ],
    "/api/v1/resource/OperationalUnit": [
        {"Id": 51, "OperationalUnitName": "Cook", "Company": 1, "Active": True},
        {"Id": 52, "OperationalUnitName": "Server", "Company": 2, "Active": True},
    ],
    "/api/v1/supervise/employee": [
        {"Id": 71, "FirstName": "Ann", "LastName": "A", "Email": "ann@x.test", "Active": True,
         "WorkplaceArray": [{"Company": 1}, {"Company": 2}]},
        {"Id": 72, "FirstName": "Ben", "LastName": "B", "Email": "ben@x.test", "Active": True,
         "WorkplaceArray": [{"Company": 2}]},
    ],
    "/api/v1/supervise/unavail/71": [],
    "/api/v1/supervise/unavail/72": [],
}


@pytest_asyncio.fixture
async def paid_tenant(db_session: AsyncSession, monkeypatch) -> dict:
    """A paid ownership group, so assert_paid_plan passes on every import."""
    monkeypatch.setattr(settings, "INTEGRATION_IMPORT_BURST", 1000)
    og_id, company_id, region_id, user_id = _id(), _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Paid", stripe_subscription_id="sub_x"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Paid Co", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    db_session.add(User(id=user_id, company_id=company_id, email="mgr@paid.test",
                        hashed_password="x", full_name="Mgr", user_role="manager"))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id, "region_id": region_id, "user_id": user_id,
            "headers": {"Authorization": f"Bearer {_make_token(user_id, company_id, 'manager')}"}}


async def _one(db: AsyncSession, stmt):
    with tenant_scope_disabled():
        return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()


async def _history(db: AsyncSession, tenant: dict, emp: Employee, past_loc: Location,
                   future_loc: Location, role: Role, keep_role: Role) -> dict:
    """Past approved shift with a check-in, time entry, payroll export and
    special-hours day at `past_loc`; a future approved shift at `future_loc`."""
    cid = tenant["company_id"]
    past_sched = ShiftSchedule(id=_id(), company_id=cid, location_id=past_loc.id,
                               week_start_date=PAST_DAY - timedelta(days=PAST_DAY.weekday()),
                               status="approved")
    future_sched = ShiftSchedule(id=_id(), company_id=cid, location_id=future_loc.id,
                                 week_start_date=FUTURE_DAY - timedelta(days=FUTURE_DAY.weekday()),
                                 status="approved")
    db.add_all([past_sched, future_sched])
    await db.flush()
    past_start = datetime.combine(PAST_DAY, time(14), tzinfo=timezone.utc)
    past = Shift(id=_id(), company_id=cid, shift_schedule_id=past_sched.id, location_id=past_loc.id,
                 employee_id=emp.id, role_id=role.id, role_name=role.name, date=PAST_DAY,
                 start_time=past_start, end_time=past_start + timedelta(hours=8))
    future_start = datetime.combine(FUTURE_DAY, time(14), tzinfo=timezone.utc)
    future = Shift(id=_id(), company_id=cid, shift_schedule_id=future_sched.id,
                   location_id=future_loc.id, employee_id=emp.id, role_id=keep_role.id,
                   role_name=keep_role.name, date=FUTURE_DAY,
                   start_time=future_start, end_time=future_start + timedelta(hours=8))
    db.add_all([past, future])
    await db.flush()
    check_in = EmployeeCheckIn(id=_id(), company_id=cid, location_id=past_loc.id, employee_id=emp.id,
                               shift_id=past.id, checked_in_at=past_start, local_date=PAST_DAY,
                               counter=0, status="matched")
    db.add(check_in)
    await db.flush()
    export = PayrollExport(id=_id(), company_id=cid, exported_by_user_id=tenant["user_id"],
                           location_id=past_loc.id, format="csv", range_start=PAST_DAY,
                           range_end=PAST_DAY, entry_count=1, paid_minutes_total=480)
    db.add(export)
    await db.flush()
    entry = TimeEntry(id=_id(), company_id=cid, location_id=past_loc.id, employee_id=emp.id,
                      shift_id=past.id, role_id=role.id, role_name=role.name, pay_date=PAST_DAY,
                      start_time=past.start_time, end_time=past.end_time, paid_minutes=480,
                      source="checked_in", check_in_id=check_in.id, payroll_export_id=export.id)
    db.add(entry)
    db.add(SpecialHoursDay(id=_id(), company_id=cid, location_id=past_loc.id, date=FUTURE_DAY,
                           open_time=time(10), close_time=time(16)))
    await db.commit()
    return {"past": past.id, "future": future.id, "entry": entry.id, "check_in": check_in.id}


@pytest.mark.xfail(strict=True, reason="#126: re-import hard-deletes; fixed in Task 7")
async def test_7shifts_reimport_archives_and_restores(client: AsyncClient, db_session: AsyncSession,
                                                      paid_tenant: dict, monkeypatch):
    routes = copy.deepcopy(SEVEN_SHIFTS)
    install_fake_upstream(monkeypatch, routes)
    url, h = "/api/v1/import/7shifts", paid_tenant["headers"]

    first = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["employees"]["created"] == 2

    ann = await _one(db_session, select(Employee).where(Employee.external_id == "9031"))
    downtown = await _one(db_session, select(Location).where(Location.external_id == "11"))
    uptown = await _one(db_session, select(Location).where(Location.external_id == "12"))
    cook = await _one(db_session, select(Role).where(Role.external_id == "21"))
    server = await _one(db_session, select(Role).where(Role.external_id == "22"))
    ids = await _history(db_session, paid_tenant, ann, downtown, uptown, cook, server)

    # Ann, Downtown and Cook disappear upstream.
    routes[f"{V2}/company/100/users"]["data"] = routes[f"{V2}/company/100/users"]["data"][1:]
    routes[f"{V2}/company/100/locations"]["data"] = routes[f"{V2}/company/100/locations"]["data"][1:]
    routes[f"{V2}/company/100/roles"]["data"] = routes[f"{V2}/company/100/roles"]["data"][1:]

    second = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["employees"]["archived"] == 1
    assert body["locations"]["archived"] == 1
    assert body["roles"]["archived"] == 1

    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is not None
    assert (await _one(db_session, select(Location).where(Location.id == downtown.id))).archived_at is not None
    assert (await _one(db_session, select(Role).where(Role.id == cook.id))).archived_at is not None
    past = await _one(db_session, select(Shift).where(Shift.id == ids["past"]))
    assert past.employee_id == ann.id and past.role_id == cook.id
    assert (await _one(db_session, select(TimeEntry).where(TimeEntry.id == ids["entry"]))).employee_id == ann.id
    assert (await _one(db_session, select(EmployeeCheckIn).where(EmployeeCheckIn.id == ids["check_in"]))) is not None
    future = await _one(db_session, select(Shift).where(Shift.id == ids["future"]))
    assert future.employee_id is None  # open shift (#174)

    # Ann and Downtown come back upstream.
    routes[f"{V2}/company/100/users"]["data"].insert(0, copy.deepcopy(SEVEN_SHIFTS[f"{V2}/company/100/users"]["data"][0]))
    routes[f"{V2}/company/100/locations"]["data"].insert(0, copy.deepcopy(SEVEN_SHIFTS[f"{V2}/company/100/locations"]["data"][0]))

    third = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert third.status_code == 200, third.text
    assert third.json()["employees"]["restored"] == 1
    assert third.json()["locations"]["restored"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is None
    assert (await _one(db_session, select(Location).where(Location.id == downtown.id))).archived_at is None
    assert await _one(db_session, select(func.count()).select_from(Employee)
                      .where(Employee.external_id == "9031")) == 1


@pytest.mark.xfail(strict=True, reason="#126: re-import hard-deletes; fixed in Task 7")
async def test_deputy_reimport_archives_and_restores(client: AsyncClient, db_session: AsyncSession,
                                                     paid_tenant: dict, monkeypatch):
    routes = copy.deepcopy(DEPUTY)
    install_fake_upstream(monkeypatch, routes)
    url, h = "/api/v1/import/deputy", paid_tenant["headers"]
    req = {"access_token": "fake", "deputy_base_url": "https://fake.deputy.com"}

    first = await client.post(url, json=req, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["employees"]["created"] == 2

    ann = await _one(db_session, select(Employee).where(Employee.external_id == "deputy:71"))
    downtown = await _one(db_session, select(Location).where(Location.name == "Downtown"))
    uptown = await _one(db_session, select(Location).where(Location.name == "Uptown"))
    cook = await _one(db_session, select(Role).where(Role.name == "Cook"))
    server = await _one(db_session, select(Role).where(Role.name == "Server"))
    ids = await _history(db_session, paid_tenant, ann, downtown, uptown, cook, server)

    routes["/api/v1/supervise/employee"] = routes["/api/v1/supervise/employee"][1:]
    second = await client.post(url, json=req, headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["employees"]["archived"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is not None
    assert (await _one(db_session, select(Shift).where(Shift.id == ids["past"]))).employee_id == ann.id
    assert (await _one(db_session, select(Shift).where(Shift.id == ids["future"]))).employee_id is None

    routes["/api/v1/supervise/employee"].insert(0, copy.deepcopy(DEPUTY["/api/v1/supervise/employee"][0]))
    third = await client.post(url, json=req, headers=h)
    assert third.status_code == 200, third.text
    assert third.json()["employees"]["restored"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is None
    assert await _one(db_session, select(func.count()).select_from(Employee)
                      .where(Employee.external_id == "deputy:71")) == 1
```

If an import model import fails (e.g. `SpecialHoursDay` is not re-exported from `backend.models`), import it from its module (`backend.models.special_hours_day`). If the Deputy external_id format is not `deputy:<Id>` or locations/roles carry different names, read `import_deputy.py` and adjust the *lookups* only — not the scenario.

- [ ] **Step 3: Confirm the harness reaches the importers and the scenario fails for the right reason**

Temporarily remove the two `xfail` marks and run:
Run: `pytest tests/test_import_archive_simulation_integration.py -v`
Expected: both FAIL at the **second** import (an `IntegrityError`/500 on a foreign key, or `KeyError: 'archived'`). If either fails at the *first* import, the fixture JSON is wrong — fix the fixture until the first import returns 200 with `created == 2`. Restore the `xfail(strict=True)` marks.

- [ ] **Step 4: Run with the marks**

Run: `pytest tests/test_import_archive_simulation_integration.py -v`
Expected: 2 xfailed.

- [ ] **Step 5: Commit**

```bash
git add tests/fake_upstream.py tests/test_import_archive_simulation_integration.py
git commit -m "test(import): simulate 7shifts and Deputy re-imports against a fake upstream (#126)"
```

---

### Task 2: Migration 0048 and model columns

**Files:**
- Create: `backend/alembic/versions/0048_archive_not_delete.py`
- Modify: `backend/models/employee.py`, `location.py`, `role.py`, `time_entry.py`, `payroll_export.py`, `employee_check_in.py`, `manager_invite.py`, `feedback_submission.py`, `gdpr_export_log.py`, `schedule_lock.py`, `password_reset_token.py`, `email_verification_token.py`, `consent.py`
- Modify: `tests/test_migrations.py`
- Create: `tests/test_users_fk_ondelete.py`

**Interfaces:**
- Produces: `Employee.archived_at`, `Employee.erased_at`, `Location.archived_at`, `Role.archived_at` (`Mapped[datetime | None]`, `DateTime(timezone=True)`); `TimeEntry.employee_name: str`, `TimeEntry.location_name: str`, `TimeEntry.location_timezone: str` (NOT NULL); `PayrollExport.location_name: str | None`; `PayrollExport.exported_by_user_id: str | None`.

Foreign-key changes (model `ForeignKey(..., ondelete=...)` **and** migration):

| table.column | ondelete | nullable after |
|---|---|---|
| `employee_check_ins.shift_id` | SET NULL | yes (already) |
| `time_entries.check_in_id` | SET NULL | yes |
| `time_entries.payroll_export_id` | SET NULL | yes |
| `time_entries.approved_by_user_id` | SET NULL | yes |
| `time_entries.attested_by_user_id` | SET NULL | yes |
| `payroll_exports.exported_by_user_id` | SET NULL | **yes (was NOT NULL)** |
| `employees.user_id` | SET NULL | yes |
| `manager_invites.invited_by_user_id` | SET NULL | **yes** |
| `feedback_submissions.user_id` | SET NULL | **yes** |
| `gdpr_export_log.user_id` | SET NULL | **yes** |
| `activation_events.user_id` | SET NULL | yes |
| `schedule_locks.locked_by_user_id` | CASCADE | no |
| `password_reset_tokens.user_id` | CASCADE | no |
| `email_verification_tokens.user_id` | CASCADE | no |
| `user_consents.user_id` | CASCADE | no |

(`manager_locations.user_id` is already CASCADE.)

- [ ] **Step 1: Write the failing FK coverage test**

```python
# tests/test_users_fk_ondelete.py
"""Every foreign key to users.id says what happens when the user goes (#126).

GDPR deletion removes the `users` row. A foreign key with no ondelete turns
that into a 500 for any manager who ever approved, exported, invited or
reset a password.
"""

from backend.database import Base
import backend.models  # noqa: F401  (registers every table)


def test_every_users_fk_declares_ondelete():
    missing = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users" and fk.ondelete not in ("CASCADE", "SET NULL"):
                missing.append(f"{table.name}.{fk.parent.name}")
    assert missing == []


def test_set_null_columns_are_nullable():
    bad = []
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.ondelete == "SET NULL" and not fk.parent.nullable:
                bad.append(f"{table.name}.{fk.parent.name}")
    assert bad == []
```

(If `Base` lives elsewhere, import it from where `tests/conftest.py` imports it.)

Run: `pytest tests/test_users_fk_ondelete.py -v` → Expected: FAIL listing the user columns.

- [ ] **Step 2: Update the models**

Add to `Employee` (after `seniority_rank`):

```python
    # NULL = active. Archived rows stay so approved shifts and pay records
    # that reference them keep their history (#126). Readers filter with
    # services/archive.active(Employee).
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by GDPR erasure. An erased employee is archived and can never be restored.
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add the same `archived_at` (with a one-line comment) to `Location` and `Role`. Add to `TimeEntry` next to `role_name`:

```python
    # Snapshots taken when the entry is written, so a later rename, archive or
    # GDPR erasure never changes pay history (#126).
    employee_name: Mapped[str] = mapped_column(String, nullable=False)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    location_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
```

Add `location_name: Mapped[str | None] = mapped_column(String, nullable=True)` to `PayrollExport`, and apply every ondelete/nullable change in the table above to the model `mapped_column(...)` (change `Mapped[str]` to `Mapped[str | None]` where it becomes nullable). Import `DateTime`/`datetime` where missing.

Run: `pytest tests/test_users_fk_ondelete.py -v` → Expected: PASS.

- [ ] **Step 3: Write the migration**

```python
"""Archive instead of delete (#126).

archived_at on employees/locations/roles, erased_at on employees, name
snapshots on time_entries and payroll_exports, and ondelete rules on the
foreign keys whose referenced row can genuinely go.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

# (table, column, referenced table, ondelete, nullable after upgrade)
_FKS = [
    ("employee_check_ins", "shift_id", "shifts", "SET NULL", True),
    ("time_entries", "check_in_id", "employee_check_ins", "SET NULL", True),
    ("time_entries", "payroll_export_id", "payroll_exports", "SET NULL", True),
    ("time_entries", "approved_by_user_id", "users", "SET NULL", True),
    ("time_entries", "attested_by_user_id", "users", "SET NULL", True),
    ("payroll_exports", "exported_by_user_id", "users", "SET NULL", True),
    ("employees", "user_id", "users", "SET NULL", True),
    ("manager_invites", "invited_by_user_id", "users", "SET NULL", True),
    ("feedback_submissions", "user_id", "users", "SET NULL", True),
    ("gdpr_export_log", "user_id", "users", "SET NULL", True),
    ("activation_events", "user_id", "users", "SET NULL", True),
    ("schedule_locks", "locked_by_user_id", "users", "CASCADE", False),
    ("password_reset_tokens", "user_id", "users", "CASCADE", False),
    ("email_verification_tokens", "user_id", "users", "CASCADE", False),
    ("user_consents", "user_id", "users", "CASCADE", False),
]
# Columns that were NOT NULL before this migration.
_WAS_NOT_NULL = {
    ("payroll_exports", "exported_by_user_id"),
    ("manager_invites", "invited_by_user_id"),
    ("feedback_submissions", "user_id"),
    ("gdpr_export_log", "user_id"),
}


def _fk_name(table: str, column: str) -> str:
    """Existing constraint names vary (Postgres defaults, and the 8-char id
    migration recreated some), so look the name up instead of guessing."""
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            return fk["name"]
    raise RuntimeError(f"no foreign key on {table}.{column}")


def _swap_fk(table: str, column: str, ref: str, ondelete: str | None) -> None:
    op.drop_constraint(_fk_name(table, column), table, type_="foreignkey")
    op.create_foreign_key(f"{table}_{column}_fkey", table, ref, [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table in ("employees", "locations", "roles"):
        op.add_column(table, sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("employees", sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("time_entries", sa.Column("employee_name", sa.String(), nullable=True))
    op.add_column("time_entries", sa.Column("location_name", sa.String(), nullable=True))
    op.add_column("time_entries", sa.Column("location_timezone", sa.String(64), nullable=True))
    op.execute(
        "UPDATE time_entries te SET employee_name = e.full_name "
        "FROM employees e WHERE e.id = te.employee_id"
    )
    op.execute(
        "UPDATE time_entries te SET location_name = l.name, location_timezone = l.timezone "
        "FROM locations l WHERE l.id = te.location_id"
    )
    for column in ("employee_name", "location_name", "location_timezone"):
        op.alter_column("time_entries", column, nullable=False)

    op.add_column("payroll_exports", sa.Column("location_name", sa.String(), nullable=True))
    op.execute(
        "UPDATE payroll_exports p SET location_name = l.name "
        "FROM locations l WHERE l.id = p.location_id"
    )

    for table, column, ref, ondelete, nullable in _FKS:
        if (table, column) in _WAS_NOT_NULL:
            op.alter_column(table, column, existing_type=sa.String(8), nullable=True)
        _swap_fk(table, column, ref, ondelete)


def downgrade() -> None:
    nulls = op.get_bind().execute(sa.text(
        "SELECT (SELECT count(*) FROM payroll_exports WHERE exported_by_user_id IS NULL)"
        " + (SELECT count(*) FROM manager_invites WHERE invited_by_user_id IS NULL)"
        " + (SELECT count(*) FROM feedback_submissions WHERE user_id IS NULL)"
        " + (SELECT count(*) FROM gdpr_export_log WHERE user_id IS NULL)"
    )).scalar()
    if nulls:
        raise RuntimeError(
            f"{nulls} row(s) lost their user to a deletion; they cannot be made "
            "NOT NULL again. Downgrading past 0048 is refused."
        )
    archived = op.get_bind().execute(sa.text(
        "SELECT (SELECT count(*) FROM employees WHERE archived_at IS NOT NULL)"
        " + (SELECT count(*) FROM locations WHERE archived_at IS NOT NULL)"
        " + (SELECT count(*) FROM roles WHERE archived_at IS NOT NULL)"
    )).scalar()
    if archived:
        raise RuntimeError(
            f"{archived} archived row(s) exist; before 0048 they would show as active. "
            "Downgrading past 0048 is refused."
        )
    for table, column, ref, _ondelete, _nullable in _FKS:
        _swap_fk(table, column, ref, None)
        if (table, column) in _WAS_NOT_NULL:
            op.alter_column(table, column, existing_type=sa.String(8), nullable=False)
    op.drop_column("payroll_exports", "location_name")
    for column in ("location_timezone", "location_name", "employee_name"):
        op.drop_column("time_entries", column)
    op.drop_column("employees", "erased_at")
    for table in ("roles", "locations", "employees"):
        op.drop_column(table, "archived_at")
```

Check the real referenced table names in the models before running (e.g. `user_consents` vs `consents`, `gdpr_export_log`); fix `_FKS` to match `__tablename__`.

- [ ] **Step 4: Add migration tests** to `tests/test_migrations.py`, following `test_0047_downgrade_refuses_while_an_open_shift_exists`:

```python
def test_0048_backfills_time_entry_snapshots(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "downgrade", "0047")
    _psql(pg, (
        "INSERT INTO companies (id, name, slug) VALUES ('mgc00048', 'M', 'mig-48');"
        "INSERT INTO regions (id, company_id, name) VALUES ('mgr00048', 'mgc00048', 'R');"
        "INSERT INTO locations (id, company_id, region_id, name, timezone)"
        " VALUES ('mgl00048', 'mgc00048', 'mgr00048', 'Dock', 'America/Chicago');"
        "INSERT INTO roles (id, company_id, name) VALUES ('mgo00048', 'mgc00048', 'X');"
        "INSERT INTO employees (id, company_id, full_name) VALUES ('mge00048', 'mgc00048', 'Ann A');"
        "INSERT INTO shift_schedules (id, company_id, location_id, week_start_date, status)"
        " VALUES ('mgs00048', 'mgc00048', 'mgl00048', '2026-08-31', 'approved');"
        "INSERT INTO shifts (id, company_id, shift_schedule_id, location_id, employee_id,"
        " role_id, role_name, date, start_time, end_time)"
        " VALUES ('mgf00048', 'mgc00048', 'mgs00048', 'mgl00048', 'mge00048', 'mgo00048', 'X',"
        " '2026-08-31', '2026-08-31 09:00+00', '2026-08-31 17:00+00');"
        "INSERT INTO time_entries (id, company_id, location_id, employee_id, shift_id, role_id,"
        " role_name, pay_date, start_time, end_time, paid_minutes, source)"
        " VALUES ('mgt00048', 'mgc00048', 'mgl00048', 'mge00048', 'mgf00048', 'mgo00048', 'X',"
        " '2026-08-31', '2026-08-31 09:00+00', '2026-08-31 17:00+00', 480, 'manager_attested');"
    ))
    try:
        _alembic(url, "upgrade", "head")
        out = _psql(pg, "SELECT employee_name || '|' || location_name || '|' || location_timezone"
                        " FROM time_entries WHERE id = 'mgt00048';")
        assert "Ann A|Dock|America/Chicago" in out
    finally:
        _psql(pg, (
            "DELETE FROM time_entries WHERE id = 'mgt00048';"
            "DELETE FROM shifts WHERE id = 'mgf00048';"
            "DELETE FROM shift_schedules WHERE id = 'mgs00048';"
            "DELETE FROM employees WHERE id = 'mge00048';"
            "DELETE FROM roles WHERE id = 'mgo00048';"
            "DELETE FROM locations WHERE id = 'mgl00048';"
            "DELETE FROM regions WHERE id = 'mgr00048';"
            "DELETE FROM companies WHERE id = 'mgc00048';"
        ))


def test_0048_downgrade_refuses_while_a_row_is_archived(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")
    _psql(pg, (
        "INSERT INTO companies (id, name, slug) VALUES ('mgc00148', 'M', 'mig-148');"
        "INSERT INTO roles (id, company_id, name, archived_at) VALUES ('mgo00148', 'mgc00148', 'X', now());"
    ))
    try:
        assert "archived row(s) exist" in _alembic_fails(url, "downgrade", "0047")
    finally:
        _psql(pg, "DELETE FROM roles WHERE id = 'mgo00148'; DELETE FROM companies WHERE id = 'mgc00148';")
    _alembic(url, "downgrade", "0047")
    _alembic(url, "upgrade", "head")
```

If the existing `test_upgrade_head_downgrade_and_upgrade_again` downgrades past 0048 on an empty DB it still passes (no archived rows). Leave it.

- [ ] **Step 5: Run**

Run: `pytest tests/test_migrations.py tests/test_users_fk_ondelete.py -v`
Expected: PASS (including chain integrity and FK type compatibility).

Every other test that builds a `TimeEntry` now needs the three snapshot fields. Run `pytest tests/ -x -q`; for each failing fixture that constructs `TimeEntry(...)` directly, add `employee_name=..., location_name=..., location_timezone=...` with values from the fixture's own employee/location. Production writers are fixed in Task 6; until then `services/time_entries.py` must also pass them, so add them there now with the values it already loads (`c.employee_name`, `c.location.name`, `c.location.timezone` in `derive_time_entries`; in `attest_shift` load `Employee.full_name` with one extra `select`). Task 6 adds the tests for that behaviour.
Also update Task 1's `_history` helper: add `employee_name=emp.full_name, location_name=past_loc.name, location_timezone=past_loc.timezone` to its `TimeEntry(...)`.
Expected after: `pytest tests/ -q` all pass (the two simulation tests still xfail).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0048_archive_not_delete.py backend/models tests backend/services/time_entries.py
git commit -m "feat(db): archive columns, pay-record name snapshots, ondelete rules (#126)"
```

---

### Task 3: The archive service

**Files:**
- Create: `backend/services/archive.py`
- Test: `tests/test_archive_service_integration.py`

**Interfaces:**
- Consumes: Task 2 columns; `backend/scheduling/open_shifts` is not needed (rows, not draft JSON).
- Produces:
  ```python
  def active(model) -> ColumnElement[bool]            # model.archived_at.is_(None)
  @dataclass class ArchiveCounts: opened: int = 0; deleted: int = 0
  async def archive_employee(db, company_id: str, employee: Employee, *, now: datetime | None = None) -> ArchiveCounts
  async def archive_location(db, company_id: str, location: Location, *, now=None) -> ArchiveCounts
  async def archive_role(db, company_id: str, role: Role, *, now=None) -> ArchiveCounts
  async def restore_employee(db, company_id: str, employee: Employee) -> None   # raises EmployeeErased
  async def restore_location(db, company_id: str, location: Location) -> None
  async def restore_role(db, company_id: str, role: Role) -> None
  class EmployeeErased(Exception)
  ```
  None of these commit; callers commit. `assert_can_add` is the caller's job (Task 4), so importers can restore within their own plan logic.

Rules (spec §2):
- **Untouchable shift:** any shift with an `EmployeeCheckIn.shift_id` or `TimeEntry.shift_id` pointing at it.
- **Employee:** future approved shifts (`ShiftSchedule.status == "approved"`, `Shift.start_time > now`, `Shift.employee_id == emp.id`, not untouchable) → `employee_id = None`, `preference_violations = []`. Draft schedules are left alone (approval converts, Task 5). Pending `EmployeeInvite` → `status = "revoked"`. Set `archived_at = now` (only if currently NULL — idempotent; a second call returns `ArchiveCounts()` and changes nothing).
- **Location:** future approved shifts at the location (not untouchable) → deleted; draft schedules (`status != "approved"`) for the location and their shifts → deleted; approved schedules left. Set `archived_at`.
- **Role:** future approved shifts with `role_id == role.id` (not untouchable) → open. Set `archived_at`.
- **Restore:** clear `archived_at`; `restore_employee` raises `EmployeeErased` if `erased_at` is set.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_archive_service_integration.py
"""Archiving keeps history and opens or drops only future work (#126)."""

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeCheckIn, EmployeeInvite, Location, Role, Shift, ShiftSchedule,
)
from backend.services.archive import (
    EmployeeErased, active, archive_employee, archive_location, archive_role,
    restore_employee,
)
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)


async def _shift(db, *, emp_id, loc_id, role, start, status="approved") -> Shift:
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc_id,
                          week_start_date=start.date() - timedelta(days=start.weekday()),
                          status=status)
    db.add(sched)
    await db.flush()
    s = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc_id,
              employee_id=emp_id, role_id=role.id, role_name=role.name, date=start.date(),
              start_time=start, end_time=start + timedelta(hours=8),
              preference_violations=["x"])
    db.add(s)
    await db.flush()
    return s


async def _reload(db, model, id_):
    return (await db.execute(select(model).where(model.id == id_)
                             .execution_options(populate_existing=True))).scalar_one_or_none()


async def test_archive_employee_opens_future_keeps_past_and_checked_in(
        db_session: AsyncSession, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    past = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW - timedelta(days=2))
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    locked = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(hours=1))
    db_session.add(EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                                   shift_id=locked.id, checked_in_at=NOW, local_date=NOW.date(),
                                   counter=0, status="matched"))
    await db_session.flush()

    counts = await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    await db_session.commit()

    assert counts.opened == 1
    assert (await _reload(db_session, Shift, past.id)).employee_id == emp.id
    opened = await _reload(db_session, Shift, future.id)
    assert opened.employee_id is None and opened.preference_violations == []
    assert (await _reload(db_session, Shift, locked.id)).employee_id == emp.id
    assert (await _reload(db_session, Employee, emp.id)).archived_at == NOW


async def test_archive_employee_revokes_pending_invites(db_session, seed_employees):
    emp = seed_employees[0]
    invite = EmployeeInvite(id=_id(), company_id=COMPANY_ID, employee_id=emp.id, token=_id() * 4,
                            email="a@x.test", status="pending",
                            expires_at=NOW + timedelta(days=7))
    db_session.add(invite)
    await db_session.flush()
    await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    await db_session.commit()
    assert (await _reload(db_session, EmployeeInvite, invite.id)).status == "revoked"


async def test_archive_twice_is_idempotent(db_session, seed_location, seed_roles, seed_employees):
    emp = seed_employees[0]
    await archive_employee(db_session, COMPANY_ID, emp, now=NOW)
    again = await archive_employee(db_session, COMPANY_ID, emp, now=NOW + timedelta(days=1))
    await db_session.commit()
    assert again.opened == 0
    assert (await _reload(db_session, Employee, emp.id)).archived_at == NOW


async def test_restore_active_is_noop(db_session, seed_employees):
    emp = seed_employees[0]
    await restore_employee(db_session, COMPANY_ID, emp)
    assert emp.archived_at is None


async def test_restore_erased_employee_refused(db_session, seed_employees):
    emp = seed_employees[0]
    emp.archived_at = NOW
    emp.erased_at = NOW
    with pytest.raises(EmployeeErased):
        await restore_employee(db_session, COMPANY_ID, emp)


async def test_archive_location_drops_future_and_drafts_keeps_past(
        db_session, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    past = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW - timedelta(days=2))
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    draft = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role,
                         start=NOW + timedelta(days=9), status="draft")
    counts = await archive_location(db_session, COMPANY_ID, loc, now=NOW)
    await db_session.commit()
    assert counts.deleted == 2
    assert await _reload(db_session, Shift, past.id) is not None
    assert await _reload(db_session, Shift, future.id) is None
    assert await _reload(db_session, Shift, draft.id) is None
    assert await _reload(db_session, ShiftSchedule, draft.shift_schedule_id) is None
    assert (await _reload(db_session, Location, loc.id)).archived_at == NOW


async def test_archive_role_opens_future_shifts(db_session, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    future = await _shift(db_session, emp_id=emp.id, loc_id=loc.id, role=role, start=NOW + timedelta(days=2))
    counts = await archive_role(db_session, COMPANY_ID, role, now=NOW)
    await db_session.commit()
    assert counts.opened == 1
    assert (await _reload(db_session, Shift, future.id)).employee_id is None
    assert (await _reload(db_session, Role, role.id)).archived_at == NOW


async def test_active_filter(db_session, seed_employees):
    seed_employees[0].archived_at = NOW
    await db_session.commit()
    ids = (await db_session.execute(select(Employee.id).where(
        Employee.company_id == COMPANY_ID, active(Employee)))).scalars().all()
    assert seed_employees[0].id not in ids
    assert seed_employees[1].id in ids
```

Check `seed_employees`/`seed_roles`/`seed_location` in `tests/conftest.py` give at least two employees and a role; if `ShiftSchedule.status` uses a different draft value than `"draft"`, use the real one. If `EmployeeInvite` needs other required columns, add them.

Run: `pytest tests/test_archive_service_integration.py -v` → Expected: FAIL (`ModuleNotFoundError: backend.services.archive`).

- [ ] **Step 2: Implement**

```python
# backend/services/archive.py
"""Archive instead of delete (#126).

Employees, locations and roles are never hard-deleted outside the
ownership-group wipe: the row stays so every approved shift, check-in and
pay record pointing at it keeps its history. Archiving only changes work
that has not happened yet, and never a shift someone checked in to or was
paid for. Nothing here commits; callers own the transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeCheckIn, EmployeeInvite, Location, Role, Shift, ShiftSchedule, TimeEntry,
)


class EmployeeErased(Exception):
    """GDPR-erased employees are archived for good."""


@dataclass
class ArchiveCounts:
    opened: int = 0
    deleted: int = 0


def active(model) -> ColumnElement[bool]:
    """The one active-row filter: `select(Employee).where(active(Employee))`."""
    return model.archived_at.is_(None)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _untouchable():
    """A shift someone checked in to or was paid for is history, not a plan."""
    return (
        exists().where(EmployeeCheckIn.shift_id == Shift.id)
        | exists().where(TimeEntry.shift_id == Shift.id)
    )


def _future_approved(company_id: str, now: datetime):
    approved = select(ShiftSchedule.id).where(
        ShiftSchedule.company_id == company_id, ShiftSchedule.status == "approved",
    )
    return (
        Shift.company_id == company_id,
        Shift.shift_schedule_id.in_(approved),
        Shift.start_time > now,
        ~_untouchable(),
    )


async def _open(db: AsyncSession, *where) -> int:
    result = await db.execute(
        update(Shift).where(*where).values(employee_id=None, preference_violations=[])
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def archive_employee(db: AsyncSession, company_id: str, employee: Employee,
                           *, now: datetime | None = None) -> ArchiveCounts:
    if employee.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    opened = await _open(db, *_future_approved(company_id, now), Shift.employee_id == employee.id)
    await db.execute(
        update(EmployeeInvite)
        .where(EmployeeInvite.company_id == company_id, EmployeeInvite.employee_id == employee.id,
               EmployeeInvite.status == "pending")
        .values(status="revoked").execution_options(synchronize_session=False)
    )
    employee.archived_at = now
    return ArchiveCounts(opened=opened)


async def archive_location(db: AsyncSession, company_id: str, location: Location,
                           *, now: datetime | None = None) -> ArchiveCounts:
    if location.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    # The location is closing: its future work is cancelled, not refilled.
    future = await db.execute(
        delete(Shift).where(*_future_approved(company_id, now), Shift.location_id == location.id)
        .execution_options(synchronize_session=False)
    )
    drafts = select(ShiftSchedule.id).where(
        ShiftSchedule.company_id == company_id, ShiftSchedule.location_id == location.id,
        ShiftSchedule.status != "approved",
    )
    draft_shifts = await db.execute(
        delete(Shift).where(Shift.shift_schedule_id.in_(drafts), ~_untouchable())
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(ShiftSchedule).where(
            ShiftSchedule.id.in_(drafts),
            ~exists().where(Shift.shift_schedule_id == ShiftSchedule.id),
        ).execution_options(synchronize_session=False)
    )
    location.archived_at = now
    return ArchiveCounts(deleted=(future.rowcount or 0) + (draft_shifts.rowcount or 0))


async def archive_role(db: AsyncSession, company_id: str, role: Role,
                       *, now: datetime | None = None) -> ArchiveCounts:
    if role.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    opened = await _open(db, *_future_approved(company_id, now), Shift.role_id == role.id)
    role.archived_at = now
    return ArchiveCounts(opened=opened)


async def restore_employee(db: AsyncSession, company_id: str, employee: Employee) -> None:
    if employee.erased_at is not None:
        raise EmployeeErased(employee.id)
    employee.archived_at = None


async def restore_location(db: AsyncSession, company_id: str, location: Location) -> None:
    location.archived_at = None


async def restore_role(db: AsyncSession, company_id: str, role: Role) -> None:
    role.archived_at = None
```

Draft schedules in this codebase may store their shifts only in `ShiftSchedule.raw_llm_output` (not as `Shift` rows); deleting the `ShiftSchedule` row is what matters. If a draft schedule is referenced by something else (e.g. a schedule lock), delete or skip per the FK — run the test and follow the error.

- [ ] **Step 3: Run**

Run: `pytest tests/test_archive_service_integration.py -v` → Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/services/archive.py tests/test_archive_service_integration.py
git commit -m "feat(archive): archive and restore employees, locations and roles (#126)"
```

---

### Task 4: Manager archive/restore endpoints, lists and plan counts

**Files:**
- Modify: `backend/routers/employees.py` (`delete_employee` 281-331, `list_employees` ~162, `bulk_upload` ~409-447, `_to_response`), `backend/routers/locations.py` (`delete_location` 277-294, `list_locations` ~24, bulk ~202), `backend/routers/roles.py` (`delete_role` 169-183, `list_roles` ~22, bulk ~134), their response schemas in `backend/schemas/`
- Modify: `backend/services/billing.py` (`count_employees_for_group` 418, `count_locations_for_group` 432)
- Test: `tests/test_archive_endpoints_integration.py`

**Interfaces:**
- Consumes: `archive_*`, `restore_*`, `EmployeeErased`, `active` (Task 3); `assert_can_add(db, company_id, *, locations=0, employees=0)` and `assert_roster_editable(db, company_id)` from `services/plan.py`.
- Produces: `DELETE /employees/{id}`, `/locations/{id}`, `/roles/{id}` → 204 (archive). `POST /employees/{id}/restore`, `/locations/{id}/restore`, `/roles/{id}/restore` → 200 with the row's response body; 409 `{"code": "employee_erased"}` for an erased employee; 402 from `assert_can_add`. `GET /employees|/locations|/roles?include_archived=true` adds archived rows; each response item gains `archived_at: datetime | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_archive_endpoints_integration.py
"""Manager delete archives; restore brings it back (#126)."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.models import Employee, EmployeeCheckIn, Location, OwnershipGroup, Role, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.integration


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_delete_employee_with_check_in_archives_instead_of_500(
        client: AsyncClient, db_session, manager_token, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    start = datetime.now(timezone.utc) - timedelta(days=3)
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                          week_start_date=start.date() - timedelta(days=start.weekday()), status="approved")
    db_session.add(sched)
    await db_session.flush()
    shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc.id,
                  employee_id=emp.id, role_id=role.id, role_name=role.name, date=start.date(),
                  start_time=start, end_time=start + timedelta(hours=8))
    db_session.add(shift)
    await db_session.flush()
    db_session.add(EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                                   shift_id=shift.id, checked_in_at=start, local_date=start.date(),
                                   counter=0, status="matched"))
    await db_session.commit()

    resp = await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    assert resp.status_code == 204

    listed = (await client.get("/api/v1/employees", headers=_h(manager_token))).json()
    assert emp.id not in [e["id"] for e in listed]
    with_archived = (await client.get("/api/v1/employees?include_archived=true",
                                      headers=_h(manager_token))).json()
    row = next(e for e in with_archived if e["id"] == emp.id)
    assert row["archived_at"] is not None


async def test_restore_employee(client, db_session, manager_token, seed_employees):
    emp = seed_employees[0]
    await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None


async def test_restore_erased_employee_409(client, db_session, manager_token, seed_employees):
    emp = seed_employees[0]
    now = datetime.now(timezone.utc)
    emp.archived_at, emp.erased_at = now, now
    await db_session.commit()
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "employee_erased"


async def test_restore_employee_respects_free_limit(client, db_session, manager_token, seed_employees,
                                                    monkeypatch):
    emp = seed_employees[0]
    await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    from fastapi import HTTPException
    import backend.routers.employees as employees_router

    async def at_limit(*args, **kwargs):
        raise HTTPException(status_code=402, detail={"code": "plan_limit"})
    monkeypatch.setattr(employees_router, "assert_can_add", at_limit)
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 402
    row = (await db_session.execute(select(Employee).where(Employee.id == emp.id)
                                    .execution_options(populate_existing=True))).scalar_one()
    assert row.archived_at is not None


async def test_delete_location_and_role_archive(client, db_session, manager_token, seed_location, seed_roles):
    assert (await client.delete(f"/api/v1/locations/{seed_location.id}", headers=_h(manager_token))).status_code == 204
    assert (await client.delete(f"/api/v1/roles/{seed_roles[0].id}", headers=_h(manager_token))).status_code == 204
    locs = (await client.get("/api/v1/locations", headers=_h(manager_token))).json()
    roles = (await client.get("/api/v1/roles", headers=_h(manager_token))).json()
    assert seed_location.id not in [l["id"] for l in locs]
    assert seed_roles[0].id not in [r["id"] for r in roles]
    assert (await client.post(f"/api/v1/locations/{seed_location.id}/restore",
                              headers=_h(manager_token))).status_code == 200
    assert (await client.post(f"/api/v1/roles/{seed_roles[0].id}/restore",
                              headers=_h(manager_token))).status_code == 200


async def test_archived_employee_not_counted_for_plan(db_session, seed_employees):
    from backend.services.billing import count_employees_for_group
    from backend.models import Company
    company = (await db_session.execute(select(Company).where(Company.id == COMPANY_ID))).scalar_one()
    if company.ownership_group_id is None:
        pytest.skip("seed company has no ownership group")
    before = await count_employees_for_group(db_session, company.ownership_group_id)
    seed_employees[0].archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert await count_employees_for_group(db_session, company.ownership_group_id) == before - 1
```

If the conftest seed company has no ownership group, replace the skip with a local OG fixture modelled on `free_tenant` in `tests/test_plan_enforcement_integration.py` — the count test must actually run. If the patch target for `assert_can_add` differs (imported under another name), patch where `employees.py` looks it up.

Run: `pytest tests/test_archive_endpoints_integration.py -v` → Expected: FAIL.

- [ ] **Step 2: Implement**

`delete_employee` keeps `assert_roster_editable`, the company-scoped lookup and 404, then replaces the whole delete cascade with:

```python
    await archive_employee(db, current_user.company_id, employee)
    await db.commit()
```

Same shape for `delete_location` (`archive_location`) and `delete_role` (`archive_role`). Add restore endpoints next to each delete, e.g.:

```python
@router.post("/{employee_id}/restore", response_model=EmployeeResponse)
async def restore_employee_endpoint(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager),
) -> EmployeeResponse:
    await assert_roster_editable(db, current_user.company_id)
    employee = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.company_id == current_user.company_id,
    ))).scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    if employee.archived_at is not None:
        if employee.erased_at is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "employee_erased",
                                        "message": "An erased employee cannot be restored."})
        await assert_can_add(db, current_user.company_id, employees=1)
        await restore_employee(db, current_user.company_id, employee)
        await db.commit()
        await db.refresh(employee)
    return _to_response(employee)  # use whatever the router's existing response builder is
```

Locations: `assert_can_add(..., locations=1)`; roles: no plan check. Use each router's existing response type/builder name.

Lists: add `include_archived: bool = False` query param; when false add `.where(active(Model))`. Add `archived_at: datetime | None = None` to the three response schemas.

Bulk uploads (decision 5): role/location name lookups for *assigning* (employees.py ~409/416) use `active(...)`; dedup lookups (employees.py ~447 by email, locations.py ~202, roles.py ~134 by name) stay unfiltered, and a match whose `archived_at` is set is reported in the existing skipped/errors list as `"<name>: exists (archived) — restore it instead"`.

Counts: in `count_employees_for_group` and `count_locations_for_group` add `active(Employee)` / `active(Location)` to the `where`. Add a docstring line: "Archived rows are neither limited nor billed (#126)."

- [ ] **Step 3: Run**

Run: `pytest tests/test_archive_endpoints_integration.py tests/test_plan_enforcement_integration.py -v` then `pytest tests/ -q -k "employee or location or role or billing or plan"`
Expected: PASS. Existing tests that asserted a delete removes the row must now assert it is archived — update them to the new behaviour (the spec changes it), never delete their assertions.

- [ ] **Step 4: Commit**

```bash
git add backend/routers/employees.py backend/routers/locations.py backend/routers/roles.py backend/schemas backend/services/billing.py tests
git commit -m "feat(archive): manager delete archives, restore endpoints, active-only lists and plan counts (#126)"
```

---

### Task 5: Active-only readers, scheduling, approval and the picker

**Files:**
- Modify: `backend/scheduling/graph.py` (541 role map, 696 locations, 717 employees, plus `EmployeeRole` rows at ~722 whose role is archived), `backend/services/candidates.py` (~112 employees, ~117 `EmployeeRole` role filter), `backend/routers/open_shifts.py` (~55), `backend/routers/schedules.py` (`edit_approved_shifts` 468-482 / 566 / 612; `approve_schedule` ~745 / ~773-835 / 858-897), `backend/routers/check_ins.py` (49, 104), `backend/routers/invites.py` (95, 202, 249, 298), `backend/routers/affinities.py` (42, 89), `backend/routers/scheduling_preferences.py` (45), `backend/routers/special_hours.py` (28), `backend/routers/employees.py` (105, 124, 137, 583-770 employee-facing), `backend/routers/manager_locations.py` (64, 95, 144), `backend/services/manager_permissions.py` (29, 72, 107), `backend/services/location_quota.py` (217, 248)
- Test: `tests/test_archive_readers_integration.py`

**Interfaces:**
- Consumes: `active()`, `archive_employee`, `archive_location`, `archive_role` (Task 3).
- Produces: 409 `{"code": "employee_archived", ...}` from `edit_approved_shifts` when an edit **changes** the assignee to an archived employee; 409 `{"code": "location_archived"}` from `approve_schedule` and from check-in (`CheckInRejected("location_archived", ...)`); approval writes an archived employee's draft entries as open shifts.

Rules for every site in the list: add `.where(active(Model))` (or `active(Model)` inside an existing `where`). Leave these **unfiltered** (decision 8 / spec "everything"): `graph.py:373`, `candidates.py:142`, `edit_warnings.py` (all), `time_entries.py`, `payroll_export.py`, `schedules.py:628/694/764/970/980/1021`, `export_schedules.py` (all), `check_ins.py:146` report, `condensed_roles.py:25`, `gdpr.py`, importers (Task 7), `billing.calculate_storage_gb`, `delete_og_and_dependents`, `seed.py`. `invites.py:202/249` switch `scalar_one()` → `scalar_one_or_none()` and return 410 `{"code": "invite_revoked"}` when the employee is gone/archived.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_archive_readers_integration.py
"""Archived rows leave the planning surfaces and stay in the history (#126)."""

from datetime import datetime, time, timedelta, timezone
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.models import Employee, EmployeeRole, Shift, ShiftSchedule
from backend.services.candidates import rank_candidates
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_archived_employee_never_a_candidate(db_session, seed_location, seed_roles, seed_employees):
    emp, role = seed_employees[0], seed_roles[0]
    if not (await db_session.execute(select(EmployeeRole).where(
            EmployeeRole.employee_id == emp.id, EmployeeRole.role_id == role.id))).first():
        db_session.add(EmployeeRole(employee_id=emp.id, role_id=role.id, skill_level=3))
    emp.archived_at = NOW
    await db_session.commit()
    day = (NOW + timedelta(days=3)).date()
    cands = await rank_candidates(db_session, COMPANY_ID, seed_location, role.id,
                                  datetime.combine(day, time(9)), datetime.combine(day, time(17)))
    assert emp.id not in [c.employee_id for c in cands]


async def test_archived_employee_absent_from_scheduler_input(db_session, seed_location, seed_employees):
    from backend.scheduling import graph
    seed_employees[0].archived_at = NOW
    await db_session.commit()
    # Call the same loader generate uses; adapt the call to _load_initial_state's real signature.
    state = await graph._load_initial_state(db_session, COMPANY_ID, [seed_location.id],
                                            (NOW + timedelta(days=7)).date())
    ids = {e["id"] for e in state["employees"]} if isinstance(state, dict) else {e.id for e in state.employees}
    assert seed_employees[0].id not in ids


def _approved_edit_url(schedule_id):
    return f"/api/v1/schedules/{schedule_id}/shifts/approved"  # use the real edit route path


async def _approved(db, loc, role, emp, start):
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                          week_start_date=start.date() - timedelta(days=start.weekday()), status="approved")
    db.add(sched)
    await db.flush()
    shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc.id,
                  employee_id=emp.id if emp else None, role_id=role.id, role_name=role.name,
                  date=start.date(), start_time=start, end_time=start + timedelta(hours=8))
    db.add(shift)
    await db.commit()
    return sched, shift


async def test_assigning_archived_employee_409(client, db_session, manager_token, seed_location,
                                               seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    sched, shift = await _approved(db_session, seed_location, seed_roles[0], None, start)
    seed_employees[1].archived_at = NOW
    await db_session.commit()
    resp = await client.patch(_approved_edit_url(sched.id), headers=_h(manager_token),
                              json={"edits": [{"shift_id": shift.id, "employee_id": seed_employees[1].id}]})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "employee_archived"


async def test_time_only_edit_keeps_archived_assignee(client, db_session, manager_token, seed_location,
                                                      seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    emp = seed_employees[0]
    sched, shift = await _approved(db_session, seed_location, seed_roles[0], emp, start)
    emp.archived_at = NOW  # archived directly, so the shift was not opened
    await db_session.commit()
    new_start = start + timedelta(hours=1)
    resp = await client.patch(_approved_edit_url(sched.id), headers=_h(manager_token), json={"edits": [{
        "shift_id": shift.id, "employee_id": emp.id,
        "start_time": new_start.isoformat(), "end_time": (new_start + timedelta(hours=8)).isoformat()}]})
    assert resp.status_code == 200, resp.text


async def test_approval_opens_archived_employees_entries(client, db_session, manager_token, seed_location,
                                                         seed_roles, seed_employees):
    # Build a draft whose raw_llm_output names seed_employees[0], archive them, approve.
    # Copy the draft-construction helper from tests/test_open_shifts_approval_integration.py
    # (same week/shape), then:
    #   seed_employees[0].archived_at = NOW; commit
    #   POST the approve route
    #   assert every Shift written for that entry has employee_id None
    #   assert no EmployeeRoleMinutes row was written for seed_employees[0]
    ...


async def test_approving_draft_at_archived_location_409(client, db_session, manager_token, seed_location,
                                                        seed_roles, seed_employees):
    # Same draft helper; set seed_location.archived_at = NOW; approve → 409 code "location_archived".
    ...


async def test_check_in_at_archived_location_refused(client, db_session, employee_token, seed_location):
    seed_location.archived_at = NOW
    await db_session.commit()
    resp = await client.post(f"/api/v1/check-ins", headers=_h(employee_token),
                             json={"location_id": seed_location.id, "token": "x"})  # use the real route/body
    assert resp.status_code in (404, 409)


async def test_my_profile_hidden_when_archived(client, db_session, employee_token, seed_employees,
                                               seed_employee_user):
    emp = next(e for e in seed_employees if e.user_id == seed_employee_user.id)
    emp.archived_at = NOW
    await db_session.commit()
    resp = await client.get("/api/v1/employees/me", headers=_h(employee_token))
    assert resp.status_code == 404


async def test_week_view_still_shows_archived_employee(client, db_session, manager_token, seed_location,
                                                       seed_roles, seed_employees):
    start = NOW - timedelta(days=2)
    emp = seed_employees[0]
    await _approved(db_session, seed_location, seed_roles[0], emp, start)
    emp.archived_at = NOW
    await db_session.commit()
    week = start.date() - timedelta(days=start.weekday())
    resp = await client.get(f"/api/v1/schedules/week?week_start={week.isoformat()}",
                            headers=_h(manager_token))  # use get_week_schedules' real route
    assert resp.status_code == 200
    assert emp.full_name in resp.text
```

The two `...` tests are **not optional**: open `tests/test_open_shifts_approval_integration.py`, reuse its draft builder verbatim, and write the four assertions listed in the comments. Replace every "use the real route" placeholder with the actual path/body from the router before running (grep the decorators). These comments are instructions to the implementer, not code to commit.

Run: `pytest tests/test_archive_readers_integration.py -v` → Expected: FAIL.

- [ ] **Step 2: Implement**

`edit_approved_shifts` (468-482): select the full `Employee` and refuse only a **change** of assignee:

```python
if edit.employee_id is not None:
    employee = (await db.execute(select(Employee).where(
        Employee.id == edit.employee_id, Employee.company_id == current_user.company_id,
    ))).scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    current_assignee = existing_shift.employee_id if existing_shift is not None else None
    if employee.archived_at is not None and edit.employee_id != current_assignee:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "employee_archived", "index": index, "shift_id": edit.shift_id,
            "message": "This employee is archived; restore them before assigning shifts.",
        })
```

(Use the function's existing names for the loaded shift and the edit index — match the `shift_has_time_entry` 409 shape at 530-542.) Role checks at 566/612 get the same "only when the role changes" rule with `active(Role)`.

`approve_schedule`: after loading the schedule, refuse if its location is archived (409 `location_archived`). Before the loop at ~773, load `archived_ids = set((await db.execute(select(Employee.id).where(Employee.company_id == company_id, Employee.archived_at.is_not(None)))).scalars())`. In the loop, before `open_entry = is_open_entry(s)`:

```python
        if not is_open_entry(s) and s.get("employee_id") in archived_ids:
            # Archived since generation: the slot is still needed, so it
            # becomes an open shift rather than a shift for someone who left.
            s = to_open_entry(s)
            shifts_data[i] = s
```

(Use the loop's index variable; write back into `shifts_data` so the role-minutes loop at 858-897 skips it via `is_open_entry`.) The valid-role set at ~745 uses `active(Role)` (decision 6).

`graph.py`: `active(Location)` at 696, `active(Employee)` at 717, `active(Role)` at 541; drop `EmployeeRole` rows whose role id is not in the active role map; drop template slots whose role is archived. `candidates.py`: `active(Employee)` at 112; filter `EmployeeRole.role_id.in_(equivalent_roles)` against `select(Role.id).where(active(Role))`. `open_shifts.py:55`: keep the location lookup unfiltered (an open shift at an archived location can't exist after Task 3, and a 404 would hide a bug).

Check-in: `_load_location` stays company-scoped; after loading, if `location.archived_at` raise `CheckInRejected("location_archived", "This location is archived.")` in `create_check_in` and return 404 in `get_check_in_qr`. Employee lookup at 104 adds `active(Employee)` (→ existing 403).

All other listed sites: add `active(...)`.

- [ ] **Step 3: Run**

Run: `pytest tests/test_archive_readers_integration.py tests/test_open_shifts_candidates_integration.py tests/test_open_shifts_approval_integration.py tests/test_open_shifts_edit_integration.py -v` then `pytest tests/ -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend tests
git commit -m "feat(archive): archived rows leave scheduling, pickers, check-in and employee routes (#126)"
```

---

### Task 6: Pay records read their own names

**Files:**
- Modify: `backend/services/time_entries.py` (`derive_time_entries` ~332-347, `attest_shift` ~613-631, `list_time_entries` ~390-404, `list_exceptions` ~501), `backend/services/payroll_export.py` (`export_approved` ~142-161, where `PayrollExport` rows are created)
- Test: `tests/test_pay_record_snapshots_integration.py`

**Interfaces:**
- Consumes: `TimeEntry.employee_name/location_name/location_timezone`, `PayrollExport.location_name` (Task 2; writers already fill the time-entry columns since Task 2 Step 5).
- Produces: `list_time_entries` and `export_approved` select `TimeEntry` columns only for names/timezone and order by `TimeEntry.pay_date, TimeEntry.employee_name`; `PayrollExport.location_name` set on single-location exports.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pay_record_snapshots_integration.py
"""Pay history shows names as worked, after renames and archives (#126)."""

import pytest

pytestmark = pytest.mark.integration


async def test_export_uses_names_as_worked(client, db_session, manager_token, seed_location,
                                           seed_roles, seed_employees):
    # 1. Create an approved past shift and an approved TimeEntry for seed_employees[0]
    #    (copy the setup from tests/test_payroll_export*_integration.py).
    # 2. Rename: seed_employees[0].full_name = "Renamed Person"; seed_location.name = "Renamed Loc";
    #    seed_location.timezone = "Asia/Tokyo"; archive the employee (archived_at = now); commit.
    # 3. Export via the payroll export route.
    # 4. assert original full_name and original location name appear in the CSV;
    #    assert "Renamed Person" and "Renamed Loc" do not;
    #    assert the local times are in the ORIGINAL timezone.
    ...


async def test_entries_list_uses_snapshots(client, db_session, manager_token, seed_location,
                                           seed_roles, seed_employees):
    # Same setup; GET the entries route; the row shows the original employee and location names.
    ...


async def test_derive_and_attest_fill_snapshots(db_session, seed_location, seed_roles, seed_employees):
    # Call derive_time_entries / attest_shift directly (see tests/test_time_entries_service.py
    # for how); assert employee_name == employee.full_name, location_name == location.name,
    # location_timezone == location.timezone on the written rows.
    ...


async def test_single_location_export_records_location_name(client, db_session, manager_token,
                                                            seed_location, seed_roles, seed_employees):
    # Export with location_id=seed_location.id; the PayrollExport row has location_name == seed_location.name.
    ...
```

Fill each `...` from the existing payroll tests (`grep -l "export_approved\|/payroll" tests/`) — reuse their setup helpers verbatim; the assertions are spelled out in the comments and every one must be written. These comments are instructions, not code to commit.

Run: `pytest tests/test_pay_record_snapshots_integration.py -v` → Expected: the rename tests FAIL (live joins show the new names).

- [ ] **Step 2: Implement**

`list_time_entries`: replace `Employee.full_name, Location.name, Location.timezone` in the select with `TimeEntry.employee_name, TimeEntry.location_name, TimeEntry.location_timezone`; drop the `Employee`/`Location` joins (keep the attester `aliased(User)` outer join); order by `TimeEntry.pay_date, TimeEntry.employee_name`. Company/location filters keep using `TimeEntry.company_id`/`TimeEntry.location_id`.

`export_approved`: same swap, keep `.with_for_update(of=TimeEntry)`. Where the `PayrollExport` row is created, set `location_name=<location.name>` when `location_id` is set (load the Location once, company-scoped).

`list_exceptions` reads `_candidate_shifts` (live, for not-yet-entered shifts) — leave it.

- [ ] **Step 3: Run**

Run: `pytest tests/test_pay_record_snapshots_integration.py tests/ -q -k "payroll or time_entr"` → Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/services/time_entries.py backend/services/payroll_export.py tests
git commit -m "feat(payroll): time entries and exports show names as worked (#126)"
```

---

### Task 7: Importers archive and restore

**Files:**
- Modify: `backend/routers/import_7shifts.py` (result init 568-575; locations 181-257; departments 307-310; roles 314-374; users 377-475; availabilities 914), `backend/routers/import_deputy.py` (result model 76-81; matching 188-313; removal 406-424; availabilities 633)
- Modify: `tests/test_import_archive_simulation_integration.py` (remove both `xfail` marks; add the two failed-fetch tests)

**Interfaces:**
- Consumes: `archive_employee`, `archive_location`, `archive_role`, `restore_employee`, `restore_location`, `restore_role`, `EmployeeErased` (Task 3).
- Produces: 7shifts `ImportResult` entity dicts with keys `created`, `updated`, `archived`, `restored` (no `deleted`; `departments` keeps `deleted` — departments are hard-deleted only when they have no FK dependants, unchanged). Deputy `DeputyImportResult.locations/roles/employees` same keys; `unavailability` keeps `created/skipped/deleted`.

Rules:
- External-id matching stays unfiltered (includes archived). A match that is archived → `restore_*` and `restored += 1` (not `updated`). An employee match with `erased_at` set → skip it, create a new employee instead (spec §3), and don't count it as restored.
- Removal: replace every hand-written delete cascade for locations, roles and employees with `archive_*` calls; count `archived += 1` only when the row was active before.
- **Decision 3:** run an entity's archive pass only when that entity's fetch succeeded. 7shifts roles: track `roles_fetched_ok`; Deputy employees: `employees_fetched_ok`. 7shifts users and locations already 502 on failure.
- Restoring through an import does not call `assert_can_add` (the import's own plan gate already ran; the same is true of creating rows there today).
- Availability imports (7shifts 914, Deputy 633) match `active(Employee)` only.

- [ ] **Step 1: Remove the `xfail` marks and add the failed-fetch tests**

```python
async def test_7shifts_failed_roles_fetch_archives_no_role(client, db_session, paid_tenant, monkeypatch):
    routes = copy.deepcopy(SEVEN_SHIFTS)
    install_fake_upstream(monkeypatch, routes)
    h = paid_tenant["headers"]
    assert (await client.post("/api/v1/import/7shifts", json={"access_token": "f"}, headers=h)).status_code == 200
    import httpx
    routes[f"{V2}/company/100/roles"] = httpx.Response(500, json={"error": "down"})
    resp = await client.post("/api/v1/import/7shifts", json={"access_token": "f"}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["roles"]["archived"] == 0
    assert resp.json()["errors"]
    with tenant_scope_disabled():
        archived = (await db_session.execute(select(Role).where(Role.archived_at.is_not(None))
                    .execution_options(populate_existing=True))).scalars().all()
    assert archived == []


async def test_deputy_failed_employee_fetch_archives_no_employee(client, db_session, paid_tenant, monkeypatch):
    routes = copy.deepcopy(DEPUTY)
    install_fake_upstream(monkeypatch, routes)
    h = paid_tenant["headers"]
    req = {"access_token": "f", "deputy_base_url": "https://fake.deputy.com"}
    assert (await client.post("/api/v1/import/deputy", json=req, headers=h)).status_code == 200
    import httpx
    routes["/api/v1/supervise/employee"] = httpx.Response(500, json={"error": "down"})
    resp = await client.post("/api/v1/import/deputy", json=req, headers=h)
    assert resp.status_code == 200
    assert resp.json()["employees"]["archived"] == 0
    with tenant_scope_disabled():
        archived = (await db_session.execute(select(Employee).where(Employee.archived_at.is_not(None))
                    .execution_options(populate_existing=True))).scalars().all()
    assert archived == []
```

Run: `pytest tests/test_import_archive_simulation_integration.py -v` → Expected: the two scenario tests FAIL (the real failure from Task 1), the failed-fetch tests FAIL.

- [ ] **Step 2: Implement** per the rules above. Sketch for the 7shifts employee removal block:

```python
    for ext_id, emp in existing_employees.items():
        if emp.id in seen_wiz_emp_ids or ext_id in seen_emp_ext_ids:
            continue
        if emp.archived_at is None:
            await archive_employee(db, wiz_company_id, emp)
            result.employees["archived"] += 1
```

and for a match:

```python
        emp = existing_employees.get(ext_id)
        if emp is not None and emp.erased_at is not None:
            emp = None  # erased for good: the person comes back as a new row
        if emp is not None and emp.archived_at is not None:
            await restore_employee(db, wiz_company_id, emp)
            result.employees["restored"] += 1
        elif emp is not None:
            result.employees["updated"] += 1
```

(Adapt names to the file. The erased row keeps its `external_id` cleared by GDPR — Task 8 — so in practice it no longer matches; the guard is belt-and-braces.)

- [ ] **Step 3: Run**

Run: `pytest tests/test_import_archive_simulation_integration.py tests/test_schema_7shifts_compat.py tests/test_schema_deputy_compat.py tests/test_plan_enforcement_integration.py tests/test_integration_import_quota_integration.py -v`
Expected: PASS (no xfail left in the simulation file).

- [ ] **Step 4: Commit**

```bash
git add backend/routers/import_7shifts.py backend/routers/import_deputy.py tests/test_import_archive_simulation_integration.py
git commit -m "fix(import): re-imports archive and restore instead of deleting (#126)"
```

---

### Task 8: GDPR erasure keeps pay records

**Files:**
- Modify: `backend/services/archive.py` (add `erase_employee`), `backend/routers/gdpr.py` (`delete_account` 209-276)
- Test: `tests/test_gdpr_archive_integration.py`

**Interfaces:**
- Produces: `async def erase_employee(db, company_id: str, employee: Employee, *, now=None) -> None` — runs `archive_employee`, sets `erased_at`, clears `email, user_id, external_id, timezone, pay_rate, hire_date, seniority_rank, location_ids` (keeps `full_name`), and deletes consents-unrelated personal rows: `EmployeeAvailability`, `EmployeeDayBlackout`, `EmployeeRole`, `EmployeeRoleMinutes`, `EmployeeAffinity` (both directions), `EmployeeCompany`, `EmployeeInvite`, the CASCADE preference/cap tables (explicit delete not needed), and `EmployeeCheckIn` rows (first nulling `TimeEntry.check_in_id` for them).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gdpr_archive_integration.py
"""GDPR erasure: personal data goes, pay records stay (#126)."""

import pytest

pytestmark = pytest.mark.integration


async def test_employee_erasure_keeps_shifts_and_pay(client, db_session, employee_token,
                                                     seed_employee_user, seed_employees, seed_location, seed_roles):
    # Setup: for the employee linked to seed_employee_user, a past approved shift with a check-in
    # and a TimeEntry (reuse Task 1's _history shape inline), plus a future approved shift.
    # DELETE /api/v1/gdpr/delete-account with employee_token → 200.
    # Assert: the Employee row still exists; archived_at and erased_at set; email, user_id,
    #   external_id, pay_rate are None; full_name unchanged.
    # Assert: past shift unchanged; TimeEntry exists with check_in_id None; EmployeeCheckIn gone;
    #   future shift employee_id None; User row gone.
    # Assert: POST /employees/{id}/restore as manager → 409 employee_erased.
    ...


async def test_manager_erasure_nulls_attribution(client, db_session, manager_token, seed_manager,
                                                 seed_employees, seed_location, seed_roles):
    # Setup: a TimeEntry with approved_by_user_id and attested_by_user_id = seed_manager.id; a
    # PayrollExport with exported_by_user_id = seed_manager.id; a ScheduleLock, PasswordResetToken
    # and EmailVerificationToken for the manager if the models allow cheap construction.
    # DELETE /api/v1/gdpr/delete-account with manager_token → 200.
    # Assert: User gone; TimeEntry and PayrollExport rows remain with those columns None.
    ...
```

Fill the `...` bodies; every listed assertion must be written (these comments are instructions, not code to commit).

Run: `pytest tests/test_gdpr_archive_integration.py -v` → Expected: FAIL.

- [ ] **Step 2: Implement** `erase_employee` in `services/archive.py` and replace the employee branch of `delete_account` with a call to it; keep `delete(UserConsent)` and `delete(User)`. The FK rules from Task 2 handle manager attribution.

- [ ] **Step 3: Run**

Run: `pytest tests/test_gdpr_archive_integration.py tests/ -q -k gdpr` → Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/services/archive.py backend/routers/gdpr.py tests/test_gdpr_archive_integration.py
git commit -m "fix(gdpr): erase the employee, keep shifts and pay records (#126)"
```

---

### Task 9: Frontend — Archive, Show archived, Restore, import counts

**Files:**
- Create: `frontend/src/utils/archived.ts`, `frontend/src/utils/archived.test.ts`
- Modify: `frontend/src/types/index.ts` (Location 94-112, Role 116-122, Employee 140-154), `api/employees.ts` (18, 63), `api/locations.ts` (5, 45), `api/roles.ts` (4, 27), `api/import7shifts.ts` (3-11), `api/importDeputy.ts` (4-10), `components/shared/DataTable.tsx` (17-30, 199-216), `components/shared/Import7ShiftsModal.tsx` (18-32, 135-150), `components/shared/ImportDeputyModal.tsx` (18-34, 157-167), `pages/manager/Employees.tsx` (172-180, 421-457, 668-675), `Locations.tsx` (122-130, 188-245), `Roles.tsx` (47-54, 76-110), all 19 `i18n/*.ts`

**Interfaces:**
- Consumes: Task 4 endpoints (`?include_archived=true`, `POST …/restore`, `archived_at` in responses); Task 7 result keys.
- Produces:
  ```ts
  export function visibleRows<T extends { archived_at?: string | null }>(rows: T[], showArchived: boolean): T[]
  export function isArchived(row: { archived_at?: string | null }): boolean
  ```
  `DataTable` gains optional props `isArchived?: (rowIndex: number) => boolean`, `onRestore?: (rowIndex: number) => void`, `deleteLabel?: string`. An archived row shows a muted "Archived" badge and a Restore button instead of Edit/Archive.

- [ ] **Step 1: Failing util test**

```ts
// frontend/src/utils/archived.test.ts
import { describe, it, expect } from "vitest";
import { isArchived, visibleRows } from "./archived";

const rows = [
  { id: "a", archived_at: null },
  { id: "b", archived_at: "2026-10-01T00:00:00Z" },
  { id: "c" },
];

describe("archived rows", () => {
  it("hides archived rows unless asked", () => {
    expect(visibleRows(rows, false).map((r) => r.id)).toEqual(["a", "c"]);
    expect(visibleRows(rows, true).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });
  it("treats a missing archived_at as active", () => {
    expect(isArchived(rows[2])).toBe(false);
    expect(isArchived(rows[1])).toBe(true);
  });
});
```

Run: `cd frontend && npm test -- archived` → Expected: FAIL (module missing).

- [ ] **Step 2: Implement the util**

```ts
// frontend/src/utils/archived.ts
// Archived employees, locations and roles (#126) stay in the database for
// history; the manager pages hide them unless "Show archived" is on.
export function isArchived(row: { archived_at?: string | null }): boolean {
  return row.archived_at != null;
}

export function visibleRows<T extends { archived_at?: string | null }>(rows: T[], showArchived: boolean): T[] {
  return showArchived ? rows : rows.filter((r) => !isArchived(r));
}
```

- [ ] **Step 3: Wire the pages**
  - Types: add `archived_at?: string | null` to Employee, Location, Role.
  - API: `listEmployees(includeArchived = false)` etc. append `?include_archived=true` when set; add `restoreEmployee(id)`, `restoreLocation(id)`, `restoreRole(id)` (`POST /…/{id}/restore`). Keep the `delete*` functions (the endpoint now archives).
  - Each page: `const [showArchived, setShowArchived] = useState(false)`; fetch with `includeArchived: true` always and filter with `visibleRows` (one request; the toggle is instant). Toggle markup copied from `Payroll.tsx:532-539`, placed in the page header beside the existing controls, label `t.common.showArchived`. Delete becomes "Archive" (`t.common.archive`), guarded by `window.confirm(t.<page>.archiveConfirm)` like `SpecialHours.tsx:96`. Restore calls `restore*`, then refetches; on a 402 show the existing limit message; on 409 `employee_erased` show `t.employeesPage.erasedCannotRestore`.
  - **Index mapping:** `DataTable` passes indexes into the array it was given — pass the *filtered* array and index into that same array in `handleDelete`/`handleRestore`.
  - `DataTable`: when `isArchived?.(i)` is true, render the row text with `text.muted`, a small "Archived" pill (`t.common.archived`), and only a Restore button (`action.edit` style) wrapped in `DemoGuard`; otherwise current behaviour, with the delete button label `deleteLabel ?? t.common.delete`.
  - Import modals: change the `deleted` column to `archived` and add `restored`; types `{ created; updated; archived; restored }`; Deputy `unavailability` keeps its own `{ created; skipped; deleted }` type (split the `SyncRow` union).
  - i18n keys (all 19 files; English text below, translate the others):
    - `common.archive`: "Archive"; `common.restore`: "Restore"; `common.archived`: "Archived"; `common.showArchived`: "Show archived"
    - `employeesPage.archiveConfirm`: "Archive this employee? Their future shifts become open shifts; past schedules and payroll are kept."
    - `employeesPage.erasedCannotRestore`: "This employee's data was erased and cannot be restored."
    - `locationsPage.archiveConfirm`: "Archive this location? Its future shifts and drafts are removed; past schedules and payroll are kept."
    - `rolesPage.archiveConfirm`: "Archive this role? Future shifts with this role become open shifts."
    - `import7Shifts.archived`: "Archived"; `import7Shifts.restored`: "Restored"; `importDeputy.archived`: "Archived"; `importDeputy.restored`: "Restored" (remove `import7Shifts.deleted`; keep `importDeputy.deleted` only if the unavailability row still uses it)

- [ ] **Step 4: Run**

Run: `cd frontend && npm test && npm run build`
Expected: all tests pass (including `logicalDirection.test.ts`), build clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): archive, show archived and restore; import shows archived/restored (#126)"
```

---

### Task 10: Docs

**Files:**
- Modify: `CLAUDE.md` (Conventions), `README.md`

- [ ] **Step 1: Add the convention bullet** to `CLAUDE.md` under Conventions, after the open-shift bullet:

```markdown
- **Employees, locations and roles are archived, never deleted** (#126).
  `archived_at` (and `employees.erased_at` for GDPR) marks a row inactive;
  `services/archive.py` owns archiving and restoring, and `active(Model)` is
  the one filter. Only `services/billing.delete_og_and_dependents` hard-deletes
  them. Any new reader must decide explicitly: planning surfaces (scheduler,
  pickers, check-in, employee routes, plan counts) are active-only; history
  (approved schedules, exports, payroll, importers' `external_id` matching)
  includes archived rows. Pay records carry their own employee/location names.
```

- [ ] **Step 2: README** — where it describes deleting employees/locations/roles or the importers, say they are archived and can be restored, and that a re-import restores by external id. Keep it to the sentences that are now wrong.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: archive-not-delete convention (#126)"
```

---

### Task 11: Full verification

- [ ] **Step 1:** `pytest tests/ -q` → Expected: all pass, 0 xfail in `test_import_archive_simulation_integration.py`.
- [ ] **Step 2:** `cd frontend && npm test && npm run build` → Expected: pass, clean build.
- [ ] **Step 3:** `grep -rn "delete(Employee)\|delete(Location)\|delete(Role)\|db.delete(emp\|db.delete(loc\|db.delete(role" backend --include=*.py` → Expected: only `services/billing.py` and `seed.py`.
- [ ] **Step 4:** No commit unless a fix was needed; a fix gets its own commit with a message naming what it fixed.
