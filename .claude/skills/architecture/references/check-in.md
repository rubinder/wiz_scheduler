# Check-In

Employees scan a rotating QR at the location to record arrival against their
scheduled shift. Paid plans only.

## Modules owned

- `backend/services/check_in.py`: `issue_token`, `record_check_in`,
  `current_counter`, `local_date_for`, `CheckInRejected`. The whole decision
  table lives here.
- `backend/services/check_in_token.py`: `build_check_in_token`,
  `verify_check_in_token`, `check_in_deep_link`.
- Router `check_ins` (`GET /check-ins/qr`, `POST /check-ins`,
  `GET /check-ins/report`). Model `employee_check_in` (`EmployeeCheckIn`).
- Frontend: `pages/employee/CheckIn.tsx`, `pages/manager/CheckInQr.tsx`,
  `pages/manager/CheckInReport.tsx`, `api/checkIns.ts`.

## Invariants

- The QR payload is an HMAC over slug, location, local date and counter.
  The counter is that location's check-in count for the local day, so
  recording one rotates the code.
- Single use is enforced by the unique constraint on
  `(location_id, local_date, counter)`, not by application logic. Keep it
  that way: catch the `IntegrityError`, do not pre-check.
- `location-local-time`: "today" for a check-in is
  `local_date_for(location, at)`, never UTC.
- Identity comes from the JWT, never from the code. The router resolves the
  caller's active `Employee`.
- `thin-router`: the router authorizes and maps `CheckInRejected` to 409.
  The rules stay in the service.
- Paid-only through `plan.assert_paid_plan` (`fail-closed`). Archived
  locations reject check-ins. Rows are kept for `RETENTION_CHECKINS_DAYS`
  and then swept by `data_retention`.
- Time entries (Payroll) consume check-ins. Pay records do not reference
  them, because check-ins expire first.

## Entry points for other contexts

`check_in.local_date_for`, `record_check_in`, `issue_token`;
`archive.delete_past_check_ins` (owned by Workforce) for erasure.

## Do not

- Put the HMAC key, or anything derived from it, in a response, log or test
  fixture.
- Trust an employee or location id carried in the token over the JWT and
  the route.
- Compute the local day in UTC.

## Tests that pin it

`tests/test_check_in_service_integration.py`, `test_check_in_api_integration.py`,
`test_check_in_model_integration.py`, `test_check_in_token.py`,
`test_check_in_retention_integration.py`,
`test_check_in_report_attestation_integration.py`.
