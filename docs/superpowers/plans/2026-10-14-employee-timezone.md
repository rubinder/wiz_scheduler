# Employee Timezone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a manager optionally give an employee a timezone, so their
availability and blackouts are converted to each location's zone when
scheduling. Also stop hand-created locations from silently becoming `UTC`.

**Architecture:**
- **Locations:** location zones get validated, and blank zones default to the
  manager's browser zone, chosen from a dropdown.
- **Employees:** a nullable `employees.timezone` column, set only by a manager.
  `NULL` means "no conversion": today's code path, untouched.
- **Conversion module:** a pure module, `backend/scheduling/employee_time.py`,
  converts employee-local availability and blackouts into a location's
  wall-clock.
- **Where it runs:** at one boundary, `nodes.load_location_context`, and only for
  employees with a timezone. Hours already used are subtracted on the employee's
  clock for those employees.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic,
`zoneinfo`, pytest + pytest-asyncio (Postgres via testcontainers); React 18 +
TypeScript, Vite, vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-employee-timezone-design.md`

## Global Constraints

- `employees.timezone` is `NULL` unless a manager sets it. Nothing infers,
  derives or backfills it: imports, employee creation and location changes never
  write it.
- `NULL` employee timezone means **no conversion anywhere**: every code path
  stays byte-for-byte today's.
- Preferences, caps and day preferences are never converted (location-local).
- Never call `.astimezone()` on a stored availability value (#61/#85).
  Conversion happens only between two explicit zones, inside `employee_time.py`.
- Timezone values are IANA names, validated with `zoneinfo.ZoneInfo`. An
  invalid value gets a 422 at the API.
- No new dependencies, Python or npm. The frontend zone list comes from
  `Intl.supportedValuesOf("timeZone")`.
- "Today" is always `datetime.now(timezone.utc).date()`, never `date.today()`
  (enforced by `tests/test_utc_today.py`).
- Tailwind: logical direction utilities only (`ms-`, `text-start`, …),
  enforced by `frontend/src/utils/logicalDirection.test.ts`.
- Every new UI string goes into all 19 locale files in `frontend/src/i18n/`
  (`translationMap` is `Record<Language, Translations>`, so `tsc` fails
  otherwise).
- Migration: `0046`, `down_revision = "0045"`, additive (a nullable column).
- The scheduling graph never raises: unparseable input degrades. Skip the
  value, or keep it unconverted.

## Review Focus

1. **The second location in the same run sees a converted copy, not the
   original.** `load_location_context` writes `available_windows` back onto the
   shared employee dict. Converting in place would make the next location
   convert already-converted times. Pinned in Task 7
   (`test_second_location_gets_its_own_conversion_not_a_double_one`).
2. **An end-of-day window (`…T23:59`) converts as end of day, not one minute
   short.** A 09:00–23:59 New York window must become 06:00–21:00 in Los
   Angeles, not 06:00–20:59. Pinned in Task 6 (`test_end_of_day_23_59_is_treated_as_midnight`).
3. **A blackout that converts to a date outside the scheduled week.** For
   example, Monday 00:30 New York is Sunday 21:30 Los Angeles of the *previous*
   week. It must not land on this week's Sunday, and next Monday's blackout must
   land on this Sunday. Pinned in Task 6
   (`test_blackout_crossing_week_edge_lands_on_the_right_sunday`).
4. **A garbage timezone already stored in the DB** (written before validation
   existed, or edited by hand) must not crash scheduling. It degrades to no
   conversion. Pinned in Task 6 (`test_invalid_zone_means_no_conversion`).
5. **An employee with no timezone at locations in different zones keeps today's
   behaviour.** Availability is read as each location's local time. Pinned in
   Task 7 (`test_no_timezone_at_two_zones_is_unchanged`).

---

### Task 0: Bring the branch up to date

`feat/employee-timezone` was cut from `main` before #167 (Postgres test suite)
merged. Tests in later tasks rely on the Postgres `conftest.py`.

**Files:** none (git only)

- [ ] **Step 1: Merge main**

```bash
cd /Users/robran/IdeaProjects/wiz_employee_tz
git fetch origin
git merge --no-edit origin/main
```

Expected: a clean merge (the branch only adds `docs/`).

- [ ] **Step 2: Confirm the test stack runs**

Run: `backend/.venv/bin/python -m pytest tests/test_avail_tz.py -q`
(if `backend/.venv` is missing: `uv venv backend/.venv && uv pip install --python backend/.venv/bin/python -r backend/requirements.txt -r backend/requirements-dev.txt`)
Expected: all pass.

- [ ] **Step 3: Push**

```bash
git push
```

---

### Task 1: Timezone utilities and the per-date offset fix

**Files:**
- Create: `backend/utils/tz.py`
- Modify: `backend/scheduling/local_scheduler.py` (remove `_tz_offset_for_location` at lines 46-54; use at lines 122, 210-211)
- Modify: `backend/scheduling/prompts.py` (remove `_tz_offset_example` at lines 447-455; use at line 221)
- Test: `tests/test_tz_utils.py`

**Interfaces:**
- Produces: `backend.utils.tz.is_valid_timezone(name: str | None) -> bool`
- Produces: `backend.utils.tz.utc_offset_on(tz_name: str, day: str, hm: str = "12:00") -> str`
  (returns `"+HH:MM"`/`"-HH:MM"`; `"+00:00"` on any bad input)

- [ ] **Step 1: Write the failing tests**

`tests/test_tz_utils.py`:

```python
"""Timezone helpers, and the per-date offset generated shifts carry."""

from backend.scheduling.local_scheduler import local_schedule
from backend.utils.tz import is_valid_timezone, utc_offset_on


def test_valid_and_invalid_zones():
    assert is_valid_timezone("America/New_York")
    assert is_valid_timezone("UTC")
    assert not is_valid_timezone("")
    assert not is_valid_timezone(None)
    assert not is_valid_timezone("Eastern Time")
    assert not is_valid_timezone("../etc/passwd")


def test_offset_follows_the_date_not_a_fixed_one():
    assert utc_offset_on("America/New_York", "2026-01-15") == "-05:00"
    assert utc_offset_on("America/New_York", "2026-07-15") == "-04:00"
    assert utc_offset_on("Asia/Kolkata", "2026-01-15") == "+05:30"


def test_offset_on_bad_input_is_utc():
    assert utc_offset_on("Not/AZone", "2026-01-15") == "+00:00"
    assert utc_offset_on("America/New_York", "not-a-date") == "+00:00"


def _state(week_start: str) -> dict:
    location = {"id": "loc00001", "name": "L", "timezone": "America/New_York"}
    template = {
        "id": "tmpl0001", "name": "T", "location_id": "loc00001",
        "weekly_schedule": {"Monday": [{
            "role_name": "Floor", "role_id": "role0001", "headcount": 1,
            "start_time": "09:00", "end_time": "17:00",
        }]},
    }
    emp = {
        "id": "e001", "full_name": "A", "email": "a@test.com",
        "location_ids": ["loc00001"],
        "roles": [{"role_id": "role0001", "role_name": "Floor", "skill_level": 3}],
        "affinities": [],
        "available_windows": [{
            "start": f"{week_start}T09:00:00+00:00",
            "end": f"{week_start}T17:00:00+00:00",
        }],
    }
    return {
        "company_id": "comp0001", "week_start_date": week_start,
        "locations": [location], "shift_templates": {"loc00001": template},
        "employees": [emp], "availability_draft": {},
        "current_location_index": 0, "completed_location_ids": [],
        "retry_count": 0, "draft_schedules": [], "errors": [],
        "current_prompt": "", "current_raw_response": "",
        "current_parsed_shifts": [], "conflict_notes": "",
        "total_input_tokens": 0, "total_output_tokens": 0,
        "current_location": location, "current_shift_template": template,
        "current_employees": [emp], "failure_entries": [],
        "role_equivalents": {},
    }


def test_a_winter_local_shift_carries_the_winter_offset():
    shifts = local_schedule(_state("2026-01-12"), strategy="random")["current_parsed_shifts"]
    assert shifts[0]["start_time"] == "2026-01-12T09:00:00-05:00"


def test_a_summer_local_shift_carries_the_summer_offset():
    shifts = local_schedule(_state("2026-07-13"), strategy="random")["current_parsed_shifts"]
    assert shifts[0]["start_time"] == "2026-07-13T09:00:00-04:00"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_tz_utils.py -q`
Expected: FAIL. `ModuleNotFoundError: backend.utils.tz`.

- [ ] **Step 3: Implement `backend/utils/tz.py`**

```python
"""IANA timezone helpers shared by locations, employees and the scheduler."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_valid_timezone(name: str | None) -> bool:
    """True if *name* is an IANA zone zoneinfo can load (e.g. America/New_York)."""
    if not name or not isinstance(name, str):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def utc_offset_on(tz_name: str, day: str, hm: str = "12:00") -> str:
    """The zone's UTC offset ("-05:00") at local *hm* on *day* (YYYY-MM-DD).

    Computed for the actual date, so a winter shift gets the winter offset.
    Falls back to "+00:00" on any unparseable input -- the scheduling graph
    degrades, it never raises.
    """
    try:
        dt = datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
        return "+00:00"
    offset = dt.strftime("%z")
    return f"{offset[:3]}:{offset[3:]}"
```

- [ ] **Step 4: Use it in `local_scheduler.py`**

Delete `_tz_offset_for_location` (lines 46-54) and the line
`tz_offset = _tz_offset_for_location(timezone_str)` in `local_schedule`. Add the
import `from backend.utils.tz import utc_offset_on` at the top. Replace the two
ISO lines in the slot loop:

```python
            start_iso = f"{slot_date}T{start_hm}:00{utc_offset_on(timezone_str, slot_date, start_hm)}"
            end_iso = f"{slot_date}T{end_hm}:00{utc_offset_on(timezone_str, slot_date, end_hm)}"
```

Then run `grep -n "tz_offset" backend/scheduling/local_scheduler.py`.
Expected: no matches.

- [ ] **Step 5: Use it in `prompts.py`**

Delete `_tz_offset_example` (lines 447-455). Add
`from backend.utils.tz import utc_offset_on`. In `build_schedule_prompt`,
replace `tz_offset = _tz_offset_example(location["timezone"])` with:

```python
    tz_offset = utc_offset_on(location["timezone"], date_map.get("Monday", week_start_date))
```

- [ ] **Step 6: Run the new tests and the scheduler suites**

Run: `backend/.venv/bin/python -m pytest tests/test_tz_utils.py tests/test_local_scheduler_integration.py tests/test_avail_tz.py tests/test_avail_local_wallclock.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/utils/tz.py backend/scheduling/local_scheduler.py backend/scheduling/prompts.py tests/test_tz_utils.py
git commit -m "fix: compute shift UTC offsets for the actual date, add tz helpers"
```

---

### Task 2: Location timezone validation and a bulk-upload default

**Files:**
- Modify: `backend/schemas/location.py`
- Modify: `backend/routers/locations.py` (bulk upload, lines ~118-245)
- Test: `tests/test_location_timezone_integration.py`

**Interfaces:**
- Consumes: `is_valid_timezone` (Task 1)
- Produces: `POST /api/v1/locations/bulk-upload` accepts optional multipart
  form field `default_timezone`

- [ ] **Step 1: Write the failing tests**

`tests/test_location_timezone_integration.py`:

```python
"""Location timezones are real IANA zones; blank bulk rows use the caller's."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from tests.conftest import REGION_ID

