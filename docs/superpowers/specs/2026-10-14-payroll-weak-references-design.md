# Self-contained payroll records: weak references from time entries

**Date:** 2026-10-14
**Issue:** #126 (shift deletion vs. foreign keys)
**Branch:** `fix/shift-delete-fks`
**Status:** Design agreed in conversation; spec awaiting review

## Goal

A pay record (`time_entries` row) must survive whatever happens to the rows it
mentions: an employee, role, location or shift being deleted, or re-created
under a new id by a re-import. It must never block those deletes with a
foreign-key 500. Pay records are kept for the legal retention period (up to 7
years), even through GDPR account deletion.

### Background

- **Foreign keys.** No foreign key into `shifts`, `employees`, `roles`,
  `locations`, `employee_check_ins`, `payroll_exports` or `users` from the
  payroll and check-in tables has an `ondelete`. On PostgreSQL, deleting a
  referenced row fails with an opaque 500.
- **Tests.** Integration tests run on Postgres since #167, so this is
  testable.
- **What breaks today:**
  - The manager employee delete, GDPR delete, and the 7shifts/Deputy employee
    removal all fail for an employee with any check-in or time entry.
  - The 7shifts role/location removal fails once payroll exists.
  - Deleting an approved shift fails if it has a check-in.
- **`time_entries` today:**
  - Hard foreign keys to `employees`, `shifts`, `roles`, `locations`,
    `employee_check_ins`, `payroll_exports` and `users` (approved_by,
    attested_by).
  - It snapshots `role_name` only.
  - Payroll export (`payroll_export.py:143`) and the time-entry list
    (`time_entries.py:392`) inner-join `Employee` and `Location` live for the
    name and timezone. A deleted employee or location would therefore drop
    its pay rows out of both.

## 1. Data model (migration 0047)

### `time_entries`: weak references plus snapshots

- **Drop the foreign keys** on `employee_id`, `shift_id`, `role_id` and
  `location_id`. They stay `NOT NULL` plain columns: a record of the ids at
  the time. Keep the existing indexes and unique constraint
  (`uq_time_entries_shift`).
- **Add snapshot columns:**
  - `employee_name String NOT NULL`
  - `location_name String NOT NULL`
  - `location_timezone String(64) NOT NULL`

  Together with the existing `role_name`, the row is self-describing.
- **Backfill the snapshots** in the migration from the current
  `employees`/`locations` rows. Every existing row still has both, because
  today's foreign keys guarantee it.
- **`SET NULL`:**
  - `check_in_id`
  - `payroll_export_id`
  - `approved_by_user_id`
  - `attested_by_user_id`

  A pay record stays valid without them.

### Other tables

- **`payroll_exports.exported_by_user_id`:** make it nullable, with
  `ON DELETE SET NULL`.
- **`employee_check_ins`:** attendance logs, which are retention-swept and
  are not pay records.
  - `shift_id`: `ON DELETE SET NULL`.
  - `employee_id` and `location_id`: `ON DELETE CASCADE`. A check-in goes
    with its employee or location.

## 2. Writers and readers

- **Derivation** (`time_entries.py`, where entries are created from shifts)
  fills `employee_name`, `location_name` and `location_timezone` from the
  rows it already loads.
- **Readers stop joining live:**
  - The payroll export and the time-entry list read the snapshot columns
    instead of joining `Employee` and `Location`.
  - The attester's name stays an outer join on `users`; it is NULL once that
    user is gone.
  - Sorting by employee uses `employee_name`.
- **Company and location filters** keep working on the plain `company_id` and
  `location_id` columns.

## 3. Delete paths

- **Manager employee delete, the 7shifts/Deputy employee removal and the
  7shifts role/location removal.** They work unchanged: payroll no longer
  blocks them, and check-ins cascade. Their pay rows remain with the snapshot
  names.
- **Approved-shift delete** (`PUT /schedules/{id}/approved-shifts`):
  - A shift with a check-in now deletes, and the check-in's `shift_id`
    becomes NULL.
  - The existing **409 for a shift with a time entry stays**. Removing a
    shift that was already paid is far more likely a mistake than intent,
    even though the database would now allow it.
- **Re-import with new ids.** A role, location or employee re-created by an
  import gets a new id. Old pay rows keep the old id and the snapshot names,
  and new pay rows use the new id. Payroll reports are unaffected, because
  they read snapshots.
- **Only the existing company/ownership-group wipe** in `services/billing.py`
  deletes pay records. No other path in this change does.

## 4. GDPR account deletion: pay records are retained

Employers must keep pay records for up to 7 years, and that legal obligation
overrides an erasure request (GDPR Art. 17(3)(b)). WizScheduler processes the
records on the employer's behalf, so the employer decides when they go, not
the employee.

`delete_account` (`routers/gdpr.py`):

- **Erases everything else:** the user, consents, invites, availability,
  blackouts, roles, affinities, preferences, shifts, check-ins (by cascade)
  and the employee row.
- **Keeps the person's `time_entries`.** Thanks to the weak references they
  survive the employee row, and they carry only what payroll needs:
  - ids
  - `employee_name`
  - location and role names
  - times and minutes
- **Manager columns.** Rows where the user only appears as a manager
  (approved_by, attested_by, exported_by) have those columns set to NULL by
  the new foreign keys.

Deleting retained pay rows after the retention period is a separate issue (a
`RETENTION_TIME_ENTRIES_DAYS` sweep, a DPA clause and a full-history export).
It is out of scope here.

## 5. Testing

Integration tests run on Postgres with foreign keys enforced.

- **Delete paths, each with a check-in and a time entry attached:**
  - Manager employee delete.
  - 7shifts employee removal, 7shifts role removal and 7shifts location
    removal.
  - Deputy employee removal.

  For each: no 500, the pay row still exists with its snapshot names, and the
  check-ins are gone.
- **Approved-shift delete.** A shift with a check-in deletes, and the
  check-in's `shift_id` is NULL. A shift with a time entry still returns 409.
- **Payroll readers.** After the employee and location are deleted, the
  payroll export and the time-entry list still include the row with the
  correct name, location name, and local times from `location_timezone`.
- **Re-import.** The employee is re-created with a new id. The old pay row
  keeps its snapshot, and new entries use the new id.
- **GDPR:**
  - An employee with time entries and check-ins: everything is gone except
    the time entries, which remain with their snapshot names.
  - A manager who approved, attested or exported: the account is deleted,
    those columns are NULL, and the pay rows remain.
- **Migration.** The backfill fills the snapshot columns for existing rows,
  and the `migration` CI job runs upgrade and downgrade on Postgres.

## Out of scope

- The 7-year retention sweep, the DPA clause and a full-history export
  (separate issue).

- Archiving or soft-delete of any kind.
- Manager **role/location delete endpoints** (`routers/roles.py`,
  `routers/locations.py`). They still fail on non-payroll references (shifts,
  schedules, templates). That is a separate, pre-existing problem; file it as
  its own issue.
- The employee-timezone import gap (#171).
