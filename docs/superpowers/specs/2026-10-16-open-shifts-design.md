# Open shifts: keep vacancies, label them, alert managers, rank the picker

**Date:** 2026-10-16
**Issue:** #174
**Followed by:** #126 (archive instead of delete), which turns an archived
employee's or role's future approved shifts into open shifts
**Branch:** `feat/open-shifts`
**Status:** Design agreed in conversation; spec awaiting review

## Goal

No vacancy silently disappears. A manager can:

- see every open shift for the week,
- be told about them without opening the schedule,
- click one, and
- fill it from a picker whose top names fit.

## Background (today)

- **Draft.** The scheduler's coverage check (`validate_schedule`,
  `nodes.py:871`) adds placeholders for template shortfalls. Each has
  `employee_id: "VACANT"` and a hard-coded English name,
  "VACANT-Staffing Shortage" (`nodes.py:935`). `ScheduleGrid` styles these
  like filled shifts, and the name is not translated.
- **Approval drops vacancies.** `schedules.py:743` skips anything that is not
  `ok` or has `employee_id == "VACANT"`. `shifts.employee_id` is NOT NULL, so
  an approved schedule cannot hold an unfilled slot at all.
- **No alert.** Nothing emails, notifies, counts or shows a banner for
  vacancies. `RosterThinBanner` is about preferences.
- **Unranked picker.** `EmployeeSearchBox`, used by both `EditShiftModal`s,
  lists every employee in API order and filters only by typed name.
- **README is inaccurate.** `README.md:33,94,100` describe a
  "2 required, 1 filled" view and vacancies a manager fixes after approval.
  Neither exists.

## Decisions

- An open shift is a `Shift` with `employee_id IS NULL`. A separate table
  and a placeholder "Open" employee were considered and rejected. A separate
  table needs a second code path for the grid, editor, locking and warnings.
  A placeholder employee leaks into counts, plan limits, pickers, payroll and
  imports.
- Managers can create open shifts themselves: unassign a filled shift, or
  add a shift with no one on it.
- The ranked picker works in both the draft review and the approved
  schedule.
- Alerts are a banner on the schedule pages plus a navigation badge. No
  email.
- The 7shifts export skips open shifts; the JSONL export includes them.
- Employees never see open shifts. Claiming them is out of scope.

## 1. Data and API

### Migration 0047

- `shifts.employee_id` becomes nullable. The foreign key stays.
- The downgrade refuses (raises) while any open shift exists, rather than
  deleting them.

### Draft JSON (`raw_llm_output`)

- The shape is unchanged. The scheduler writes open entries as
  `"employee_id": null, "employee_name": null, "status": "OPEN"`.
- Every draft reader also accepts the legacy form
  (`"employee_id": "VACANT"` / `"status": "VACANT"`) as open. Drafts saved
  before the deploy therefore keep their vacancies.

### Approval (`schedules.py:741`)

- `OPEN` and legacy `VACANT` entries become `Shift` rows with
  `employee_id = NULL`.
- `CONFLICT` entries and entries with no valid role are still skipped.
- Fairness minutes (`EmployeeRoleMinutes`, legacy) skip open entries.

### Editing approved shifts (`PUT /schedules/{id}/approved-shifts`)

A missing `employee_id` already means "unchanged", so `ApprovedShiftEdit`
gains `open: bool = False`:

- `open: true` on an existing shift sets `employee_id = NULL`.
- `open: true` with no `shift_id` creates a new open shift.
- `open: true` together with `employee_id` returns 400 `invalid_edit`.
- The `touched` set and edit warnings skip a NULL employee.
- The check-in lock (409 `shift_locked_by_checkin`) and the time-entry guard
  (409 `shift_has_time_entry`) apply unchanged.

### Draft edits (`PUT /schedules/{id}/shifts`)

They accept `employee_id: null` with `status: "OPEN"`.

### Reads

- **Approved week** (`GET /schedules/week/...`, `schedules.py:1019`): open
  shifts return `employee_id: null` and `employee_name: null`. The backend
  never sends a display string for them.
- **Preference asterisks:** `annotate_preference_violations` skips open
  shifts.
- **Export to 7shifts:** skips open shifts. The result gains
  `open_shifts_skipped: int`.
- **JSONL export and the approved-schedules export list:** include open
  shifts with `employee_id: null` and `employee_name: null`.
