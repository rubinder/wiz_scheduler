# Employee timezone for availability

**Date:** 2026-10-14
**Branch / PR:** `feat/employee-timezone` / #170 (draft)
**Status:** Design approved in conversation; spec awaiting review

## Goal

Make employee availability carry an explicit timezone, so it is never ambiguous
when an employee works at locations in different zones. Do this **ahead of
need**: every current customer is single-zone, and for them behaviour must not
change.

### Principles

1. **Nobody has to think about timezones by default.** No required fields, no
   setup steps, no prompts. Every zone is inferred, timezone controls are
   optional, and nothing new is shown while an employee's zone equals their
   locations' zone (true for every employee today).
2. **One rule per kind of time.**
   - Availability and blackouts describe the *person's life*, so they are read in
     the **employee's** zone.
   - Preferences and caps describe *the kind of shift*, so they stay in the
     **location's** local time, unchanged.
3. **Convert at one boundary.** Availability stays on the employee's clock until
   the scheduler hands it to a specific location, then it is converted once.
   Everything after that point compares location-local times face-to-face,
   exactly as today.

### Background

- Availability is stored as location wall-clock time tagged `+00:00` and compared
  face-to-face. Rule from #61/#85: never `.astimezone()` availability.
- 7shifts availability (`{day}_from`/`{day}_to`) and time-off
  (`partial_from`/`partial_to`) carry no timezone. 7shifts locations always do
  (`timezone`, required, IANA). Both were confirmed against the live API on
  2026-10-14.
- WizScheduler locations have a required `timezone` column, but hand-created
  locations get `"UTC"` when the manager leaves it blank (form:
  `Locations.tsx:171`; CSV: `locations.py:212`). Deputy imports hard-code
  `America/New_York` (`import_deputy.py:188`).
- Employees have no timezone and no "primary location": only `location_ids`, a
  JSON list whose order is not meaningful.

## Scope

**In:**
- location timezone inference and a dropdown
- the `employees.timezone` column and its resolver
- converting availability and blackouts at the scheduler boundary
- edit warnings
- the offset-date bug fix
- the Deputy location zone

**Out:**
- preferences and caps (they stay location-local by design)
- storing availability as true instants
- address geocoding
- employees setting their own zone
- per-viewer display conversion

---

## 1. Location timezone (first piece)

Employee zones derive from location zones, so locations must stop silently
becoming `UTC`.

1. **Dropdown.** Replace the free-text Timezone field in `Locations.tsx` with a
   searchable dropdown of IANA zones from `Intl.supportedValuesOf("timeZone")`
   (no new dependency). New locations get the browser's zone pre-selected:
   `Intl.DateTimeFormat().resolvedOptions().timeZone`.
2. **Bulk upload.** The CSV/JSON upload request sends the browser zone as
   `default_timezone`, and blank rows use it. If it's absent (a direct API
   caller), the backend falls back to `"UTC"`, as today.
3. **Validation.** Create, update and bulk upload reject any value
   `zoneinfo.ZoneInfo` cannot load, with a 422. Today any string is accepted.
4. **Deputy import.** Use the zone from Deputy's location payload if present,
   otherwise the importing manager's browser zone sent with the import request.
   The payload field is to be confirmed during implementation.
