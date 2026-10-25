# RLS migration (0044) failure — findings and fix plan

**Date:** 2026-10-14
**Context:** Issue #125 part 2 (PR #165, Postgres Row-Level Security), hotfix #166
**Status:** Phase 1 implemented on `fix/rls-migration-unblock`; Phase 2 is a proposal

## What happened

The `deploy-backend` job for `cad56b03` (run 38562988068) failed with
"Deployment … not found after stabilization … rolled back by the deployment
circuit breaker".

- Each new task (task definition `:125`) runs `alembic upgrade head` before
  uvicorn. Migration `0044_add_rls_policies.py:120` raised
  `relation "ownership_group_employees" does not exist`, the container exited 1,
  ECS retried, and the circuit breaker rolled back to `:123` (2026-10-12).
- Production is unaffected. Alembic runs the whole upgrade in one transaction, so
  every attempt rolled back; each retry logged `Running upgrade 0042 -> 0043`
  again.

## Production database state (inspected 2026-10-14)

Read-only one-off ECS task on task definition `:123` (command override,
`READ ONLY` transaction):

| Fact | Value |
|---|---|
| Engine | RDS PostgreSQL 15.17, `db.t3.micro`, single-AZ, `default.postgres15` |
| Alembic version | `0042` (0043 `feedback_submissions` not applied either) |
| Tables / FKs | 43 tables, 87 FKs; matches the ORM of the running image exactly |
| RLS | Not enabled anywhere; 0 policies; `app_role`/`analytics_role`/`admin_role` do not exist |
| Connecting user | the RDS master user — owns every table, `rds_superuser`, `rolsuper=false`, **`rolbypassrls=false`** |

## Defects in 0044 / 0045

1. **Hand-typed table list.**
   - Nonexistent: `ownership_group_employees`, `affinities`,
     `scheduling_preferences`, `special_hours`, `invoice_items`. 0045 also
     references `ownership_group_employees`.
   - Exists without `company_id` (the policy would fail next): `storage_snapshots`.
   - Tenant tables missing from the list: `condensed_roles`, `departments`,
     `employee_affinities`, `employee_check_ins`, `employee_companies`,
     `employee_day_blackouts`, `employee_day_preferences`,
     `employee_hour_range_caps`, `employee_hour_range_preferences`,
     `employee_role_minutes`, `employee_roles`, `payroll_exports`,
     `schedule_locks`, `special_hours_days`, `user_consents`, `users`.
2. **`FORCE ROW LEVEL SECURITY` would lock the app out.** The app still connects as
   the table owner (`DATABASE_URL` → the RDS master user), and there is no policy for
   that role. Reproduced on Postgres 16: the owner under FORCE reads `count = 0`
   and inserts fail with `new row violates row-level security policy`. Fixing
   only the table names would have turned this deploy failure into an outage.
3. **Company-ID format mismatch.** `dependencies._set_rls_context` stores JSON
   (`["a","b"]`) via `set_config`, but the policy casts it to `text[]`.
   Reproduced: `malformed array literal: "["a","b"]"`. Every query would fail
   once the app connects as `app_role`.
4. **Session-level setting.** `set_config(..., false)` persists on a pooled
   connection, so the next checkout inherits the previous tenant's IDs. The
   broad `except Exception` also hides a failed `set_config`, which leaves the
   transaction aborted.
5. **0045 would have failed next.** It creates `ix_<table>_company_id` on tables
   that already have an index leading on `company_id` (for example
   `ix_employees_company_id` from the model). At 0042, every tenant table except
   `user_consents` already has one.
6. **CI did not catch it.** `test (migration)` only checks the revision chain. It
   never runs `upgrade head` against Postgres.

## Constraint: BYPASSRLS is not available

- PostgreSQL 15 `CREATE ROLE`: "You must be a superuser to create a new role
  having the BYPASSRLS attribute." RDS never grants true superuser.
- PostgreSQL 16+: "Only superuser roles or roles with BYPASSRLS can specify
  BYPASSRLS." The master user lacks it, so a major upgrade does not help.
- So any "sees everything" role (service/admin/analytics) needs explicit
  `USING (true)` policies.