BASE = "/api/v1/locations"


async def test_create_rejects_a_non_iana_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"region_id": REGION_ID, "name": "X", "timezone": "Eastern Time"},
    )
    assert resp.status_code == 422


async def test_create_accepts_an_iana_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"region_id": REGION_ID, "name": "X", "timezone": "America/Chicago"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone"] == "America/Chicago"


async def test_update_rejects_a_non_iana_zone(
    client: AsyncClient, manager_token: str, seed_location,
):
    resp = await client.put(
        f"{BASE}/{seed_location.id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"timezone": "Mars/Olympus"},
    )
    assert resp.status_code == 422


async def _bulk(client, token, csv_text, data=None):
    return await client.post(
        f"{BASE}/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("locations.csv", csv_text, "text/csv")},
        data=data or {},
    )


async def test_bulk_blank_rows_use_the_default_timezone(
    client: AsyncClient, manager_token: str, seed_region, db_session: AsyncSession,
):
    region_name = seed_region.name
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nBlank Store,{region_name},,\n",
        data={"default_timezone": "America/Denver"},
    )
    assert resp.status_code == 200, resp.text
    loc = (await db_session.execute(
        select(Location).where(Location.name == "Blank Store")
    )).scalar_one()
    assert loc.timezone == "America/Denver"


async def test_bulk_blank_rows_fall_back_to_utc_without_a_default(
    client: AsyncClient, manager_token: str, seed_region, db_session: AsyncSession,
):
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nNo Default,{seed_region.name},,\n",
    )
    assert resp.status_code == 200, resp.text
    loc = (await db_session.execute(
        select(Location).where(Location.name == "No Default")
    )).scalar_one()
    assert loc.timezone == "UTC"