5. **Existing `UTC` locations.** Not changed automatically: a real UTC store
   cannot be told apart from a defaulted blank. The Locations page shows a quiet
   inline hint beside a location whose zone is `UTC` ("Is this location really
   on UTC?"). It is not a banner and does not block anything.

## 2. Employee timezone: data model and resolution

- **Column:** `employees.timezone`, `String(64)`, nullable. Migration `0046`;
  no backfill.
- **Meaning:**
  - `NULL` (the default, and every existing row): derive the zone from the
    employee's locations whenever it's needed, so it follows location changes.
  - A value: an explicit manager override, used as-is.
- **Resolver:** `services/employee_timezone.resolve_employee_timezone(employee,
  locations_by_id, company_locations) -> str` is the **only** reader of the
  column. Rules, in order:
  1. If `employee.timezone` is set, return it.
  2. Otherwise take the zones of the employee's own locations and return the most
     common one.
  3. On a tie, prefer the zone most common across all of the company's
     locations; if still tied, the alphabetically first zone.
  4. If the employee has no locations, return the most common company zone.
  5. If the company has no locations, return `"UTC"`.

  Deterministic. There is no "first location", since locations have no
  creation timestamp and `location_ids` order is not meaningful.
- **Imports** (7shifts, Deputy, CSV employees) leave `timezone` NULL.
- **API:**
  - `EmployeeCreate`/`EmployeeUpdate` accept optional `timezone`, validated with
    `zoneinfo`; `null` clears an override.
  - `EmployeeResponse` adds `timezone` (raw, nullable) and `effective_timezone`
    (resolved).
- **UI (manager employee form):** an optional "Timezone" select, defaulting to
  "Automatic (same as location: America/New_York)". Picking a zone sets the
  override, and picking Automatic clears it. Nothing else in the UI changes.

## 3. Conversion in the scheduler

New module `backend/scheduling/employee_time.py`, pure functions, no DB access:

- `availability_to_location(windows, emp_tz, loc_tz) -> windows`: reads each
  window's face in `emp_tz` on its own date, converts it to `loc_tz`, and emits
  it in the existing shape (local face tagged `+00:00`). Any window that crosses
  midnight after conversion is **split at midnight**, because downstream code
  assumes a window sits within one day.
- `blackouts_to_location(blackouts, week_dates, emp_tz, loc_tz) -> blackouts`:
  expands each recurring blackout (`day_of_week`, `HH:MM`–`HH:MM`) onto the
  actual dates of the week being scheduled, converts each per date (correct
  across DST), and returns records in the **same** `(day_of_week, start, end)`
  shape, split at midnight. A blackout may land on a different weekday. Within a
  single scheduled week, weekday and date map one-to-one.
- `instant_to_employee_face(dt, emp_tz) -> naive datetime`: converts a real
  instant (a shift) to the employee's wall clock.
- **Same zone short-circuit.** When `emp_tz == loc_tz` (by key), every function
  returns its input unchanged, with no parsing or rebuilding.

### Where they are called

1. **`graph._load_employee_availability`.** Loads availability unchanged
   (employee clock) and attaches each employee's resolved zone to the employee
   record (`emp["timezone"]`). Blackouts are loaded the same way.
2. **`graph._committed_shifts_by_employee`.** Approved shifts are converted with
   `instant_to_employee_face(shift_instant, emp_tz)` instead of
   `_shift_local_face(shift, its location)`, so subtraction happens on the
   employee's clock. When every location shares the employee's zone, the result
   is identical to today.
3. **`nodes._filter_availability_against_draft`.** Draft spans from locations
   earlier in the run carry real offsets. They are converted with
   `instant_to_employee_face` before subtraction, which fixes the cross-zone
   case where they are currently read on the earlier location's clock.
4. **`nodes.load_location_context` is the single conversion point.** After the
   draft filter, each eligible employee's remaining `available_windows` and
   blackouts are converted to the current location's zone. Everything downstream
   is unchanged:
   - `prompts._parse_avail_by_day`
   - `eligible_for_slot`
   - the LLM prompt
   - `validate_schedule`
   - `local_schedule`
   - `annotate_preferences`
5. **`services/edit_warnings.py`.** Compares in the **edited shift's location**
   zone:
   - availability is converted emp→loc
   - the employee's other shifts are converted from their instants into the
     edited shift's location zone, instead of each one's own location zone

Instant-based checks (`validate_and_update_availability` draft overlap,
`rest_rules`, max hours) are already zone-correct and are untouched.

### Bug fix carried in

`local_scheduler._tz_offset_for_location` and `prompts._tz_offset_example`
compute the offset at a fixed date (2026-03-30), so winter shifts get the summer
offset (`-04:00` instead of `-05:00` in New York). Both take the slot's actual
date and compute the offset for that date.

### DST edge cases

A face that does not exist (spring-forward, e.g. 02:30) or is ambiguous
(fall-back) is resolved with `fold=0`, matching `zoneinfo`'s default. Both cases
are only reachable for cross-zone employees and are covered by tests.

## 4. What does not change

- Storage of availability, write paths (UI, 7shifts, Deputy, seed) and the
  availability API responses. Availability is still entered and shown as wall
  clock; it now means the employee's clock, which for every employee today is
  the location's.
- The #61/#85 rule: stored availability is never reinterpreted with
  `.astimezone()`. Conversion happens only between two explicit zones, at the
  boundary.
- Preferences, caps and day preferences.
- The employee availability pages. A small "Times are in America/Los_Angeles"
  note appears **only** when the employee's effective zone differs from one of
  their locations' zones. That never happens today.

## 5. Testing

- **Resolver:** override wins; majority; tie by company majority, then
  alphabetical; no locations; no company locations.
- **`employee_time`:**
  - same zone returns its input unchanged (no rebuild)
  - New York → Los Angeles window
  - midnight split in both directions
  - a blackout moving to another weekday
  - a DST-transition week in both directions (spring-forward and fall-back)
  - nonexistent and ambiguous faces
- **Regression guard.** The existing contract tests pass unchanged:
  `test_avail_local_wallclock.py`, `test_avail_tz.py`,
  `test_multi_location_availability.py`,
  `test_availability_holds_integration.py`,
  `test_edit_approved_warnings_integration.py`,
  `test_seed_availability_integration.py`, the preference/local-scheduler
  tests. A golden test also runs the local scheduler on the seed data and
  asserts identical output with the feature in place.
- **Cross-zone integration:** an employee in `America/New_York` working a New
  York and a Los Angeles location.
  - availability 09:00–17:00 New York covers 06:00–14:00 in Los Angeles
  - a New York shift consumes the right Los Angeles hours (no double-booking)
  - an evening blackout crosses into the next day correctly
  - edit warnings fire on the converted window
- **Location:** a non-IANA value gets a 422; bulk upload uses `default_timezone`
  for blank rows and `UTC` when it's absent; the Deputy zone.
- **Offset bug:** a January slot at a New York location carries `-05:00`.
- **Frontend:** the dropdown pre-selects the browser zone; Automatic clears the
  override; the logical-direction Tailwind rule
  (`logicalDirection.test.ts`) still passes.

## 6. Rollout

- Migration `0046` is additive (a nullable column). It must land after #169,
  which fixes `0044`/`0045`.
- No data migration. With every employee resolving to their locations' zone,
  the conversion does nothing, so schedules generated before and after deploy
  are identical. The golden test pins this.
- The existing-`UTC` hint lets managers correct legacy locations at their own
  pace.
