# Payroll

Payable time derived from approved shifts and check-ins, the CSV exports,
retention of pay records, and the paid payroll archive.
`services/payroll_archive.py` is a protected path.

## Modules owned

- `backend/services/time_entries.py`: `derive_time_entries`,
  `list_time_entries`, `list_exceptions`, `attest_shift`, `approve_entries`,
  `attestation_rates`, and the pure `paid_minutes` / `pay_date_for`.
- `backend/services/payroll_export.py`: `export_approved`, `export_history`,
  `write_history_csv`, `render_csv`.
- `backend/services/payroll_archive.py`: offer, purchase
  (`verify_archive_session`, `record_archive_purchase`), write, and
  retrieval tokens. `backend/services/archive_storage.py` holds the S3
  storage behind a `Protocol`.
- `backend/services/data_retention.py`: `time_entry_cutoff`,
  `payroll_retention_info`, `delete_expired_time_entries`, and the monthly
  `run_data_retention` sweep. The sweep also deletes or clears other
  contexts' rows: rejected and stale draft schedules, old availability,
  check-ins, expired invites, revoked consents, failure logs, signup
  signals and payroll export logs. A change there touches Scheduling,
  Workforce, Check-In, Billing and Legal too.
- Routers: `payroll`, `payroll_archive`. Models: `time_entry`,
  `payroll_export`, `payroll_archive`.
- Frontend: `pages/manager/Payroll.tsx`, `pages/PayrollArchiveRetrieve.tsx`,
  `pages/PayrollArchiveDownload.tsx`, `hooks/usePayrollArchiveReturn.ts`,
  `utils/payrollHours.ts`, `utils/payrollRetention.ts`,
  `utils/payrollArchive.ts`.

## Invariants

- `location-local-time`: pay dates and times are the location's zone
  (`pay_date_for(timezone_name, start)`). Pure functions take primitives,
  so their tests check the rule and not the SQLite driver, which drops
  tzinfo.
- Pay records carry their own employee and location names, so archiving or
  erasing an employee never blanks history (`archive-never-delete`).
  Payroll reads include archived rows.
- Pay records are kept for the retention period
  (`RETENTION_TIME_ENTRIES_DAYS`). Admins are warned before a sweep deletes
  them.
- `fail-closed`: a paid archive is written before a cancelled group is
  deleted, whatever `PAYROLL_ARCHIVE_ENABLED` says. A Checkout session counts
  only if `verify_archive_session` accepts it. Archive purchases never go
  through `charge_saved_card`, which is for AI credits only.
- A duplicate archive payment is refunded with `stripe.Refund.create` in
  `payroll_archive._refund`. This is the one allowlisted
  `stripe-outside-billing` violation in `tests/architecture_allowlist.json`.
  It awaits a move into `services/billing*`. Do not add a second.
- History export access is gated by `plan.assert_payroll_history_access`.

## Entry points for other contexts

`time_entries.derive_time_entries`, `approve_entries`;
`payroll_export.export_approved`, `export_history`;
`data_retention.payroll_retention_info`, `run_data_retention` (cron only);
`payroll_archive.write_archive` (called from the deletion lifecycle).

## Do not

- Join live `employees.full_name` into an export instead of the snapshot.
- Delete time entries outside the retention sweep.
- Derive a pay date from UTC or the server clock.
- Add Stripe calls here. Route new money movement through Billing.

## Tests that pin it

`tests/test_time_entries_service.py`, `test_time_entries_derive_integration.py`,
`test_time_entry_model_integration.py`, `test_payroll_api_integration.py`,
`test_payroll_attest_integration.py`, `test_payroll_end_to_end_integration.py`,
`test_payroll_export_csv.py`, `test_payroll_retention_integration.py`,
`test_payroll_archive_integration.py`, `test_pay_record_snapshots_integration.py`,
`test_export_location_times_integration.py`;
`frontend/src/utils/payrollHours.test.ts`, `payrollRetention.test.ts`,
`payrollArchive.test.ts`.
