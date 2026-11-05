+++
name = "tenancy-security"
[triggers]
app = ["backend/routers/**", "backend/services/**", "backend/models/**", "backend/models.py"]
[mechanical]
app = ["cd {worktree} && {py} -m pytest tests/test_tenant_filter_integration.py tests/test_rls_enforce_integration.py tests/test_body_id_ownership_integration.py tests/test_location_scope.py tests/test_location_scope_admin_routes_integration.py tests/test_location_scope_employee_routes_integration.py tests/test_location_scope_schedule_routes_integration.py -q"]
+++

## Reads

The diff and every router, service and model it touches, plus `backend/dependencies.py`, `backend/tenant.py` and `backend/services/location_scope.py` for the contracts.

## Checks

- `company_id` scoping on every query.
- Row-level security (RLS): no bypass of the tenant session; cross-tenant reads use `cross_tenant_session` deliberately.
- IDs in request bodies that belong to another tenant.
- An auth dependency on every new endpoint (`require_manager` or the employee ownership check).
- Location scope for regular managers.
- Secrets in code, logs or error messages.

Cite the architecture pattern id (fail-closed, thin-router, aggregate-root, ...) where relevant.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. The gate result carries only "security finding — see owner notification", and the owner is notified locally. Write the details to the local gate result and nowhere public.

## Pass

The mechanical tests exit 0 and no security finding survives the refute pass.
