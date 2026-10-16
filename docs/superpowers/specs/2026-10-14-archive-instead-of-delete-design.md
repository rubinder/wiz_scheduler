# Archive instead of delete: keep approved schedules and payroll

**Date:** 2026-10-14 (revised 2026-10-15, 2026-10-16)
**Issue:** #126 (deletion vs. foreign keys)
**Builds on:** #174 (open shifts, PR #175)
**Branch:** `fix/archive-not-delete`, stacked on `feat/open-shifts`
**Status:** Design agreed in conversation; revised spec awaiting review

## Goal

Removing an employee, location or role must never:

- fail with a foreign-key 500, or
- destroy past approved schedules or pay records.

Pay records are kept for the legal retention period (up to 7 years), even
through GDPR account deletion.

## Background

- **Foreign keys.** The payroll and check-in tables reference `employees`,
  `locations`, `roles` and `shifts` with no `ondelete`. On PostgreSQL,
  deleting a referenced row fails with an opaque 500. Integration tests run on
  Postgres since #167, so this is testable.
- **What breaks today:**
  - The manager employee delete, GDPR delete and the 7shifts/Deputy employee
    removal fail for an employee with any check-in or time entry.
  - The 7shifts location removal fails once the location has a time entry,
    check-in, payroll export or special-hours day. The 7shifts role removal
    fails once the role has a time entry.
  - The manager role and location deletes (`routers/roles.py`,
    `routers/locations.py`) fail as soon as anything references the row.
- **What is destroyed today, even when the delete succeeds:**
  - The manager employee delete (`routers/employees.py:305`), GDPR delete and
    both importers delete every shift of the employee, including past
    approved ones.
  - The 7shifts location removal (`import_7shifts.py:239`) deletes the
    location's schedules and shifts outright.
- **Already guarded:** editing or deleting an approved shift that has a
  check-in returns 409 `shift_locked_by_checkin`, and one with a time entry
  returns 409 `shift_has_time_entry`. Both stay.

## Decision: archive, don't delete

Employees, locations and roles are **archived** rather than deleted. The row
stays, so every approved shift, schedule, check-in and pay record that points
at it stays intact and keeps displaying the right name. Only the ownership
group wipe (`services/billing.delete_ownership_group`) still hard-deletes, and
it already deletes the dependent rows in order.

Because these rows are no longer deleted, their foreign keys can stay. The
weak-reference design in the earlier draft of this spec is no longer needed.

## 1. Data model (migration 0048)

### Archive columns

- `employees.archived_at`, `locations.archived_at`, `roles.archived_at`:
  `DateTime(timezone=True)`, NULL = active.
- `employees.erased_at`: `DateTime(timezone=True)`, set by GDPR deletion
  (§5). An erased employee can never be restored.

### Pay records carry their own names

Pay records keep the names **as they were when the time was worked**, so a
later rename, archive or erasure never changes pay history.

- `time_entries` gains:
  - `employee_name String NOT NULL`
  - `location_name String NOT NULL`
  - `location_timezone String(64) NOT NULL`

  These sit alongside the existing `role_name`. The foreign keys on
  `employee_id`, `location_id`, `role_id` and `shift_id` stay.
  `shift_id` stays RESTRICT: a pay record must never vanish silently, and the
  409 stays.
- The migration backfills the new columns from the current `employees` and
  `locations` rows.
- `payroll_exports` gains `location_name String NULL`, backfilled for
  single-location exports. NULL `location_id` still means every location.

### `SET NULL` where the referenced row can genuinely go

- `employee_check_ins.shift_id`
- `time_entries.check_in_id` and `time_entries.payroll_export_id` (the
  retention sweeps already null them in code)
- `time_entries.approved_by_user_id` and `time_entries.attested_by_user_id`
- `payroll_exports.exported_by_user_id`, which becomes nullable

The user columns matter because GDPR deletion removes the manager's `users`
row.

## 2. Archiving

### Endpoints

- `DELETE /employees/{id}`, `/locations/{id}` and `/roles/{id}` archive
  instead of deleting. The UI labels the action "Archive".
- `POST /employees/{id}/restore`, `/locations/{id}/restore` and
  `/roles/{id}/restore` clear `archived_at`.
  - Restoring an employee or location calls `assert_can_add` first.
  - Restoring an erased employee returns 409.

### Effects of archiving an employee

