# Employee timezone for availability

> **Superseded by #171 (2026-10-28).** The product decision is that availability
> is always the location's local time: whoever enters it (the employee, a
> manager, an import) is assumed to know the location's zone. The per-employee
> conversion was removed and `employees.timezone` was dropped (migration 0055).
> This document is kept as history only.

**Date:** 2026-10-14
**Branch / PR:** `feat/employee-timezone` / #170 (draft)
**Status:** Design approved in conversation; spec awaiting review

## Goal

Let a manager **optionally** give an employee an explicit timezone, so that
employee's availability can be read correctly across locations in different
zones. Do this **ahead of need**: every current customer is single-zone, and for
them behaviour must not change.

By default an employee has **no timezone**, and their availability is the local
time of whichever location is being scheduled, exactly as today. Availability is
converted **only** for an employee whose timezone a manager has explicitly set.

### Principles

1. **Nobody has to think about timezones by default.** No required fields, no
   setup steps, no prompts. Employees have no timezone unless a manager sets
   one, and nothing new is shown for an employee without one (every employee
   today).
2. **One rule per kind of time.**
   - Availability and blackouts describe the *person's life*. They are read in
     the employee's zone **when one is set**, otherwise in the location's local
     time, as today.
   - Preferences and caps describe *the kind of shift*, so they always stay in
     the **location's** local time, unchanged.
3. **Convert at one boundary, and only when asked.** For an employee with an
   explicit timezone, availability stays on the employee's clock until the
   scheduler hands it to a specific location, then it is converted once. For
   everyone else nothing is converted anywhere. Everything after the boundary
   compares location-local times face-to-face, exactly as today.

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
- the optional `employees.timezone` column, set only by a manager
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

A location's zone is the target every conversion lands in, and it already
decides real shift instants (check-in matching, payroll lateness, notice
periods). Locations must stop silently becoming `UTC`.

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

## 2. Employee timezone: data model

- **Column:** `employees.timezone`, `String(64)`, nullable. Migration `0046`;
  no backfill.
- **Meaning:**
  - `NULL` (the default, and every existing row): **no timezone**. The
    employee's availability and blackouts are the local time of whichever
    location is being scheduled. Today's behaviour, with no conversion.
  - A value: set **only** by a manager. Availability and blackouts are that
    zone's wall clock and get converted to each location's zone.
- **Nothing infers or derives it.** Imports (7shifts, Deputy, CSV employees)
  and employee creation leave it NULL. Changing an employee's locations never
  touches it.
- **API:** `EmployeeCreate`/`EmployeeUpdate` accept optional `timezone`,
  validated with `zoneinfo`; `null` clears it. `EmployeeResponse` returns
  `timezone` (nullable).
- **UI (manager employee form):** an optional "Timezone" select, defaulting to
  "None (location's local time)". Picking a zone sets it, and picking None clears
  it. Nothing else in the UI changes.

## 3. Conversion in the scheduler

New module `backend/scheduling/employee_time.py`, pure functions, no DB access:

Every function takes `emp_tz: str | None`. **`None` means no conversion**:
the input is returned unchanged.

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
- **Short-circuit.** When `emp_tz is None`, or equals `loc_tz` by key, every
  function returns its input unchanged, with no parsing or rebuilding.

### Where they are called

1. **`graph._load_employee_availability`.** Loads availability unchanged and
   attaches `emp["timezone"]` (the column value, usually `None`). Blackouts are
   loaded the same way.
2. **`graph._committed_shifts_by_employee`.**
   - With `emp_tz` set: approved shifts are converted with
     `instant_to_employee_face(shift_instant, emp_tz)`, so subtraction happens
     on the employee's clock.
   - With `None`: today's `_shift_local_face(shift, its location)`, unchanged.
3. **`nodes._filter_availability_against_draft`.** Draft spans from locations
   earlier in the run carry real offsets.
   - With `emp_tz` set: they are converted with `instant_to_employee_face`
     before subtraction.
   - With `None`: today's face subtraction, unchanged.
4. **`nodes.load_location_context` is the single conversion point.** For an
   employee with `emp_tz` set, their remaining `available_windows` and blackouts
   are converted to the current location's zone after the draft filter. For
   everyone else, nothing happens. Everything downstream is unchanged:
   - `prompts._parse_avail_by_day`
   - `eligible_for_slot`
   - the LLM prompt
   - `validate_schedule`
   - `local_schedule`
   - `annotate_preferences`
5. **`services/edit_warnings.py`.** For an employee with `emp_tz` set, it
   compares in the **edited shift's location** zone:
   - availability is converted emp→loc
   - the employee's other shifts are converted from their instants into that
     zone

   With `None`, it behaves exactly as today.

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
  clock: the location's, unless a manager set the employee's timezone.
- Every code path for an employee without a timezone, i.e. every employee
  today.
- The #61/#85 rule: stored availability is never reinterpreted with
  `.astimezone()`. Conversion happens only for an explicitly set employee zone,
  between two explicit zones, at the boundary.
- Preferences, caps and day preferences.
- The employee availability pages. A small "Times are in America/Los_Angeles"
  note appears **only** for an employee with an explicit timezone that differs
  from one of their locations' zones. The note appears on the employee's
  Availability page and on the manager's Employee Availability page.

## 5. Testing

- **No timezone means no conversion.** For an employee with
  `timezone = NULL` at locations in **different** zones, availability is read as
  each location's local time, unchanged. This pins the default.
- **`employee_time`:**
  - `None` and same-zone return their input unchanged (no rebuild)
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
- **Cross-zone integration:** an employee whose timezone a manager set to
  `America/New_York`, working a New York and a Los Angeles location.
  - availability 09:00–17:00 New York covers 06:00–14:00 in Los Angeles
  - a New York shift consumes the right Los Angeles hours (no double-booking)
  - an evening blackout crosses into the next day correctly
  - edit warnings fire on the converted window
- **Location:** a non-IANA value gets a 422; bulk upload uses `default_timezone`
  for blank rows and `UTC` when it's absent; the Deputy zone.
- **Offset bug:** a January slot at a New York location carries `-05:00`.
- **Frontend:** the location dropdown pre-selects the browser zone; the
  employee select defaults to None, and None clears it; the logical-direction
  Tailwind rule
  (`logicalDirection.test.ts`) still passes.

## 6. Rollout

- Migration `0046` is additive (a nullable column). It must land after #169,
  which fixes `0044`/`0045`.
- No data migration. Every employee starts with no timezone, so nothing is
  converted and schedules generated before and after deploy are identical. The
  golden test pins this.
- The existing-`UTC` hint lets managers correct legacy locations at their own
  pace.