- **Unchanged,** because they already filter by employee: check-in
  (`services/check_in.py:133`), payroll derivation
  (`services/time_entries.py`, inner join on `Employee`), other-shift lookups
  in edit warnings, and GDPR.

## 2. Scheduler

- **Internals unchanged.** `validate_schedule` and the local scheduler still
  find the shortfall per (day, role) and add one placeholder per missing
  person.
- **What they emit changes.** Each placeholder is
  `employee_id: null, employee_name: null, status: "OPEN"`, carrying the
  template's role, location, date and times.
- **Preference caps.** A cap at weight 1.0 (`nodes.py:1036`) emits `OPEN`
  instead of `VACANT`.
- **Stream.** `LocationResult` gains `open_count: int`. NDJSON lines carry
  `OPEN` entries like any other shift.
- **Where the shape changes.** Inside the graph, every node's checks are
  written against the `VACANT` placeholder, so the graph keeps using it.
  `emit_result`, the last node on both the AI and local paths, converts each
  placeholder to the open shape. Everything downstream (draft JSON, approval,
  the API, the UI) only sees open entries, or legacy `VACANT` entries in old
  drafts.

## 3. Candidate ranking

### `POST /schedules/candidates` (manager only)

- **Request:** `location_id`, `role_id`, `date`, `start_time` and
  `end_time` (local `HH:MM` at the location; an end at or before the start
  means the next day), and an optional `exclude_shift_id`.
- **Response:** the company's employees in tiers 1–3 (#126 later adds an
  archived filter), each with `employee_id`, `full_name`, `tier`, and three
  flags: `available`, `has_role` and `at_location`.

### Tiers

1. available, has the role, and assigned to the location
2. has the role and assigned to the location, but not available
3. available and assigned to the location, but without the role

Everyone else is not offered: people not assigned to the location, and
people assigned to it with neither the role nor availability. (Decided
2026-10-16: the picker shows only people who fit the location.)

Within a tier, names sort alphabetically. Picking someone from tier 2 saves
normally and gets the existing `no_availability` warning after saving.

### What the flags mean

- **`at_location`:** the location is in the employee's `location_ids`, the
  same rule as `nodes.py:42`.
- **`has_role`:** an `employee_roles` row exists for `role_id`.
- **`available`:** the scheduler's definition. An availability window covers
  the whole span after subtracting the employee's other approved shifts, and
  the span does not overlap one of the employee's blackouts. A blackout is a
  recurring weekday time range (for example Monday 20:00–22:00), not a whole
  day. When the employee has a timezone, the
  employee's zone is converted to the location's zone (`employee_time.py`).
  `exclude_shift_id` is left out of "other shifts".

### One availability check

The coverage logic in `services/edit_warnings.py` moves into one shared
function used by both the candidates endpoint and the edit warnings.
Blackouts count in both.

This is an intentional behaviour change: the `no_availability` edit warning
now also fires when the span overlaps a blackout. The shared function also
reads availability windows the way the scheduler does: a window whose end is
at or before its start means "until 23:59".

### Queries

Everything is bulk-loaded in about five queries regardless of company size:
employees, employee roles, availability windows touching the date,
blackouts, and that day's approved shifts for the company. Scoring then
happens in Python. There are no per-employee queries.

### Draft review

The same endpoint is used. "Other shifts" means approved shifts only;
unsaved assignments in the same draft are not seen, and the save warnings
cover them.

### Access

- Everything is scoped to `current_user.company_id`, like every other
  schedule route. A location outside the company returns 404.
- Per-manager location scoping (`get_accessible_location_ids`) is not used.
  No schedule route enforces it today, and it returns no locations for a
  manager whose `manager_type` is unset, so using it here alone would hide
  the picker and the badge from those managers.

## 4. UI and alerts

### Open-shift card (`ScheduleGrid`)

- Rendered when `employee_id === null`, or for the legacy `"VACANT"`.
- Dashed border, amber tint, and the translated label "Open shift" in place
  of a name. Role and times show as usual.
- Clicking it opens the edit modal, as for any shift.

### Picker (both `EditShiftModal`s)

- When the modal opens, it calls `/schedules/candidates` for the shift's
  slot. It asks again when the role or times change in the modal.
- The list is grouped under three translated headings:
  1. Best fit
  2. Has the role, not available
  3. Available, no role
- Each person carries small chips for the flags they are missing.
- Typing filters by name within the groups.
- "Leave open" sits at the top. On an approved shift it sends `open: true`;
  in a draft it sets `employee_id: null` with `status: "OPEN"`.