- **Past approved shifts** are kept unchanged.
- **Future approved shifts** (`start_time > now`) become open shifts
  (`employee_id = NULL`, per #174).
  - Shifts with a check-in or time entry are never touched.
- **Kept for restore:** roles, availability, preferences, blackouts and
  affinities.
- **Pending invites** are revoked.
- **Login:** the employee's user can still sign in, but the employee-facing
  routes treat an archived employee as having no employee record.

### Effects of archiving a location

- Past approved schedules and shifts are kept.
- Future approved shifts at the location are deleted: the location is
  closing, so there is nothing to refill.
- Draft (unapproved) schedules for the location are deleted.
- Kept for restore: shift templates, special hours and departments.
- Check-in at the location is refused.

### Effects of archiving a role

- Past approved shifts are kept.
- Future approved shifts with the role become open shifts.
- `employee_roles` rows are kept for restore.
- The scheduler and the template editor ignore the role.

### Who sees archived rows

**Active only:**

- the scheduler (`scheduling/graph.py`, `nodes.py`, `local_scheduler.py`)
- manager lists and pickers (the employee picker never offers an archived
  employee)
- check-in
- invites, affinities and special hours
- the employee-facing routes
- edit warnings
- the open-shift candidate picker (`services/candidates.py`): an archived
  employee is never offered, and an archived role gives no tier credit
- assigning a shift in the approved-schedule edit path: assigning an
  archived employee returns 409 `employee_archived`
- approval of a draft: an entry for an employee archived since generation is
  written as an open shift
- plan limit counts (`count_employees_for_group`,
  `count_locations_for_group`)

**Everything, archived included:**

- approved-schedule views and exports
- payroll and time entries
- importers' `external_id` matching

The manager Employees, Locations and Roles pages get a "Show archived"
toggle with a Restore button.

One helper (for example `active(Employee)`) adds the
`archived_at IS NULL` filter, so readers don't hand-write it. The
implementation plan lists every query site in the files that select these
models.

## 3. Importers (7shifts and Deputy)

- **Removed upstream:** a row whose `external_id` is no longer returned is
  archived, not deleted. The removal blocks
  (`import_7shifts.py:236-475`, `import_deputy.py:~407`) are replaced by the
  archive service, so imports stop deleting shifts, schedules, templates and
  departments.
- **Back upstream:** matching by `external_id` includes archived rows.
  - A match that is archived is restored.
  - A match that is erased is skipped and a new row is created.
- **Counts:** `ImportResult` reports `archived` and `restored` in place of
  `deleted`. The frontend summary shows both.

## 4. Readers and writers of pay records

- **Writing:** time-entry derivation (`services/time_entries.py`) fills
  `employee_name`, `location_name` and `location_timezone` from the rows it
  already loads.
- **Reading:** the payroll export (`services/payroll_export.py:145`) and the
  time-entry list read those columns instead of joining `Employee` and
  `Location` live. Company and location filters keep using the id columns.
- **Sorting:** sorting by employee uses `employee_name`.

## 5. GDPR account deletion

Employers must keep pay records for up to 7 years. That legal obligation
overrides an erasure request (GDPR Art. 17(3)(b)), and schedule records are
often subject to the same kind of obligation (for example NYC Fair
Workweek). WizScheduler processes these records on the employer's behalf.

`delete_account` (`routers/gdpr.py`):

- **The employee row is archived and erased, not deleted:**
  - set `archived_at` and `erased_at`
  - clear `email`, `user_id`, `external_id`, `timezone`, `pay_rate`,
    `hire_date`, `seniority_rank` and `location_ids`
  - keep `full_name`, so past schedules and pay records stay readable
- **Deleted:**
  - consents, invites, availability, blackouts, preferences, caps
  - roles, role minutes, affinities, `employee_company` rows
  - check-ins (attendance logs, not pay records), deleted explicitly because
    the employee row stays
  - the `users` row
- **Shifts:** future approved shifts become open shifts. Past approved shifts
  are kept.
- **Pay records** (`time_entries`) are kept.
- **Manager references:** where the user only appears as a manager
  (approved_by, attested_by, exported_by), the new `SET NULL` foreign keys
  null those columns.

Deleting retained records after the retention period is a separate issue
(a `RETENTION_TIME_ENTRIES_DAYS` sweep, a DPA clause and a full-history
export).

## 6. Order of work

#174 (open shifts) is built (PR #175). This branch is stacked on
`feat/open-shifts` and its PR targets that branch until #175 merges, then
retargets `main`. Open shifts are a `Shift` with `employee_id IS NULL`, so
"becomes an open shift" means nulling `employee_id` on the row and leaving
its time, role and location as they were.

## 7. Testing

Integration tests run on Postgres with foreign keys enforced.

- **Import simulation (written first, failing on today's code):** an
  end-to-end test drives the real `POST /import/7shifts` and
  `POST /import/deputy` routes against canned upstream JSON. The router's
  `httpx.AsyncClient` is replaced, via monkeypatch, with a client on an
  `httpx.MockTransport` that answers each 7shifts/Deputy URL from a fixture
  dict (httpx ships `MockTransport`; no new dependency). Scenario:
  1. First import: one company, two locations, two roles, two employees.
  2. Attach a past approved shift, a check-in, a time entry, a payroll
     export and a special-hours day to employee A and location X, plus a
     future approved shift for A.
  3. Re-import with A, X and one role missing upstream.
  4. Assert: 200 (today: 500 on the foreign keys); A, X and the role are
     archived, not deleted; the past shift and pay rows are unchanged; A's
     future shift is open; `archived` counts are reported.
  5. Re-import with A back upstream: A is restored by `external_id`, with no
     duplicate row, and `restored` is reported.

  A shared fake-upstream helper (`tests/fake_upstream.py`) holds the
  transport, so later importer tests reuse it instead of creating model
  rows directly the way `test_schema_*_compat.py` does.

- **Archive paths:** manager employee/location/role archive, 7shifts
  employee/location/role removal, Deputy employee removal. Each runs with a
  check-in, a time entry, a payroll export and a special-hours day attached.
  For each:
  - no 500
  - past approved shifts are unchanged
  - pay rows are unchanged
  - future shifts are open (or deleted, for a location)
- **Readers:**
  - archived rows are absent from the scheduler input, pickers, check-in and
    plan counts
  - archived rows are present in approved schedules, exports and payroll
- **Restore:**
  - the manager restore respects `assert_can_add`
  - a re-import restores by `external_id` without creating a duplicate
  - an erased employee cannot be restored
- **Payroll:**
  - after a rename, the export and the time-entry list still show the
    original name, location name and local times
- **GDPR:**
  - employee: personal fields cleared, past shifts and pay rows remain,
    check-ins gone, restore refused
  - manager who approved, attested or exported: the account is deleted,
    those columns are NULL, and the pay rows remain
- **Migration:** backfill, upgrade and downgrade on Postgres.

## Out of scope

- The 7-year retention sweep, the DPA clause and a full-history export
  (separate issue).
- Open shifts themselves: persistence, labels, alerts and the ranked picker
  (#174).
- The employee-timezone import gap (#171).
