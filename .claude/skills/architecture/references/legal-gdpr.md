# Legal/GDPR

Data-subject rights (export, erasure, consent) and the published legal text.
`backend/legal/**` and `backend/routers/gdpr.py` are protected paths, and
legal text changes always wait for the owner and legal review.

## Modules owned

- `backend/legal/`: `privacy-policy.txt`, `terms.txt`, `dpa.txt`,
  `dpa-processors.json`. Text only, served verbatim.
- `backend/routers/gdpr.py`: `GET /gdpr/export`, `DELETE /gdpr/delete-account`,
  `GET /gdpr/consents`, `POST /gdpr/consents/revoke`, and the three legal
  text routes. There is no `services/gdpr.py`. The logic lives in this
  router, which predates `thin-router`.
- Models: `consent` (`UserConsent`), `gdpr_export_log`.
- Frontend: `pages/employee/DataPrivacy.tsx`, `pages/manager/DataPrivacy.tsx`,
  `api/gdpr.ts`.

## Invariants

- Erasure keeps the employee row (`archive.erase_employee`). Personal data
  goes. The row, its `full_name` and its pay history stay, because the
  employer must keep them (`archive-never-delete`). The `User` row and its
  consents are deleted.
- A company always keeps an admin. The last admin promotes another manager
  before erasing their own account (`manager_permissions.lock_company_admin_ids`).
- Legal records fail closed (`fail-closed`). A retention sweep, export or
  erasure that is unsure leaves the record in place. Revoking a consent sets
  `revoked_at` and keeps the row. The retention sweep deletes revoked
  consents only after their retention period. Active consents go only with
  the user.
- Legal text is read from `backend/legal/` and not edited in code.

## Entry points for other contexts

The HTTP routes above. Erasure itself is `archive.erase_employee` (Workforce).
The retention sweep is `data_retention.run_data_retention` (see `payroll.md`).

## Do not

- Hard-delete an `Employee` on account deletion.
- Change policy wording in a feature PR, or inline legal text in the
  frontend.
- Add personal data to an export from another tenant, or to a log.
- Grow `gdpr.py` with new rules. New logic goes in a service.

## Tests that pin it

`tests/test_gdpr_integration.py`, `test_gdpr_archive_integration.py`,
`test_archive_service_integration.py` (erasure),
`test_manager_admin_integration.py` (last admin).