- If the ranking request fails, the picker falls back to the plain,
  ungrouped employee list, with a short notice.
- This is a new `CandidatePicker` component. `EmployeeSearchBox` is
  unchanged, because other pages use it.

### Adding an open shift

Neither page has an add-shift control today; the approved-shift API can
create a shift, but no UI calls it. Each location on both pages gets a
"+ Add open shift" button. It opens a small form (role, day of the week,
start and end time) and creates an open shift: a new open entry in the draft,
or `open: true` with no `shift_id` on an approved schedule. A new approved
shift's times are read in the location's zone when they arrive without an
offset, as edits to existing shifts already are.

### Banner

- "{n} open shifts this week", shown per location on the draft review (from
  `open_count`) and on the approved week (counted from the loaded shifts).
- Clicking it highlights the open cards and scrolls to the first one.
- Hidden when the count is 0.

### Navigation badge

- `GET /schedules/open-shifts/count` returns the number of open shifts in
  approved schedules for the company, starting in `[now, now + 14 days)`.
- The Sidebar's "Approved Schedules" item shows the count when it is above
  0, and so does the collapsed "Scheduling" group heading.
- It is fetched when the Sidebar mounts and on every route change, and
  refreshed after any approved-shift edit or approval through a
  `wiz:open-shifts-changed` window event.

### Conventions

- New strings go into all 19 locales.
- Styling uses logical utilities only (`ms-`, `border-s`, `text-start`),
  enforced by `logicalDirection.test.ts`.

### Employees

Employee schedule views already filter by `employee_id`. Open shifts never
appear there.

## 5. Documentation

- **README:** rewrite `README.md:33,94,100` to describe open shifts as they
  now work. A shortfall becomes labelled open shifts that survive approval,
  are counted in a banner and the navigation badge, and are filled from a
  ranked picker.
- **CLAUDE.md:** add a convention entry: "An open shift is a `Shift` with
  `employee_id IS NULL`. Any new reader of shifts must decide explicitly
  whether it includes open shifts. Candidate ranking and the
  `no_availability` warning share one availability check; the frontend never
  evaluates availability itself."
- **Marketing site** (separate repo): after this ships, check the scheduling
  feature pages for vacancy claims. This is a follow-up, not part of this
  change.

## 6. Testing

Integration tests run on Postgres. Frontend tests use vitest.

### Backend

- **Migration:** nullable after upgrade; the downgrade refuses while open
  shifts exist.
- **Scheduler:**
  - a template shortfall emits one `OPEN` entry per missing person with
    `employee_id: null`, and `open_count` matches
  - a weight-1.0 cap emits `OPEN`
- **Approval:**
  - `OPEN` and legacy `VACANT` entries become NULL-employee shifts
  - `CONFLICT` entries are still skipped
  - fairness minutes ignore open shifts
- **Editing approved shifts:**
  - `open: true` unassigns; with no `shift_id` it creates an open shift
  - a new shift sent without an offset is stored in the location's zone
  - `open` with `employee_id` returns 400
  - filling an open shift works and warns as usual
  - unassigning a checked-in shift returns 409
- **Readers:**
  - check-in never matches an open shift
  - payroll derivation skips open shifts
  - the 7shifts export skips them and reports `open_shifts_skipped`
  - JSONL includes them with a null employee
  - the week view returns `employee_name: null`
  - preference asterisks skip them
- **Candidates:**
  - each of the three tiers gets the right people, alphabetical within a
    tier, and nobody outside them is returned
  - a blackout makes someone unavailable, and so does an overlapping
    approved shift
  - employee-timezone conversion is applied
  - `exclude_shift_id` stops a shift clashing with itself
  - another company's employees never appear
  - another company's location returns 404
  - the query count is constant as the number of employees grows
- **Shared availability check:** the `no_availability` warning now fires on
  a blackout day.
- **Open-shift count:** only the 14-day window, only approved schedules, and
  only this company.

### Frontend

- The open card renders from a null employee and from legacy `"VACANT"`.
- The picker groups candidates by tier, and "Leave open" sends `open: true`.
- The banner is hidden at 0; the badge shows the count.
- Every new string exists in all 19 locales.

## Out of scope

- Email or push notifications for open shifts.
- Employees seeing or claiming open shifts.
- Sending open shifts to 7shifts.
- Archiving (#126). That work builds on this and takes the next migration
  number.