## Plan

### Phase 1 — unblock deploys (hotfix)

Goal: 0043–0045 apply cleanly with **no change to what the running app sees**.

1. Derive the table list at migration time, in both upgrade and downgrade, and
   in 0045:
   ```sql
   SELECT table_name FROM information_schema.columns
   WHERE table_schema = 'public' AND column_name = 'company_id'
   ```
2. Use `ENABLE ROW LEVEL SECURITY` only, **not `FORCE`**. The owner keeps
   bypassing RLS, so the app is unaffected until it moves to `app_role`.
3. Make policy creation idempotent (`DROP POLICY IF EXISTS` then `CREATE
   POLICY`). Keep role creation guarded.
4. 0045: create an index only where none leads on `company_id`, named
   `ix_rls_<table>_company_id`, so downgrade drops only what it created.
5. CI: run `alembic upgrade head` (and `downgrade`) against a testcontainers
   Postgres in the migration job. Add a test asserting every `company_id` table
   has RLS enabled and the expected policies.

### Phase 2 — enforce RLS (separate PR, needs design sign-off)

6. Roles, all via policies:
   - `app_role`: tenant policy `company_id = ANY(<ids>)`, for authenticated requests.
   - `service_role`: `USING (true) WITH CHECK (true)`. Used by background loops in
     `main.py`, Stripe webhooks, `public` router, login/registration/invite
     acceptance/password reset, `run_abuse_report.py`, data retention.
   - `admin_role` / `analytics_role`: `USING (true)`, limited by grants
     (analytics `SELECT` only). Only create them if a person or tool uses them.
   - `ALTER DEFAULT PRIVILEGES` so future tables get grants automatically.
7. Fix the ID format: store a Postgres array literal (`{a,b}`), or have the
   policy parse JSON.
8. Make the setting transaction-local (`set_config(..., true)`) per request, or
   reset it on connection checkout. Don't swallow `set_config` failures.
9. Two engines: `app_role` for `get_db` on authenticated routes, `service_role`
   for everything else, decided per code path. Watch pre-auth paths: `users` and
   `employee_companies` have `company_id`, so login under `app_role` would find
   no user.
10. Only then consider `FORCE`, once nothing but Alembic connects as the owner.

### Rejected alternatives

- **Fix only the five table names:** the migration then succeeds and the app
  sees empty tables (defect 2).
- **Upgrade Postgres for BYPASSRLS:** 16+ still requires the granting role to
  have it.
- **Run service code as the master user permanently:** works without FORCE, but
  gives background jobs owner/DDL rights. Acceptable only as a temporary step.

## Related finding — naive shift timestamps (open)

`tests/test_availability_holds_integration.py` (3 tests) passes only on a
US-Eastern machine and fails in CI (`TZ=UTC`); reproduce with
`TZ=UTC pytest tests/test_availability_holds_integration.py`.

- `routers/schedules.py` (approve, ~line 734) does
  `datetime.fromisoformat(s["start_time"])`. A naive value goes to asyncpg,
  whose timestamptz encoder calls `obj.astimezone(utc)`, and Python treats a
  naive datetime as the **host's local time**.
- The fixture's naive `"T13:00:00"` is stored as 17:00Z on EDT and 13:00Z on UTC.
  `_shift_local_face` then reads 13:00 vs 09:00 New York time.
- **Still to determine:** whether the AI path can hand approve naive times in
  production. `local_scheduler.py` emits aware times with the location offset.
  If the AI path can, production (UTC) stores location wall-clock as UTC, a
  4–5 h shift error. Fix direction: in approve, attach the location's
  `ZoneInfo` to naive values (per CLAUDE.md, derive offsets from
  `location.timezone`), and make the fixture use aware times.

## How to re-inspect production

See the memory note `reference_prod_db_inspect_ecs.md`: `aws ecs run-task` on
the service's task definition with a `command` override (migrations live in
Dockerfile `CMD`, so they don't run), network config copied from
`describe-services`, output read from `/ecs/wizscheduler` stream
`ecs/wizscheduler/<task-id>`.
