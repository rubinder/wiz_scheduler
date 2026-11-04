# Imports

The anti-corruption layer for 7shifts and Deputy: their companies,
locations, roles, employees and availability, translated into WizScheduler's
domain at the edge.

## Modules owned

- `backend/routers/import_7shifts.py`: `POST /import/7shifts`,
  `POST /import/7shifts/availabilities`, with the translation helpers
  `_import_single_company`, `_expand_7shifts_availability`,
  `_subtract_intervals`, `_time_str_to_minutes`.
- `backend/routers/import_deputy.py`: `POST /import/deputy`,
  `POST /import/deputy/availabilities`, with `_deputy_location_timezone`,
  `_deputy_origin`, `_invert_unavailability`.
- `backend/services/integration_import_quota.py`
  (`begin_integration_import`, the per-group burst and cooldown). Model
  `integration_import`.
- Frontend: `api/import7shifts.ts`, `api/importDeputy.ts`.

There is no import service layer. The translation lives in the two routers
(about 1,400 and 850 lines), which predate `thin-router`. New translation
code goes in a service module, such as `services/import_<vendor>.py`, and
the router calls it.

## Invariants

- `anti-corruption-layer`: vendor field names and shapes stop here. What
  leaves an importer is an `Employee`, `Location`, `Role` or availability
  row in domain terms. Deputy's zone resolution
  (`_deputy_location_timezone`) and 7shifts' availability expansion are the
  models to follow.
- `location-local-time`: imported availability is the location's wall
  clock. The importer resolves the location zone. It never converts per
  employee.
- `archive-never-delete`: importers match `external_id` against archived
  rows too. A returning person is restored (`archive.restore_*`), not
  duplicated. Rows missing upstream are archived (`archive.archive_*`),
  never deleted.
- Imports are paid-only (`plan.assert_paid_plan`), which is why they do not
  call `assert_can_add`. A free-plan import path would have to call it.
- Every run reserves a slot with `begin_integration_import` first
  (`fail-closed` on the cooldown).
- Schema changes must keep the 7shifts and Deputy compatibility tests green.

## Entry points for other contexts

None. Imports call Workforce (`archive.*`), Billing & Plans
(`plan.assert_paid_plan`, `billing.get_ownership_group_id`) and the quota
service. Nothing calls into Imports.

## Do not

- Let a vendor payload, or a field named after one, into a model, service
  or frontend type.
- Delete rows that disappeared upstream.
- Call a vendor API from anywhere but these modules.
- Log vendor API tokens or echo them in responses.

## Tests that pin it

`tests/test_schema_7shifts_compat.py`, `test_schema_deputy_compat.py`,
`test_deputy_location_timezone.py`, `test_deputy_origin.py`,
`test_import_archive_simulation_integration.py`,
`test_integration_import_quota_integration.py`.
