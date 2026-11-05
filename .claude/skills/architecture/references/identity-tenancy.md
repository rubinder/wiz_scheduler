# Identity & Tenancy

Who the caller is, which tenant they act for, and which locations they may
touch. Most of this context is on the factory's protected-path list
(`routers/auth.py`, `dependencies.py`, `tenant.py`,
`services/location_scope.py`), so changes to it wait for the owner. The
`tenancy-security` gate reviews every router, service and model change
against it.

## Modules owned

- `backend/tenant.py`: the request's allowed company ids (context variable)
  and the explicit cross-tenant escape hatch for jobs.
- `backend/database.py`: the ORM tenant filter, Postgres row-level security
  wiring, and `cross_tenant_session` for the few legitimate cross-tenant
  request reads.
- `backend/dependencies.py`: `get_current_user` (JWT to `User`),
  `require_manager`, `require_admin`, `get_location_scope`,
  `get_ownership_group_company_ids`.
- `backend/services/location_scope.py`: `LocationScope`,
  `location_scope_for`, `require_employee_visible` / `_editable`,
  `visible_employee_ids`.
- `backend/services/manager_permissions.py`: admin vs regular managers,
  location assignment, `LastAdminError`.
- `backend/services/ownership.py`: `assert_owned` for ids in request bodies.
- `backend/services/email_verification.py`, `passwords.py`,
  `password_reset_email.py`, `email_verification_email.py`,
  `manager_invite_email.py`.
- Routers: `auth`, `invites`, `manager_invites`, `manager_locations`,
  `company`. Models: `user`, `company`, `manager_invite`, `manager_location`,
  `email_verification_token`, `password_reset_token`.
- Frontend: `frontend/src/api/auth.ts`, `googleAuth.ts`,
  `hooks/useAuth.tsx`, `hooks/useManagerPermissions.ts`,
  `utils/permissions.ts` (display only; the API is the security boundary).

## Invariants

- Every tenant query is scoped to the caller's company ids, taken from the
  JWT, never from the request body. Isolation is enforced twice, by the ORM
  filter and by Postgres RLS. Do not rely on only one.
- An id in a request body that names another tenant's row answers 404, the
  same as a missing row (`ownership.assert_owned`). A location outside a
  regular manager's scope also answers 404.
- Routers ask `LocationScope` whether a manager is restricted. They never
  read `manager_type` themselves. The company always keeps an admin.
- Any new path that creates a `User` sets `email_verified_at` when the flow
  already proves the address (invite link, Google `email_verified`).
  Otherwise it leaves the field NULL and calls `send_verification`. NULL
  blocks `POST /schedules/generate` (403 `email_not_verified`) and nothing
  else (`fail-closed`).

## Entry points for other contexts

`dependencies.get_current_user`, `require_manager`, `require_admin`,
`get_location_scope`; `location_scope.location_scope_for`,
`visible_employee_ids`; `ownership.assert_owned`;
`email_verification.assert_email_verified`, `send_verification`;
`manager_permissions.get_accessible_location_ids`, `is_admin_manager`.

## Do not

- Take `company_id` from a request body or query string.
- Add a new cross-tenant read outside the existing helpers. Each one is a
  `tenancy-security` finding that needs the owner.
- Add an endpoint without an auth dependency, unless it is public by design
  (`routers/public.py`, the Stripe webhook verified by signature).
- Decide "restricted manager" anywhere but `location_scope.py`.
- Put credentials, signing secrets or database role names in code, tests,
  commits or PR text. The repo is public.

## Tests that pin it

`tests/test_tenant_filter_integration.py`, `test_rls_enforce_integration.py`,
`test_body_id_ownership_integration.py`, `test_location_scope.py`,
`test_location_scope_admin_routes_integration.py`,
`test_location_scope_employee_routes_integration.py`,
`test_location_scope_schedule_routes_integration.py`,
`test_manager_type_integration.py`, `test_manager_admin_integration.py`,
`test_auth_integration.py`, `test_email_verification_integration.py`,
`test_password_reset_integration.py`, `test_manager_invites_integration.py`,
`test_users_fk_ondelete.py`, `test_db_tls.py`;
`frontend/src/utils/permissions.test.ts`,
`frontend/src/pages/manager/teamAccess.test.ts`.