async def test_bulk_skips_a_row_with_an_invalid_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nBad Zone,{seed_region.name},,Eastern Time\n",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 0
    assert any("timezone" in e for e in body["errors"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_location_timezone_integration.py -q`
Expected:
- The 422 tests and `test_bulk_skips_a_row_with_an_invalid_zone` FAIL: invalid zones are accepted today.
- `test_bulk_blank_rows_use_the_default_timezone` FAILS: it gets `UTC`.

- [ ] **Step 3: Validate in `backend/schemas/location.py`**

Change the pydantic import to
`from pydantic import BaseModel, Field, field_validator` and add
`from backend.utils.tz import is_valid_timezone`. Add a module-level helper and
validators:

```python
def _check_timezone(value: str | None) -> str | None:
    if value is not None and not is_valid_timezone(value):
        raise ValueError(
            f"'{value}' is not a valid IANA timezone (for example America/New_York)"
        )
    return value
```

Inside `LocationCreate` (after `signal_config`):

```python
    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        return _check_timezone(v)
```

Inside `LocationUpdate` (after `signal_config`):

```python
    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str | None) -> str | None:
        return _check_timezone(v)
```

- [ ] **Step 4: Bulk upload default and per-row validation in `backend/routers/locations.py`**

Imports: `from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status`
and `from backend.utils.tz import is_valid_timezone`.

Signature:

```python
@router.post("/bulk-upload", response_model=LocationBulkUploadResponse)
async def bulk_upload_locations(
    file: UploadFile,
    default_timezone: str | None = Form(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationBulkUploadResponse:
```

Add to the docstring: `Blank timezone cells use the multipart field default_timezone
(the caller's browser zone), else UTC.`

Before the row loop:

```python
    fallback_tz = (default_timezone or "").strip()
    if not is_valid_timezone(fallback_tz):
        fallback_tz = "UTC"
```

Replace `timezone = (row.get("timezone") or "").strip() or "UTC"` with:

```python
        row_tz = (row.get("timezone") or "").strip() or fallback_tz
```

After the `region is None` check, before building the `Location`:

```python
        if not is_valid_timezone(row_tz):
            errors.append(
                f"Row {row_num}: timezone '{row_tz}' is not a valid IANA timezone, skipped"
            )
            skipped += 1
            continue
```

In `Location(...)`, change `timezone=timezone,` to `timezone=row_tz,`.

- [ ] **Step 5: Run the tests**

Run: `backend/.venv/bin/python -m pytest tests/test_location_timezone_integration.py -q`
Expected: PASS. Then `backend/.venv/bin/python -m pytest $(grep -l "locations" tests/*integration*.py) -q`. Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/schemas/location.py backend/routers/locations.py tests/test_location_timezone_integration.py
git commit -m "feat: validate location timezones; bulk upload uses the caller's default zone"
```

---

### Task 3: Deputy location timezone

**Files:**
- Modify: `backend/routers/import_deputy.py` (request model line 47; location loop lines ~178-205)
- Modify: `frontend/src/api/importDeputy.ts` (`importFromDeputy` body)
- Test: `tests/test_deputy_location_timezone.py`

**Interfaces:**
- Consumes: `is_valid_timezone` (Task 1)
- Produces: `import_deputy._deputy_location_timezone(deputy_location: dict, default_timezone: str | None) -> str`
- Produces: `DeputyImportRequest.default_timezone: str | None`

- [ ] **Step 1: Write the failing test**

`tests/test_deputy_location_timezone.py`:

```python
"""Deputy locations get Deputy's zone, else the importer's browser zone."""

from backend.routers.import_deputy import _deputy_location_timezone


def test_uses_a_zone_carried_by_the_payload():
    assert _deputy_location_timezone({"Timezone": "America/Chicago"}, "America/Denver") == "America/Chicago"
    assert _deputy_location_timezone(
        {"_DPMetaData": {"Timezone": "Australia/Sydney"}}, None,
    ) == "Australia/Sydney"


def test_falls_back_to_the_callers_zone():
    assert _deputy_location_timezone({}, "America/Denver") == "America/Denver"


def test_ignores_invalid_values():
    assert _deputy_location_timezone({"Timezone": "AEST"}, "Not/AZone") == "America/New_York"


def test_last_resort_keeps_the_previous_default():
    assert _deputy_location_timezone({}, None) == "America/New_York"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_deputy_location_timezone.py -q`
Expected: FAIL. `ImportError: cannot import name '_deputy_location_timezone'`.

- [ ] **Step 3: Implement**

In `backend/routers/import_deputy.py` add
`from backend.utils.tz import is_valid_timezone`, and add to `DeputyImportRequest`:

```python
    # The importing manager's browser zone, used when Deputy's location
    # payload carries no usable timezone.
    default_timezone: str | None = None
```

Add, above the import route:

```python
def _deputy_location_timezone(
    deputy_location: dict, default_timezone: str | None,
) -> str:
    """Deputy's zone for a location if its payload carries one, else the
    importing manager's browser zone, else the historical America/New_York."""
    meta = deputy_location.get("_DPMetaData") or {}
    address = meta.get("AddressObject") or {}
    for candidate in (
        deputy_location.get("Timezone"),
        deputy_location.get("TimeZone"),
        meta.get("Timezone"),
        address.get("Timezone"),
    ):
        if isinstance(candidate, str) and is_valid_timezone(candidate):
            return candidate
    if default_timezone and is_valid_timezone(default_timezone):
        return default_timezone
    return "America/New_York"
```

In the location loop, replace

```python
            # Deputy doesn't expose timezone on locations; default to America/New_York
            timezone = "America/New_York"
```

with

```python
            # Only used for NEW locations below: an existing location keeps
            # whatever zone a manager may have corrected it to.
            timezone = _deputy_location_timezone(dl, body.default_timezone)
```

(The `existing_locs` branch does not set `timezone`; leave it so.)

- [ ] **Step 4: Send the browser zone from the frontend**

`frontend/src/api/importDeputy.ts`: add
`import { browserTimeZone } from "../utils/timezones";` and in
`importFromDeputy`'s body:

```ts
    body: JSON.stringify({
      access_token: accessToken,
      deputy_base_url: deputyBaseUrl,
      default_timezone: browserTimeZone(),
    }),
```

(`utils/timezones.ts` is created in Task 4. If you run Task 3 first, create the file
with Task 4 Step 3's content now.)

- [ ] **Step 5: Confirm against a real payload**

If a Deputy token is available, fetch one location
(`GET {deputy_base_url}/api/v1/resource/Company`) and note which key, if any,
carries an IANA zone. If it's a key not in the candidate list, add it to
`_deputy_location_timezone` with a test. Record the finding in the commit
message. If no token is available, say so in the PR description.

- [ ] **Step 6: Run and commit**

Run: `backend/.venv/bin/python -m pytest tests/test_deputy_location_timezone.py tests/test_schema_deputy_compat.py -q`
Expected: PASS.

```bash
git add backend/routers/import_deputy.py frontend/src/api/importDeputy.ts tests/test_deputy_location_timezone.py
git commit -m "feat: Deputy locations use Deputy's zone or the importer's browser zone"
```

---

### Task 4: Location timezone dropdown, browser default, UTC hint (frontend)

**Files:**
- Create: `frontend/src/utils/timezones.ts`
- Create: `frontend/src/utils/timezones.test.ts`
- Modify: `frontend/src/pages/manager/Locations.tsx` (column at line 60; `handleCreate` line ~171; bulk upload call)
- Modify: `frontend/src/api/locations.ts` (`bulkUploadLocations`, lines 49-58)
- Modify: all 19 `frontend/src/i18n/*.ts` (key `locationsPage.utcHint`), via the script in Task 8 Step 1, or add by hand now

**Interfaces:**
- Produces: `browserTimeZone(): string`
- Produces: `timeZoneOptions(current?: string | null): { value: string; label: string }[]`
- Produces: `availabilityZoneNote(employeeTz: string | null | undefined, locationZones: string[]): string | null` (used in Task 9)

- [ ] **Step 1: Write the failing tests**

`frontend/src/utils/timezones.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { availabilityZoneNote, browserTimeZone, timeZoneOptions } from "./timezones";

describe("browserTimeZone", () => {
  it("reads the runtime zone (the test script pins TZ=Asia/Tokyo)", () => {
    expect(browserTimeZone()).toBe("Asia/Tokyo");
  });
});

describe("timeZoneOptions", () => {
  it("puts the browser zone first and includes UTC", () => {
    const opts = timeZoneOptions();
    expect(opts[0].value).toBe("Asia/Tokyo");
    expect(opts.some((o) => o.value === "UTC")).toBe(true);
  });

  it("keeps a current value even if the runtime list lacks it", () => {
    expect(timeZoneOptions("Etc/GMT+5").some((o) => o.value === "Etc/GMT+5")).toBe(true);
  });

  it("has no duplicates", () => {
    const values = timeZoneOptions("Asia/Tokyo").map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
  });

  it("labels with spaces instead of underscores", () => {
    expect(timeZoneOptions("America/New_York").find((o) => o.value === "America/New_York")?.label)
      .toBe("America/New York");
  });
});

describe("availabilityZoneNote", () => {
  it("is null for an employee with no timezone", () => {
    expect(availabilityZoneNote(null, ["America/New_York", "America/Los_Angeles"])).toBeNull();
  });
  it("is null when every location shares the employee's zone", () => {
    expect(availabilityZoneNote("America/New_York", ["America/New_York"])).toBeNull();
  });
  it("names the zone when a location differs", () => {
    expect(availabilityZoneNote("America/New_York", ["America/Los_Angeles"])).toBe("America/New_York");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/utils/timezones.test.ts` (from the repo
root `frontend/`; the npm script sets `TZ=Asia/Tokyo`, so run
`TZ=Asia/Tokyo npx vitest run src/utils/timezones.test.ts`)
Expected: FAIL. Cannot resolve `./timezones`.

- [ ] **Step 3: Implement `frontend/src/utils/timezones.ts`**

```ts
/** IANA timezone helpers. The zone list comes from the browser, so there is
 *  no bundled database and no new dependency. */

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function timeZoneOptions(current?: string | null): { value: string; label: string }[] {
  let zones: string[] = [];
  try {
    const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    zones = intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    zones = [];
  }
  const browser = browserTimeZone();
  const all = new Set<string>([...zones, "UTC", browser]);
  if (current) all.add(current);
  const ordered = [browser, ...[...all].filter((z) => z !== browser).sort()];
  return ordered.map((z) => ({ value: z, label: z.replace(/_/g, " ") }));
}

/** The zone to mention on an availability page, or null to say nothing.
 *  Only an employee with an explicit timezone that differs from one of their
 *  locations' zones gets a note -- nobody, by default. */
export function availabilityZoneNote(
  employeeTz: string | null | undefined,
  locationZones: string[],
): string | null {
  if (!employeeTz) return null;
  return locationZones.some((z) => z !== employeeTz) ? employeeTz : null;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && TZ=Asia/Tokyo npx vitest run src/utils/timezones.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the Locations page**

In `frontend/src/pages/manager/Locations.tsx`:
- import `{ browserTimeZone, timeZoneOptions } from "../../utils/timezones"`
- replace the timezone column with
  `{ key: "timezone", label: t.locationsPage.timezone, type: "select", options: timeZoneOptions() },`.
  If the existing column uses a literal `"Timezone"` label, keep the literal so you
  don't change unrelated copy.
- in `handleCreate`, replace `timezone: (row.timezone as string) || "UTC",`
  with `timezone: (row.timezone as string) || browserTimeZone(),`

Because `timeZoneOptions()` returns a stable list, compute it once inside the
existing `useMemo` that builds `columns`.

Above the `DataTable` render, show the UTC hint only when such locations exist:

```tsx
{(() => {
  const utcNames = locations.filter((l) => l.timezone === "UTC").map((l) => l.name);
  return utcNames.length > 0 ? (
    <p className={`mb-3 text-sm ${text.muted}`}>
      {t.locationsPage.utcHint.replace("{names}", utcNames.join(", "))}
    </p>
  ) : null;
})()}
```

(Use whatever the file already names its locations state and `text` style
import; both exist on this page.)

- [ ] **Step 6: Send the default zone with bulk uploads**

In `frontend/src/api/locations.ts` `bulkUploadLocations`, after
`formData.append("file", file);` add
`formData.append("default_timezone", browserTimeZone());`, importing
`browserTimeZone` from `../utils/timezones`.

- [ ] **Step 7: Add the i18n key**

Add `utcHint` inside `locationsPage` in all 19 locale files, using the values in
Task 8's table (key `locationsPage.utcHint`). Run Task 8 Step 1's script with
only that key, or add it by hand.

- [ ] **Step 8: Type-check, test, commit**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: no type errors, all tests pass (including `logicalDirection.test.ts`).

```bash
git add frontend/src/utils/timezones.ts frontend/src/utils/timezones.test.ts frontend/src/pages/manager/Locations.tsx frontend/src/api/locations.ts frontend/src/i18n
git commit -m "feat: location timezone dropdown defaulting to the browser zone, UTC hint"
```

---

### Task 5: `employees.timezone` column and API

**Files:**
- Create: `backend/alembic/versions/0046_add_employee_timezone.py`
- Modify: `backend/models/employee.py` (`Employee`, after `location_ids` ~line 27)
- Modify: `backend/schemas/employee.py` (`EmployeeCreate`, `EmployeeUpdate`, `EmployeeResponse`, `EmployeeMeLocationResponse`, `EmployeeMeResponse`)
- Modify: `backend/routers/employees.py` (`create_employee` ~line 176, `update_employee` ~line 240, `get_my_employee_profile` lines 98-150)
- Test: `tests/test_employee_timezone_integration.py`

**Interfaces:**
- Consumes: `is_valid_timezone` (Task 1)
- Produces: `Employee.timezone: Mapped[str | None]`
- Produces: API field `timezone: str | None` on employee create/update/response.
  `/employees/me` gains `timezone` and `locations[].timezone`.

- [ ] **Step 1: Write the failing tests**

`tests/test_employee_timezone_integration.py`:

```python
"""employees.timezone: NULL by default, set only by a manager, validated."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee
from tests.conftest import EMPLOYEE1_ID, LOCATION_ID

BASE = "/api/v1/employees"


async def test_a_new_employee_has_no_timezone(
    client: AsyncClient, manager_token: str, seed_location,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"full_name": "New Person", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone"] is None


async def test_a_manager_sets_and_clears_it(
    client: AsyncClient, manager_token: str, seed_employees, db_session: AsyncSession,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"timezone": "America/Los_Angeles"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "America/Los_Angeles"

    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"timezone": None})
    assert resp.status_code == 200
    db_session.expire_all()
    emp = (await db_session.execute(
        select(Employee).where(Employee.id == EMPLOYEE1_ID)
    )).scalar_one()
    assert emp.timezone is None


async def test_an_update_without_the_field_leaves_it_alone(
    client: AsyncClient, manager_token: str, seed_employees,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                     json={"timezone": "America/Chicago"})
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"full_name": "Renamed"})
    assert resp.json()["timezone"] == "America/Chicago"


async def test_changing_locations_never_touches_it(
    client: AsyncClient, manager_token: str, seed_employees,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"location_ids": [LOCATION_ID]})
    assert resp.json()["timezone"] is None


async def test_an_invalid_zone_is_rejected(
    client: AsyncClient, manager_token: str, seed_employees,
):
    resp = await client.put(
        f"{BASE}/{EMPLOYEE1_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"timezone": "Pacific Time"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_integration.py -q`
Expected: FAIL. `KeyError: 'timezone'`, and the 422 test returns 200.

- [ ] **Step 3: Model column**

In `backend/models/employee.py`, inside `class Employee`, after `location_ids`:

```python
    # IANA zone the employee's availability and blackouts are written in.
    # NULL (the default) = no zone: they are each location's local time and
    # nothing is converted. Set only by a manager; nothing infers it.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 4: Migration**

`backend/alembic/versions/0046_add_employee_timezone.py`:

```python
"""Add employees.timezone (optional; NULL = availability is location-local).

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "timezone")
```

- [ ] **Step 5: Schemas**

In `backend/schemas/employee.py` add
`from backend.utils.tz import is_valid_timezone` and a helper:

```python
def _check_employee_timezone(value: str | None) -> str | None:
    if value is not None and not is_valid_timezone(value):
        raise ValueError(
            f"'{value}' is not a valid IANA timezone (for example America/New_York)"
        )
    return value
```

Add to `EmployeeCreate` and `EmployeeUpdate` (after `seniority_rank`):

```python
    # Optional; NULL = no zone (availability is each location's local time).
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str | None) -> str | None:
        return _check_employee_timezone(v)
```

Add `timezone: str | None = None` to `EmployeeResponse` (after
`seniority_rank`), to `EmployeeMeLocationResponse` (after `location_name`) and
to `EmployeeMeResponse` (after `email`).

- [ ] **Step 6: Router**

`create_employee`: add `timezone=body.timezone,` to the `Employee(...)`
constructor.

`update_employee`: after the `seniority_rank` block:

```python
    # Clearable: {"timezone": null} returns the employee to location-local
    # availability. Absent = unchanged.
    if "timezone" in body.model_fields_set:
        employee.timezone = body.timezone
```

`get_my_employee_profile`: build locations with their zone, and return the
employee's:

```python
        locations = [
            EmployeeMeLocationResponse(
                location_id=loc.id, location_name=loc.name, timezone=loc.timezone,
            )
            for loc in loc_result.scalars().all()
        ]
```

```python
    return EmployeeMeResponse(
        id=employee.id,
        full_name=employee.full_name,
        email=employee.email,
        timezone=employee.timezone,
        roles=roles,
        locations=locations,
    )
```

- [ ] **Step 7: Run tests (API + migration chain)**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_integration.py tests/test_migrations.py tests/test_schema_7shifts_compat.py tests/test_schema_deputy_compat.py -q`
Expected: PASS. (The schema compat tests must pass for schema PRs.)

- [ ] **Step 8: Commit**

```bash
git add backend/models/employee.py backend/alembic/versions/0046_add_employee_timezone.py backend/schemas/employee.py backend/routers/employees.py tests/test_employee_timezone_integration.py
git commit -m "feat: optional employees.timezone, set only by a manager"
```

---

### Task 6: `employee_time` conversion module

**Files:**
- Create: `backend/scheduling/employee_time.py`
- Test: `tests/test_employee_time.py`

**Interfaces:**
- Produces: `needs_conversion(emp_tz: str | None, loc_tz: str | None) -> bool`
- Produces: `availability_to_location(windows: list[dict[str, str]], emp_tz: str | None, loc_tz: str | None) -> list[dict[str, str]]`.
  Windows are `{"start": iso, "end": iso}` and keep each window's own tag.
- Produces: `blackouts_to_location(blackouts: list[dict[str, str]], week_start_date: str, num_days: int, emp_tz: str | None, loc_tz: str | None) -> list[dict[str, str]]`.
  Blackouts are `{"day": "Monday", "start": "HH:MM", "end": "HH:MM"}`.
- Produces: `instant_to_employee_face(dt: datetime, emp_tz: str) -> datetime`.
  Naive in, unchanged out; aware → naive face in `emp_tz`. Raises `ZoneInfoNotFoundError`/`ValueError` on a bad zone.

- [ ] **Step 1: Write the failing tests**

`tests/test_employee_time.py`:

```python
"""Employee-zone -> location-zone conversion for availability and blackouts."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.scheduling.employee_time import (
    availability_to_location,
    blackouts_to_location,
    instant_to_employee_face,
    needs_conversion,
)

NY, LA = "America/New_York", "America/Los_Angeles"


def w(start: str, end: str) -> dict:
    return {"start": f"{start}+00:00", "end": f"{end}+00:00"}


# -- no conversion ---------------------------------------------------------

def test_none_or_same_zone_returns_the_very_same_list():
    windows = [w("2026-03-02T09:00:00", "2026-03-02T17:00:00")]
    assert availability_to_location(windows, None, LA) is windows
    assert availability_to_location(windows, NY, NY) is windows
    assert not needs_conversion(None, LA)
    assert not needs_conversion(NY, NY)


def test_invalid_zone_means_no_conversion():
    windows = [w("2026-03-02T09:00:00", "2026-03-02T17:00:00")]
    assert availability_to_location(windows, "Not/AZone", LA) is windows
    assert availability_to_location(windows, NY, "garbage") is windows


# -- availability ----------------------------------------------------------

def test_ny_window_seen_from_la():
    out = availability_to_location([w("2026-03-02T09:00:00", "2026-03-02T17:00:00")], NY, LA)
    assert out == [w("2026-03-02T06:00:00", "2026-03-02T14:00:00")]


def test_window_split_at_midnight_going_east():
    # 20:00-23:00 LA is 23:00-02:00 NY: two pieces, the first ending at 23:59.
    out = availability_to_location([w("2026-03-02T20:00:00", "2026-03-02T23:00:00")], LA, NY)
    assert out == [
        w("2026-03-02T23:00:00", "2026-03-02T23:59:00"),
        w("2026-03-03T00:00:00", "2026-03-03T02:00:00"),
    ]


def test_window_split_at_midnight_going_west():
    # 00:00-04:00 NY is 21:00-01:00 LA (previous evening into the day).
    out = availability_to_location([w("2026-03-03T00:00:00", "2026-03-03T04:00:00")], NY, LA)
    assert out == [
        w("2026-03-02T21:00:00", "2026-03-02T23:59:00"),
        w("2026-03-03T00:00:00", "2026-03-03T01:00:00"),
    ]


def test_end_of_day_23_59_is_treated_as_midnight():
    # NY "until end of day" is 21:00 in LA -- not 20:59.
    out = availability_to_location([w("2026-03-02T09:00:00", "2026-03-02T23:59:00")], NY, LA)
    assert out == [w("2026-03-02T06:00:00", "2026-03-02T21:00:00")]


def test_unparseable_window_is_kept_as_is():
    bad = {"start": "nonsense", "end": "x"}
    assert availability_to_location([bad], NY, LA) == [bad]


def test_dst_week_uses_each_dates_own_offset():
    # 2026-03-08: US spring-forward. NY and LA switch together, so the gap
    # stays 3h on both sides of the change.
    before = availability_to_location([w("2026-03-07T09:00:00", "2026-03-07T17:00:00")], NY, LA)
    after = availability_to_location([w("2026-03-09T09:00:00", "2026-03-09T17:00:00")], NY, LA)
    assert before == [w("2026-03-07T06:00:00", "2026-03-07T14:00:00")]
    assert after == [w("2026-03-09T06:00:00", "2026-03-09T14:00:00")]


def test_zones_with_different_dst_rules():
    # Europe/London springs forward 2026-03-29; NY already did on 03-08.
    # On 03-16 the gap is 4h, on 03-30 it is back to 5h.
    mid = availability_to_location([w("2026-03-16T12:00:00", "2026-03-16T16:00:00")], "Europe/London", NY)
    late = availability_to_location([w("2026-03-30T12:00:00", "2026-03-30T16:00:00")], "Europe/London", NY)
    assert mid == [w("2026-03-16T08:00:00", "2026-03-16T12:00:00")]
    assert late == [w("2026-03-30T07:00:00", "2026-03-30T11:00:00")]


def test_nonexistent_spring_forward_face_does_not_raise():
    out = availability_to_location([w("2026-03-08T02:30:00", "2026-03-08T05:00:00")], NY, LA)
    assert out and all(p["start"] < p["end"] for p in out)


def test_ambiguous_fall_back_face_does_not_raise():
    out = availability_to_location([w("2026-11-01T01:30:00", "2026-11-01T05:00:00")], NY, LA)
    assert out and all(p["start"] < p["end"] for p in out)


# -- blackouts -------------------------------------------------------------

WEEK = "2026-03-02"  # a Monday


def test_blackouts_none_or_same_zone_unchanged():
    bos = [{"day": "Tuesday", "start": "14:00", "end": "18:00"}]
    assert blackouts_to_location(bos, WEEK, 7, None, LA) is bos
    assert blackouts_to_location(bos, WEEK, 7, NY, NY) is bos


def test_blackout_shifts_hours():
    bos = [{"day": "Tuesday", "start": "14:00", "end": "18:00"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Tuesday", "start": "11:00", "end": "15:00"},
    ]


def test_blackout_moves_to_another_weekday():
    bos = [{"day": "Wednesday", "start": "01:00", "end": "02:00"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Tuesday", "start": "22:00", "end": "23:00"},
    ]


def test_blackout_to_end_of_day_moves_whole_to_the_next_day():
    # LA Tue 22:00-midnight is NY Wed 01:00-03:00.
    bos = [{"day": "Tuesday", "start": "22:00", "end": "23:59"}]
    assert blackouts_to_location(bos, WEEK, 7, LA, NY) == [
        {"day": "Wednesday", "start": "01:00", "end": "03:00"},
    ]


def test_blackout_split_at_midnight():
    # LA Tue 20:00-23:00 is NY Tue 23:00 - Wed 02:00.
    bos = [{"day": "Tuesday", "start": "20:00", "end": "23:00"}]
    assert blackouts_to_location(bos, WEEK, 7, LA, NY) == [
        {"day": "Tuesday", "start": "23:00", "end": "23:59"},
        {"day": "Wednesday", "start": "00:00", "end": "02:00"},
    ]


def test_blackout_crossing_week_edge_lands_on_the_right_sunday():
    # Monday 00:30-01:30 NY is Sunday 21:30-22:30 LA. This week's Monday
    # instance falls on the PREVIOUS Sunday (outside the week) and is dropped;
    # NEXT Monday's instance falls on THIS week's Sunday and is kept.
    bos = [{"day": "Monday", "start": "00:30", "end": "01:30"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == [
        {"day": "Sunday", "start": "21:30", "end": "22:30"},
    ]


def test_blackout_with_bad_times_is_kept():
    bos = [{"day": "Tuesday", "start": "late", "end": "later"}]
    assert blackouts_to_location(bos, WEEK, 7, NY, LA) == bos


# -- instants --------------------------------------------------------------

def test_instant_to_employee_face():
    instant = datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc)  # 09:00 LA
    assert instant_to_employee_face(instant, NY) == datetime(2026, 3, 2, 12, 0)


def test_naive_instant_is_returned_unchanged():
    naive = datetime(2026, 3, 2, 9, 0)
    assert instant_to_employee_face(naive, NY) is naive
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_time.py -q`
Expected: FAIL. `ModuleNotFoundError: backend.scheduling.employee_time`.

- [ ] **Step 3: Implement `backend/scheduling/employee_time.py`**

```python
"""Employee-zone -> location-zone conversion for availability and blackouts.

By default an employee has no timezone and none of this runs: availability is
the local wall clock of whichever location is being scheduled (#61/#85). Only
for an employee whose timezone a manager has set are their availability and
blackouts read as THAT zone's wall clock and converted, once, into the
location's wall clock at the scheduling boundary (nodes.load_location_context).

Everything here works on wall-clock faces between two EXPLICIT zones. It never
calls .astimezone() on a stored value as if its +00:00 tag were real.
Preferences and caps are deliberately never converted: they describe the kind
of shift, which is location-local.
"""

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_END_OF_DAY = time(23, 59)


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
        return None


def needs_conversion(emp_tz: str | None, loc_tz: str | None) -> bool:
    """True only for a valid employee zone that differs from a valid location zone."""
    if not emp_tz or emp_tz == loc_tz:
        return False
    return _zone(emp_tz) is not None and _zone(loc_tz) is not None


def _convert_face(face: datetime, src: ZoneInfo, dst: ZoneInfo) -> datetime:
    # fold=0: a nonexistent (spring-forward) or ambiguous (fall-back) face
    # resolves the way zoneinfo does by default.
    return face.replace(tzinfo=src, fold=0).astimezone(dst).replace(tzinfo=None)


def _as_exclusive_end(start: datetime, end: datetime) -> datetime:
    # The codebase writes "until end of day" as 23:59; convert it as midnight
    # so the converted window keeps its full length.
    if end.time() == _END_OF_DAY:
        return datetime.combine(end.date() + timedelta(days=1), time())
    return end


def _pieces(start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
    """Split [start, end) at each midnight; a piece ending at midnight is
    written as 23:59 of its own day (the codebase's end-of-day convention)."""
    out: List[Tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        next_midnight = datetime.combine(cur.date() + timedelta(days=1), time())
        piece_end = min(end, next_midnight)
        shown_end = (
            datetime.combine(cur.date(), _END_OF_DAY)
            if piece_end == next_midnight else piece_end
        )
        if shown_end > cur:
            out.append((cur, shown_end))
        cur = piece_end
    return out


def availability_to_location(
    windows: List[Dict[str, str]],
    emp_tz: str | None,
    loc_tz: str | None,
) -> List[Dict[str, str]]:
    """Convert employee-local availability windows to the location's wall clock.

    Returns *windows* itself (same object) when no conversion applies. Each
    output window keeps its input's offset tag, so the shape downstream code
    reads is unchanged.
    """
    if not needs_conversion(emp_tz, loc_tz):
        return windows
    src, dst = _zone(emp_tz), _zone(loc_tz)
    out: List[Dict[str, str]] = []
    for window in windows:
        try:
            start_aware = datetime.fromisoformat(window["start"])
            end_aware = datetime.fromisoformat(window["end"])
        except (KeyError, ValueError, TypeError):
            out.append(window)
            continue
        tag = start_aware.tzinfo
        start = start_aware.replace(tzinfo=None)
        end = _as_exclusive_end(start, end_aware.replace(tzinfo=None))
        if end <= start:
            out.append(window)
            continue
        for p_start, p_end in _pieces(_convert_face(start, src, dst), _convert_face(end, src, dst)):
            out.append({
                "start": p_start.replace(tzinfo=tag).isoformat(),
                "end": p_end.replace(tzinfo=tag).isoformat(),
            })
    return out


def blackouts_to_location(
    blackouts: List[Dict[str, str]],
    week_start_date: str,
    num_days: int,
    emp_tz: str | None,
    loc_tz: str | None,
) -> List[Dict[str, str]]:
    """Convert recurring employee-local blackouts for the week being scheduled.

    Each blackout is placed on the real dates of its weekday -- including the
    day before and after the week, since a conversion can carry an instance
    across the week's edge -- converted per date (DST-correct), and kept only
    where it lands inside the week. Output keeps the {"day","start","end"}
    shape; within one scheduled week a weekday names exactly one date.
    """
    if not blackouts or not needs_conversion(emp_tz, loc_tz):
        return blackouts
    src, dst = _zone(emp_tz), _zone(loc_tz)
    try:
        week_start = date.fromisoformat(week_start_date)
    except (ValueError, TypeError):
        return blackouts
    week_dates = {week_start + timedelta(days=i) for i in range(num_days)}
    candidate_dates = [week_start + timedelta(days=i) for i in range(-1, num_days + 1)]

    out: List[Dict[str, str]] = []
    for bo in blackouts:
        try:
            start_t = time.fromisoformat(bo["start"])
            end_t = time.fromisoformat(bo["end"])
        except (KeyError, ValueError, TypeError):
            out.append(bo)
            continue
        for d in candidate_dates:
            if DAY_NAMES[d.weekday()] != bo.get("day"):
                continue
            start = datetime.combine(d, start_t)
            end = _as_exclusive_end(start, datetime.combine(d, end_t))
            if end <= start:
                continue
            for p_start, p_end in _pieces(_convert_face(start, src, dst), _convert_face(end, src, dst)):
                if p_start.date() not in week_dates:
                    continue
                out.append({
                    "day": DAY_NAMES[p_start.weekday()],
                    "start": p_start.strftime("%H:%M"),
                    "end": p_end.strftime("%H:%M"),
                })
    return out


def instant_to_employee_face(dt: datetime, emp_tz: str) -> datetime:
    """A real instant (a shift time) as a naive wall-clock face in *emp_tz*.

    A naive value has no instant to convert and is returned unchanged.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(ZoneInfo(emp_tz)).replace(tzinfo=None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_time.py -q`
Expected: PASS. If a DST expectation fails, re-derive it by hand from the zone
rules before changing code. The expected values above are worked out in their
comments.

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/employee_time.py tests/test_employee_time.py
git commit -m "feat: employee-zone to location-zone conversion for availability and blackouts"
```

---

### Task 7: Wire conversion into the scheduler

**Files:**
- Modify: `backend/scheduling/graph.py` (`_committed_shifts_by_employee` ~line 335, `_load_employee_availability` ~line 417, `_load_initial_state` employee dict ~line 760)
- Modify: `backend/scheduling/nodes.py` (`_filter_availability_against_draft` lines 91-146, `load_location_context` lines 149-220)
- Test: `tests/test_employee_timezone_scheduling.py`, `tests/test_employee_timezone_holds_integration.py`

**Interfaces:**
- Consumes: `needs_conversion`, `availability_to_location`, `blackouts_to_location`, `instant_to_employee_face` (Task 6); `Employee.timezone` (Task 5)
- Produces: employee dicts in `SchedulingState.employees` carry `"timezone": str | None`
- Produces: `_load_employee_availability(db, company_id, week_start_date, num_days, all_employee_ids=None, employee_timezones: dict[str, str | None] | None = None)`

- [ ] **Step 1: Write the failing node-level tests**

`tests/test_employee_timezone_scheduling.py`:

```python
"""Conversion at the scheduling boundary: only for an employee with a timezone."""

from copy import deepcopy

from backend.scheduling.local_scheduler import local_schedule
from backend.scheduling.nodes import (
    _filter_availability_against_draft,
    load_location_context,
)

NY, LA = "America/New_York", "America/Los_Angeles"
MON = "2026-03-02"
LOC_NY = {"id": "locny001", "name": "NY", "timezone": NY}
LOC_LA = {"id": "locla001", "name": "LA", "timezone": LA}
ROLE = {"role_id": "role0001", "role_name": "Floor", "skill_level": 3}


def _template(loc_id: str, start: str, end: str) -> dict:
    return {
        "id": f"t-{loc_id}", "name": "T", "location_id": loc_id,
        "weekly_schedule": {"Monday": [{
            "role_name": "Floor", "role_id": "role0001", "headcount": 1,
            "start_time": start, "end_time": end,
        }]},
    }


def _emp(timezone: str | None, windows: list[dict], blackouts=None) -> dict:
    return {
        "id": "e001", "full_name": "A", "email": "a@test.com",
        "location_ids": ["locny001", "locla001"], "roles": [ROLE],
        "affinities": [], "available_windows": windows,
        "day_blackouts": blackouts or [], "timezone": timezone,
    }


def _state(emp: dict, location_index: int, templates: dict) -> dict:
    locations = [LOC_NY, LOC_LA]
    return {
        "company_id": "comp0001", "week_start_date": MON, "num_days": 7,
        "locations": locations, "shift_templates": templates,
        "employees": [emp], "availability_draft": {},
        "current_location_index": location_index, "completed_location_ids": [],
        "retry_count": 0, "draft_schedules": [], "errors": [],
        "current_prompt": "", "current_raw_response": "",
        "current_parsed_shifts": [], "conflict_notes": "",
        "total_input_tokens": 0, "total_output_tokens": 0,
        "current_location": locations[location_index],
        "current_shift_template": {}, "current_employees": [],
        "failure_entries": [], "role_equivalents": {},
    }


NY_9_17 = {"start": f"{MON}T09:00:00+00:00", "end": f"{MON}T17:00:00+00:00"}


def test_no_timezone_at_two_zones_is_unchanged():
    emp = _emp(None, [NY_9_17])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "09:00", "17:00")}))
    assert ctx["current_employees"][0]["available_windows"] == [NY_9_17]


def test_with_a_timezone_the_location_sees_converted_windows():
    emp = _emp(NY, [NY_9_17])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")}))
    assert ctx["current_employees"][0]["available_windows"] == [
        {"start": f"{MON}T06:00:00+00:00", "end": f"{MON}T14:00:00+00:00"},
    ]


def test_conversion_never_leaks_into_the_shared_employee():
    emp = _emp(NY, [NY_9_17])
    state = _state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")})
    load_location_context(state)
    assert state["employees"][0]["available_windows"] == [NY_9_17]


def test_second_location_gets_its_own_conversion_not_a_double_one():
    emp = _emp(NY, [NY_9_17])
    templates = {
        "locny001": _template("locny001", "09:00", "17:00"),
        "locla001": _template("locla001", "06:00", "14:00"),
    }
    state = _state(emp, 0, templates)
    first = load_location_context(state)
    assert first["current_employees"][0]["available_windows"] == [NY_9_17]
    state = {**state, "current_location_index": 1, "current_location": LOC_LA}
    second = load_location_context(state)
    assert second["current_employees"][0]["available_windows"] == [
        {"start": f"{MON}T06:00:00+00:00", "end": f"{MON}T14:00:00+00:00"},
    ]


def test_blackouts_are_converted_for_the_location():
    emp = _emp(NY, [NY_9_17], blackouts=[{"day": "Monday", "start": "14:00", "end": "18:00"}])
    ctx = load_location_context(_state(emp, 1, {"locla001": _template("locla001", "06:00", "14:00")}))
    assert ctx["current_employees"][0]["day_blackouts"] == [
        {"day": "Monday", "start": "11:00", "end": "15:00"},
    ]


def test_draft_spans_are_subtracted_on_the_employees_clock():
    # Booked 09:00-13:00 at the NY store (real offset -05:00). For an employee
    # on NY time that consumes 09:00-13:00 of their NY-clock availability.
    emp = _emp(NY, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T09:00:00-05:00", "end": f"{MON}T13:00:00-05:00"}]}
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T13:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_draft_spans_from_another_zone_are_converted_before_subtracting():
    # Booked 06:00-10:00 at the LA store (-08:00) = 09:00-13:00 NY time.
    emp = _emp(NY, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T06:00:00-08:00", "end": f"{MON}T10:00:00-08:00"}]}
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T13:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_draft_without_timezone_keeps_face_subtraction():
    emp = _emp(None, [NY_9_17])
    draft = {"e001": [{"start": f"{MON}T06:00:00-08:00", "end": f"{MON}T10:00:00-08:00"}]}
    # Face subtraction (today's behaviour): 06:00-10:00 carved from 09:00-17:00.
    assert _filter_availability_against_draft(emp, draft) == [
        {"start": f"{MON}T10:00:00+00:00", "end": f"{MON}T17:00:00+00:00"},
    ]


def test_local_scheduler_output_identical_for_none_and_same_zone():
    templates = {"locny001": _template("locny001", "09:00", "17:00")}

    def run(tz):
        state = _state(_emp(tz, [NY_9_17]), 0, templates)
        ctx = load_location_context(state)
        state = {**state, **ctx}
        return local_schedule(deepcopy(state), strategy="random")["current_parsed_shifts"]

    assert run(None) == run(NY)
```

- [ ] **Step 2: Run to verify they fail**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_scheduling.py -q`
Expected:
- the conversion, blackout and draft-in-another-zone tests FAIL;
- `test_no_timezone_at_two_zones_is_unchanged`, `test_draft_without_timezone_keeps_face_subtraction` and the "identical output" test already PASS. They pin today's behaviour.

- [ ] **Step 3: Draft filter on the employee's clock (`nodes.py`)**

Add `from backend.scheduling.employee_time import availability_to_location, blackouts_to_location, instant_to_employee_face, needs_conversion`
to `nodes.py`'s imports. In `_filter_availability_against_draft`, replace the
consumed-parsing loop:

```python
    emp_tz = employee.get("timezone")
    consumed: List[Tuple[datetime, datetime]] = []
    for c in consumed_raw:
        try:
            if emp_tz:
                # Draft spans are real instants (generated shifts carry their
                # location's offset); read them on the employee's clock.
                consumed.append((
                    instant_to_employee_face(datetime.fromisoformat(c["start"]), emp_tz),
                    instant_to_employee_face(datetime.fromisoformat(c["end"]), emp_tz),
                ))
            else:
                consumed.append((_wall_clock(c["start"]), _wall_clock(c["end"])))
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
            # A window we cannot parse is skipped rather than raised on: the
            # scheduling graph degrades, it never throws.
            continue
```

Add `from zoneinfo import ZoneInfoNotFoundError` to the imports. In the
docstring, after "Comparison is done on wall-clock faces…", add: "For an employee
with a timezone, both sides are on the employee's clock (see employee_time)."

- [ ] **Step 4: The single conversion point (`load_location_context`)**

Replace the "Log each employee's availability window count" loop with:

```python
    # Availability stays on the employee's clock on the shared employee dict
    # (the draft filter is cumulative and idempotent). For an employee with a
    # timezone, THIS location gets a converted COPY -- converting in place
    # would make the next location convert already-converted times.
    loc_tz = location.get("timezone")
    week_start_date = state["week_start_date"]
    num_days = state.get("num_days", 7)
    converted_employees: List[Dict[str, Any]] = []
    for emp in location_employees:
        windows_before = len(emp.get("available_windows", []))
        emp["available_windows"] = _filter_availability_against_draft(
            emp, state["availability_draft"]
        )
        emp_tz = emp.get("timezone")
        if needs_conversion(emp_tz, loc_tz):
            emp = {
                **emp,
                "available_windows": availability_to_location(
                    emp["available_windows"], emp_tz, loc_tz,
                ),
                "day_blackouts": blackouts_to_location(
                    emp.get("day_blackouts", []), week_start_date, num_days,
                    emp_tz, loc_tz,
                ),
            }
        converted_employees.append(emp)
        emp_roles = [r.get("role_name", "") for r in emp.get("roles", [])]
        logger.warning(
            "[SCHED-TRACE]   employee %s: roles=%s, avail_windows=%d (was %d before draft filter), tz=%s",
            emp.get("id", ""), emp_roles, len(emp["available_windows"]), windows_before,
            emp_tz or "-",
        )
    location_employees = converted_employees
```

- [ ] **Step 5: Committed shifts on the employee's clock (`graph.py`)**

Add `from backend.scheduling.employee_time import instant_to_employee_face`.
Change `_committed_shifts_by_employee`'s signature to accept
`employee_timezones: Dict[str, str | None] | None = None`, and inside the loop:

```python
    for s, loc in rows:
        emp_tz = (employee_timezones or {}).get(str(s.employee_id))
        face = _shift_employee_face(s, emp_tz) if emp_tz else None
        if face is None:
            face = _shift_local_face(s, loc)
        if face is None:
            continue
```

(keeping the existing week-filter and append lines below). Add the helper above
it:

```python
def _shift_employee_face(shift: Shift, emp_tz: str) -> Tuple[datetime, datetime] | None:
    """A committed Shift on the employee's own clock (employee has a timezone).

    Falls back to None on a bad zone or value; the caller then uses the
    location face, as for an employee without a timezone.
    """
    try:
        return (
            instant_to_employee_face(shift.start_time, emp_tz),
            instant_to_employee_face(shift.end_time, emp_tz),
        )
    except (AttributeError, ValueError, TypeError, ZoneInfoNotFoundError):
        return None
```

Add `employee_timezones: Dict[str, str | None] | None = None` to
`_load_employee_availability`'s signature (after `all_employee_ids`) and pass it
through:

```python
    emp_avail_map = _subtract_committed_shifts(
        emp_avail_map,
        await _committed_shifts_by_employee(
            db, company_id, week_start, week_end, employee_timezones,
        ),
    )
```

In `_load_initial_state`, pass
`employee_timezones={str(emp.id): emp.timezone for emp in employees_orm},` to
`_load_employee_availability(...)`, and add `"timezone": emp.timezone,` to the
employee dict (after `"seniority_rank"`).

- [ ] **Step 6: Run the node tests**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_scheduling.py -q`
Expected: PASS.

- [ ] **Step 7: Integration test for approved-shift holds across zones**

`tests/test_employee_timezone_holds_integration.py`:

```python
"""Approved shifts consume an employee-with-timezone's hours on their clock."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Location, Shift, ShiftSchedule
from backend.scheduling.graph import _load_employee_availability
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, REGION_ID, ROLE_FLOOR_ID, _id

DAY = date(2026, 3, 2)  # Monday


async def _seed(db: AsyncSession) -> None:
    la_id = _id()
    db.add(Location(id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
                    name="LA Store", timezone="America/Los_Angeles"))
    await db.flush()
    # Employee available 09:00-17:00 on THEIR (New York) clock.
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=DAY.year, month=DAY.month, day=DAY.day,
        start_time=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 3, 2, 17, 0, tzinfo=timezone.utc),
    ))
    sched_id = _id()
    db.add(ShiftSchedule(id=sched_id, company_id=COMPANY_ID, location_id=la_id,
                         week_start_date=DAY, status="approved"))
    await db.flush()
    # An approved 06:00-10:00 LA shift = 09:00-13:00 New York.
    la = ZoneInfo("America/Los_Angeles")
    db.add(Shift(
        id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched_id,
        location_id=la_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 3, 2, 6, 0, tzinfo=la),
        end_time=datetime(2026, 3, 2, 10, 0, tzinfo=la),
    ))
    await db.commit()


async def test_hold_is_subtracted_on_the_employees_clock(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed(db_session)
    avail = await _load_employee_availability(
        db_session, COMPANY_ID, DAY.isoformat(), 7,
        employee_timezones={EMPLOYEE1_ID: "America/New_York"},
    )
    windows = avail[EMPLOYEE1_ID]
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T13:00:00+00:00")
    assert windows[0]["end"].endswith("T17:00:00+00:00")


async def test_without_a_timezone_the_hold_keeps_todays_location_face(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed(db_session)
    avail = await _load_employee_availability(db_session, COMPANY_ID, DAY.isoformat(), 7)
    windows = avail[EMPLOYEE1_ID]
    # Today's behaviour: the LA face 06:00-10:00 is carved from 09:00-17:00.
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T10:00:00+00:00")
```

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_holds_integration.py -q`
Expected: PASS.

- [ ] **Step 8: Regression guard: every existing scheduling test, under UTC**

Run: `TZ=UTC backend/.venv/bin/python -m pytest tests/test_avail_local_wallclock.py tests/test_avail_tz.py tests/test_multi_location_availability.py tests/test_availability_holds_integration.py tests/test_seed_availability_integration.py tests/test_local_scheduler_integration.py tests/test_preferences_local_scheduler.py tests/test_schedule_pipeline_integration.py tests/test_min_rest_clopening.py tests/test_annotate_preferences_node.py tests/test_preferences_ai_path.py tests/test_cost_seniority_ai_path.py tests/test_signal_weights.py tests/test_affinity_validation.py -q`
Expected: all pass, no test edited.

- [ ] **Step 9: Commit**

```bash
git add backend/scheduling/graph.py backend/scheduling/nodes.py tests/test_employee_timezone_scheduling.py tests/test_employee_timezone_holds_integration.py
git commit -m "feat: convert availability and blackouts for employees with a timezone"
```

---

### Task 8: Edit warnings for employees with a timezone, plus the i18n strings

**Files:**
- Modify: `backend/services/edit_warnings.py` (lines ~125-187)
- Test: `tests/test_employee_timezone_edit_warnings_integration.py`
- Modify: all 19 `frontend/src/i18n/*.ts`

**Interfaces:**
- Consumes: `availability_to_location`, `needs_conversion` (Task 6); `Employee.timezone` (Task 5)

- [ ] **Step 1: Add the i18n keys (all four, used by Tasks 4 and 9)**

Run this once from the repo root. It inserts each key after an anchor that
exists in every locale and fails loudly if an anchor is missing:

```python
# scripts/_add_tz_i18n.py -- run with: python3 scripts/_add_tz_i18n.py ; then delete it
import pathlib, re

T = {
 "en":  ("These locations are set to UTC. If that isn't right, pick their timezone: {names}", "Availability timezone", "None (location's local time)", "Times are in {zone}"),
 "zh":  ("这些地点设置为 UTC。如果不正确，请选择它们的时区：{names}", "可用时间的时区", "无（地点当地时间）", "时间为 {zone} 时区"),
 "hi":  ("ये स्थान UTC पर सेट हैं। अगर यह सही नहीं है, तो उनका टाइमज़ोन चुनें: {names}", "उपलब्धता का टाइमज़ोन", "कोई नहीं (स्थान का स्थानीय समय)", "समय {zone} में है"),
 "ar":  ("هذه المواقع مضبوطة على UTC. إذا لم يكن ذلك صحيحًا، فاختر منطقتها الزمنية: {names}", "المنطقة الزمنية للتوفر", "لا شيء (التوقيت المحلي للموقع)", "الأوقات بتوقيت {zone}"),
 "fr":  ("Ces sites sont réglés sur UTC. Si ce n'est pas correct, choisissez leur fuseau horaire : {names}", "Fuseau horaire des disponibilités", "Aucun (heure locale du site)", "Heures exprimées en {zone}"),
 "es":  ("Estas ubicaciones están configuradas en UTC. Si no es correcto, elige su zona horaria: {names}", "Zona horaria de disponibilidad", "Ninguna (hora local de la ubicación)", "Horas en {zone}"),
 "pt":  ("Estes locais estão configurados em UTC. Se não estiver certo, escolha o fuso horário deles: {names}", "Fuso horário da disponibilidade", "Nenhum (hora local do local)", "Horários em {zone}"),
 "bn":  ("এই লোকেশনগুলো UTC-তে সেট করা আছে। এটি সঠিক না হলে, তাদের টাইমজোন বেছে নিন: {names}", "উপলব্ধতার টাইমজোন", "কোনোটি নয় (লোকেশনের স্থানীয় সময়)", "সময় {zone} অনুযায়ী"),
 "ru":  ("Для этих точек указан UTC. Если это неверно, выберите их часовой пояс: {names}", "Часовой пояс доступности", "Нет (местное время точки)", "Время указано в {zone}"),
 "ur":  ("یہ مقامات UTC پر سیٹ ہیں۔ اگر یہ درست نہیں تو ان کا ٹائم زون منتخب کریں: {names}", "دستیابی کا ٹائم زون", "کوئی نہیں (مقام کا مقامی وقت)", "اوقات {zone} کے مطابق ہیں"),
 "id":  ("Lokasi ini diatur ke UTC. Jika tidak sesuai, pilih zona waktunya: {names}", "Zona waktu ketersediaan", "Tidak ada (waktu lokal lokasi)", "Waktu dalam {zone}"),
 "de":  ("Diese Standorte sind auf UTC eingestellt. Falls das nicht stimmt, wähle ihre Zeitzone: {names}", "Zeitzone der Verfügbarkeit", "Keine (Ortszeit des Standorts)", "Zeiten in {zone}"),
 "pcm": ("Dis locations dey set for UTC. If e no correct, choose dem timezone: {names}", "Availability timezone", "None (location local time)", "Time dey for {zone}"),
 "te":  ("ఈ లొకేషన్లు UTCకి సెట్ చేయబడ్డాయి. అది సరైనది కాకపోతే, వాటి టైమ్‌జోన్ ఎంచుకోండి: {names}", "అందుబాటు టైమ్‌జోన్", "ఏదీ లేదు (లొకేషన్ స్థానిక సమయం)", "సమయాలు {zone}లో ఉన్నాయి"),
 "tr":  ("Bu konumlar UTC olarak ayarlı. Doğru değilse saat dilimlerini seçin: {names}", "Müsaitlik saat dilimi", "Yok (konumun yerel saati)", "Saatler {zone} saat diliminde"),
 "ta":  ("இந்த இடங்கள் UTC-க்கு அமைக்கப்பட்டுள்ளன. இது சரியில்லை என்றால், அவற்றின் நேர மண்டலத்தைத் தேர்ந்தெடுக்கவும்: {names}", "கிடைக்கும் நேர மண்டலம்", "எதுவுமில்லை (இடத்தின் உள்ளூர் நேரம்)", "நேரங்கள் {zone} இல் உள்ளன"),
 "vi":  ("Các địa điểm này đang đặt múi giờ UTC. Nếu chưa đúng, hãy chọn múi giờ của chúng: {names}", "Múi giờ của lịch rảnh", "Không có (giờ địa phương của địa điểm)", "Thời gian theo {zone}"),
 "ja":  ("これらの店舗はUTCに設定されています。正しくない場合はタイムゾーンを選択してください：{names}", "勤務可能時間のタイムゾーン", "なし（店舗の現地時間）", "時刻は{zone}です"),
 "mr":  ("ही ठिकाणे UTC वर सेट आहेत. हे बरोबर नसल्यास, त्यांचा टाइमझोन निवडा: {names}", "उपलब्धतेचा टाइमझोन", "काहीही नाही (ठिकाणाची स्थानिक वेळ)", "वेळा {zone} मध्ये आहेत"),
}

def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

for lang, (utc_hint, tz_label, tz_none, times_in) in T.items():
    p = pathlib.Path(f"frontend/src/i18n/{lang}.ts")
    s = p.read_text()
    def insert_after(block: str, anchor_key: str, line: str) -> None:
        global s
        b = s.index(f"  {block}: {{")
        m = re.compile(rf"^    {anchor_key}: .*$", re.M).search(s, b)
        assert m, (lang, block, anchor_key)
        s = s[:m.end()] + "\n" + line + s[m.end():]
    if "utcHint:" not in s:
        insert_after("locationsPage", "timezone", f"    utcHint: {q(utc_hint)},")
    if "availabilityTimezone:" not in s:
        insert_after("employeesPage", "payRateGatedHint", f"    availabilityTimezone: {q(tz_label)},\n    availabilityTimezoneNone: {q(tz_none)},")
    if "timesInZone:" not in s:
        insert_after("common", "appName", f"    timesInZone: {q(times_in)},")
    p.write_text(s)
print("ok")
```

Then: `python3 scripts/_add_tz_i18n.py && rm scripts/_add_tz_i18n.py && cd frontend && npx tsc --noEmit`
Expected: `ok`, no type errors. If an anchor assert fires, open that locale
file, find the equivalent line and adjust the anchor for that file only. If
Task 4 already added `utcHint`, the script skips it.

- [ ] **Step 2: Write the failing edit-warning test**

`tests/test_employee_timezone_edit_warnings_integration.py`:

```python
"""Edit warnings read a timezone employee's availability in the shift's zone."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, EmployeeAvailability, Location, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, REGION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"
DAY = date(2026, 8, 31)
LA = ZoneInfo("America/Los_Angeles")


async def _la_shift(db: AsyncSession) -> tuple[str, str]:
    la_id = _id()
    db.add(Location(id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
                    name="LA Store", timezone="America/Los_Angeles"))
    await db.flush()
    sched_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=sched_id, company_id=COMPANY_ID, location_id=la_id,
                         week_start_date=DAY, status="approved",
                         created_at=datetime.now(timezone.utc)))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sched_id,
                 location_id=la_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
                 role_name="Floor Associate", date=DAY,
                 start_time=datetime(2026, 8, 31, 6, 0, tzinfo=LA),
                 end_time=datetime(2026, 8, 31, 10, 0, tzinfo=LA)))
    # Available 09:00-17:00 on the employee's New York clock = 06:00-14:00 LA.
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=2026, month=8, day=31,
        start_time=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    ))
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE1_ID)
                     .values(timezone="America/New_York"))
    await db.commit()
    return sched_id, shift_id


async def test_a_covered_la_shift_does_not_warn(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    sched_id, shift_id = await _la_shift(db_session)
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id,
                         "start_time": "2026-08-31T07:00:00-07:00",
                         "end_time": "2026-08-31T11:00:00-07:00"}]},
    )
    assert resp.status_code == 200, resp.text
    codes = [w["code"] for w in resp.json().get("warnings", [])]
    assert "no_availability" not in codes


async def test_an_uncovered_la_shift_warns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    sched_id, shift_id = await _la_shift(db_session)
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id,
                         "start_time": "2026-08-31T13:00:00-07:00",
                         "end_time": "2026-08-31T17:00:00-07:00"}]},
    )
    assert resp.status_code == 200, resp.text
    codes = [w["code"] for w in resp.json().get("warnings", [])]
    assert "no_availability" in codes
```

Before running, check how the approved-shifts response exposes warnings
(`grep -n "warnings" backend/routers/schedules.py backend/schemas/schedule.py`).
If the key isn't `warnings` at the top level, adjust the two `resp.json()`
reads, and nothing else.

- [ ] **Step 3: Run to verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_edit_warnings_integration.py -q`
Expected: `test_a_covered_la_shift_does_not_warn` FAILS. Availability is read as
09:00-17:00 LA, so 07:00 is "uncovered".

- [ ] **Step 4: Implement in `backend/services/edit_warnings.py`**

Imports: `from backend.models import Employee, EmployeeAvailability, Location, Shift`
and `from backend.scheduling.employee_time import availability_to_location, needs_conversion`.

Just before `# 1. no_availability`, inside the
`if emp_id is not None and span_start is not None and span_end is not None:`
block, load the employee's zone and decide the comparison zone:

```python
            emp_tz = (await db.execute(
                select(Employee.timezone).where(
                    Employee.id == emp_id, Employee.company_id == company_id,
                )
            )).scalar_one_or_none()
            # An employee with a timezone is compared in the edited shift's
            # location zone: availability converted emp->loc, other shifts read
            # in that zone. Without one (or with no known location), today's
            # per-location faces are used unchanged.
            convert = location is not None and needs_conversion(emp_tz, location.timezone)
```

In the `other_rows` loop, replace `face = _shift_local_face(s, loc)` with:

```python
                face = _shift_local_face(s, location if convert else loc)
```

Replace the `covered` loop's window parsing:

```python
            covered = False
            for w in windows:
                raw = [{"start": w.start_time.isoformat(), "end": w.end_time.isoformat()}]
                pieces = (
                    availability_to_location(raw, emp_tz, location.timezone)
                    if convert else raw
                )
                for piece in pieces:
                    w_start = _wall_clock(piece["start"])
                    w_end = _wall_clock(piece["end"])
                    for free_start, free_end in _subtract_consumed(w_start, w_end, other_spans):
                        if free_start <= span_start and free_end >= span_end:
                            covered = True
                            break
                    if covered:
                        break
                if covered:
                    break
```

- [ ] **Step 5: Run the new and existing warning tests**

Run: `backend/.venv/bin/python -m pytest tests/test_employee_timezone_edit_warnings_integration.py tests/test_edit_approved_warnings_integration.py tests/test_edit_approved_schedule_integration.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/services/edit_warnings.py tests/test_employee_timezone_edit_warnings_integration.py frontend/src/i18n
git commit -m "feat: edit warnings honour an employee timezone; add timezone i18n strings"
```

---

### Task 9: Employee timezone in the UI

**Files:**
- Modify: `frontend/src/types/index.ts` (`Employee` ~line 150, `EmployeeMeProfile` line 171, `EmployeeMeLocation`)
- Modify: `frontend/src/api/employees.ts` (create/update payload types ~lines 32, 52)
- Modify: `frontend/src/pages/manager/Employees.tsx` (state lines 36-63, `startEdit` ~line 108, `cancelEdit`, `handleSave`, `handleCreate`, location cells lines ~587-592 and ~713)
- Modify: `frontend/src/pages/employee/Availability.tsx` (header, line ~103 onwards)

**Interfaces:**
- Consumes: `timeZoneOptions`, `availabilityZoneNote` (Task 4); i18n keys
  `employeesPage.availabilityTimezone`, `employeesPage.availabilityTimezoneNone`, `common.timesInZone` (Task 8)

- [ ] **Step 1: Types and API payloads**

- `types/index.ts`:
  - add `timezone: string | null;` to `Employee` (after `seniority_rank`) and to `EmployeeMeProfile` (after `email`)
  - add `timezone?: string | null;` to `EmployeeMeLocation`
- `api/employees.ts`: add `timezone?: string | null;` next to `seniority_rank?:` in
  both the create and update payload types.

- [ ] **Step 2: Employees page: a select inside the location cell**

In both `editValues` and `addValues` state types add `timezone: string;` and
`timezone: ""` to every initial/reset object: the two `useState` initialisers,
`cancelEdit`, and the add-row reset after `handleCreate` and in the Cancel
button around line 743. In `startEdit`, add `timezone: emp.timezone ?? "",`.
In `handleSave` and `handleCreate` payloads add
`timezone: editValues.timezone || null,` and
`timezone: addValues.timezone || null,` respectively.

Add the import `import { timeZoneOptions } from "../../utils/timezones";` and a
memo near the other memos: `const tzOptions = useMemo(() => timeZoneOptions(), []);`.

Edit-row location cell (replace the `isEditing ? renderLocationEditor(...)`
branch):

```tsx
                    {isEditing ? (
                      <>
                        {renderLocationEditor(editValues.location_ids, (ids) =>
                          setEditValues((v) => ({ ...v, location_ids: ids }))
                        )}
                        <label className={`mt-2 block text-xs ${text.muted}`}>
                          {t.employeesPage.availabilityTimezone}
                          <select
                            className="glass-input-sm mt-1 w-full"
                            value={editValues.timezone}
                            onChange={(e) => setEditValues((v) => ({ ...v, timezone: e.target.value }))}
                          >
                            <option value="">{t.employeesPage.availabilityTimezoneNone}</option>
                            {tzOptions.map((o) => (
                              <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                          </select>
                        </label>
                      </>
                    ) : (
                      <>
                        {renderLocationBadges(emp.location_ids)}
                        {emp.timezone && (
                          <span className={`mt-1 block text-xs ${text.muted}`}>{emp.timezone}</span>
                        )}
                      </>
                    )}
```

Add-row location cell (around line 713): wrap the existing
`renderLocationEditor(addValues.location_ids, …)` in a fragment and append the
same `<label>…<select>` bound to `addValues.timezone`/`setAddValues`.

- [ ] **Step 3: Employee Availability page note**

In `pages/employee/Availability.tsx`, import
`{ availabilityZoneNote } from "../../utils/timezones"`. Directly under the
main-render `<h1 …>{t.availability.title}</h1>` (the one after
`if (!profile)`), add:

```tsx
      {(() => {
        const zone = availabilityZoneNote(
          profile.timezone,
          profile.locations.map((l) => l.timezone).filter((z): z is string => !!z),
        );
        return zone ? (
          <p className={`mb-4 text-sm ${text.muted}`}>
            {t.common.timesInZone.replace("{zone}", zone)}
          </p>
        ) : null;
      })()}
```

(The manager availability page is a combined all-employee table. The
employee's zone is shown on the Employees page instead, a deliberate
narrowing of the spec's "availability pages". Note it in the PR.)

- [ ] **Step 4: Type-check, test, build**

Run: `cd frontend && npx tsc --noEmit && npm test && npm run build`
Expected: no type errors; all vitest suites pass (incl. `logicalDirection.test.ts`); build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/employees.ts frontend/src/pages/manager/Employees.tsx frontend/src/pages/employee/Availability.tsx
git commit -m "feat: optional employee availability timezone in the manager UI"
```

---

### Task 10: Full verification and PR

**Files:** `docs/superpowers/specs/2026-10-14-employee-timezone-design.md` (one-line scope note)

- [ ] **Step 1: Backend suites as CI runs them, under two host zones**

```bash
TZ=UTC backend/.venv/bin/python -m pytest tests/ --ignore=tests/test_migrations.py -m "not integration" -q
TZ=UTC backend/.venv/bin/python -m pytest tests/ --ignore=tests/test_migrations.py -m integration -q
TZ=Asia/Tokyo backend/.venv/bin/python -m pytest tests/test_employee_time.py tests/test_employee_timezone_scheduling.py tests/test_employee_timezone_holds_integration.py tests/test_employee_timezone_edit_warnings_integration.py tests/test_tz_utils.py -q
backend/.venv/bin/python -m pytest tests/test_migrations.py tests/test_schema_7shifts_compat.py tests/test_schema_deputy_compat.py -q
```

Expected: all pass. Paste the summary lines into the PR.

- [ ] **Step 2: Frontend**

Run: `cd frontend && npx tsc --noEmit && npm test && npm run build`
Expected: pass.

- [ ] **Step 3: Spec note**

In the spec's §4 bullet about availability pages, append: "(Implemented on
the employee's own Availability page; managers see the zone on the Employees
page.)"

- [ ] **Step 4: Commit, push, mark the PR ready**

```bash
git add docs/superpowers/specs/2026-10-14-employee-timezone-design.md
git commit -m "docs: note where the availability timezone note appears"
git push
gh pr ready 170
```

Update PR #170's description:
- tick the checklist
- summarise the changes
- record the Deputy payload finding from Task 3 Step 5
- state that migration `0046` must deploy after #169
