# Workforce

The roster and what is true about each person: regions, locations, roles,
employees, availability, blackouts, preferences and affinities, and their
archive lifecycle.

## Modules owned

- `backend/services/archive.py`: `active`, `archive_employee`,
  `archive_location`, `archive_role`, `restore_employee`,
  `restore_location`, `restore_role`, `erase_employee`, `lock_employees`,
  `delete_past_check_ins`, `EmployeeErased`.
- `backend/services/availability_check.py`: `is_available`, `window_dict`,
  `blackout_dicts`. This is the one definition of "available".
- `backend/services/preference_loader.py` (`load_employee_preferences`),
  `backend/services/preference_submissions.py` (employee-submitted
  preferences and the manager review), `preference_decision_email.py`.
- Routers: `employees`, `roles`, `condensed_roles`, `regions`, `locations`,
  `affinities`, `scheduling_preferences`, `preference_submissions`.
- Models: `employee` (with availability and blackouts), `role`,
  `condensed_role`, `region`, `location`, `department`,
  `employee_role_minutes`, `preference_submission`.
- Frontend: `pages/manager/{Employees,Roles,RoleEquivalents,Regions,Locations,Team,EmployeeAvailability,DayBlackouts,DayPreferences,HourRangePreferences,HourRestrictions,FrequencyCaps,PreferenceReviews,QuickEntry}.tsx`,
  `pages/employee/{Availability,MyPreferences}.tsx`,
  `utils/archived.ts`, `utils/availabilityDates.ts`,
  `utils/preferenceSubmissions.ts`.

## Invariants

- `archive-never-delete`: only `services/archive.py` sets or clears
  `archived_at`. `active(Model)` is the one filter. Only
  `billing.delete_og_and_dependents` hard-deletes employees, locations or
  roles. Erasure (`erase_employee`) removes personal data but keeps the row,
  its `full_name`, and its pay history.
- Every reader chooses on purpose. Planning surfaces (scheduler, pickers,
  check-in, employee routes, plan counts) are active-only. History
  (approved schedules, exports, payroll, importer `external_id` matching)
  includes archived rows.
- `location-local-time`: availability, blackouts and preferences are the
  location's wall clock. Employees have no timezone (#171).
- `single-evaluator`: `availability_check.is_available` answers "can this
  employee work this span". The frontend never evaluates availability. It
  shows what `POST /schedules/candidates` and the edit warnings return.
- Roles are never hardcoded. No role name literal outside `backend/seed.py`.
- Creating an `Employee` or `Location` calls `plan.assert_can_add` first.

## Entry points for other contexts

`archive.active`, the `archive_*` / `restore_*` functions, `erase_employee`;
`availability_check.is_available`, `window_dict`, `blackout_dicts`;
`preference_loader.load_employee_preferences`.

## Do not

- `db.delete(employee)`, `delete(Location)` or `delete(Role)` anywhere.
- Write `x.archived_at = ...` outside `archive.py`
  (`archived-at-outside-archive`).
- Filter `archived_at.is_(None)` by hand. Use `active(Model)`.
- Convert availability per employee, or `.astimezone()` a stored
  availability value.
- Add an `isAvailable`-style helper in `frontend/src`
  (`frontend-availability-eval`).

## Tests that pin it

`tests/test_archive_service_integration.py`,
`test_archive_readers_integration.py`, `test_archive_endpoints_integration.py`,
`test_archive_review_fixes_integration.py`, `test_availability_check.py`,
`test_avail_local_wallclock.py`, `test_avail_tz.py`,
`test_availability_location_local_integration.py`,
`test_multi_location_availability.py`, `test_employees_integration.py`,
`test_roles_integration.py`, `test_locations_integration.py`,
`test_regions_integration.py`, `test_affinity_validation.py`,
`test_preference_submissions_*_integration.py`,
`test_preference_persistence_integration.py`;
`frontend/src/utils/archived.test.ts`, `availabilityDates.test.ts`,
`preferenceSubmissions.test.ts`, `frontend/src/architecture.test.ts`.
