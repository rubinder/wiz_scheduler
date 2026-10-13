# Part 2: Postgres Row-Level Security (Defense in Depth)

## Overview
Defense-in-depth security layer: after Part 1's application-level ORM filtering, add Postgres RLS policies to catch raw SQL, ORM bypasses, and script access.

**Status**: Planning  
**Prerequisites**: Part 1 merged (#164), database role split not yet done

---

## Role Structure

### 1. **app_role** (Application)
- **Permissions**: SELECT, INSERT, UPDATE, DELETE on all tenant tables
- **RLS Policy**: Filtered by `current_setting('app.company_ids')`
- **Used by**: FastAPI application (ECS Fargate workers)
- **Set by**: `get_db()` immediately after connection checkout

### 2. **alembic_role** (Migration)
- **Permissions**: ALL (owner equivalent for migrations)
- **RLS Policy**: None (owner, bypasses RLS)
- **Used by**: Alembic schema migrations
- **Note**: Stays as current master user or becomes dedicated owner

### 3. **analytics_role** (Optional - Reporting)
- **Permissions**: SELECT only
- **RLS Policy**: Allows SELECT across all company_ids (or filtered by analytics scope)
- **Used by**: Scheduled reports, dashboards, billing calculations
- **Policy Design**:
  ```sql
  CREATE POLICY analytics_access
    ON <table>
    FOR SELECT
    USING (true);  -- see all data
  ```

### 4. **admin_role** (Optional - Support/Debug)
- **Permissions**: SELECT, UPDATE on most tables
- **RLS Policy**: Same as analytics (full access)
- **Used by**: Support staff, manual fixes, audits
- **Policy Design**: Same as analytics
- **Access Control**: SSH to bastion, then RDS proxy, then admin role

---

## Implementation Steps

### Phase 1: Preparation (TF + Schema)
1. **Terraform changes** (`terraform/rds.tf`, `secrets.tf`, `ecs.tf`):
   - Create `app_role`, `analytics_role`, `admin_role` in RDS
   - Grant appropriate permissions to each
   - Create secrets in Secrets Manager for `app_role` and `analytics_role`
   - Update ECS task definition to use `app_role` secret

2. **Alembic migration** (e.g., `0044_add_rls_policies.py`):
   - Run as owner/alembic_role (requires manual apply or existing owner connection)
   - For each table with `company_id`:
     ```sql
     ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
     ALTER TABLE <table> FORCE ROW LEVEL SECURITY;  -- force for owner too
     
     CREATE POLICY tenant_isolation_<table>
       ON <table>
       FOR ALL
       USING (company_id = ANY(current_setting('app.company_ids', true)::text[]))
       WITH CHECK (company_id = ANY(current_setting('app.company_ids', true)::text[]));
     ```

### Phase 2: Application Integration
1. **Update `backend/database.py`**:
   - After `async_session_factory().begin()`, execute:
     ```python
     await db.execute(text(
       f"SET LOCAL app.company_ids = '{json.dumps(company_ids)}'"
     ))
     ```
   - Happens right after `get_db()` connection checkout, before any query

2. **Background jobs** (data_retention.py, main.py loops):
   - `tenant_scope_disabled()` no longer needed at app level
   - Instead, jobs connect as `analytics_role` or `app_role` with appropriate company_ids set
   - OR stay as `tenant_scope_disabled()` + admin_role

3. **Scripts** (run_retention.py, run_abuse_report.py):
   - Connect as `admin_role` or `analytics_role` with RLS intact
   - Set company_ids appropriately or leave unset for full access (if policy allows)

### Phase 3: Testing
1. **Integration tests** (against PostgreSQL, not SQLite):
   - Connect as `app_role`
   - Set company_ids to company A
   - Assert `SELECT * FROM shifts` returns only A's shifts
   - Assert `INSERT INTO shifts (company_id=B, ...)` fails with RLS violation
   - Test each role individually

2. **Regression tests**:
   - All existing tests continue to pass with RLS enabled

---

## RLS Policy Template

For each table with `company_id`:

```sql
-- For app_role (standard CRUD)
CREATE POLICY app_tenant_isolation
  ON <table_name>
  FOR ALL
  USING (
    company_id = ANY(current_setting('app.company_ids', true)::text[])
  )
  WITH CHECK (
    company_id = ANY(current_setting('app.company_ids', true)::text[])
  );

-- For analytics_role (SELECT only, full access)
CREATE POLICY analytics_full_access
  ON <table_name>
  FOR SELECT
  USING (true);
```

---

## Migration Strategy

### Dev/Test Environments
1. Run Alembic migration (owner connection)
2. Create roles
3. Run integration tests against PostgreSQL
4. Update ECS task definition to use `app_role`

### Staging
1. Dry-run: apply migration, test with `app_role`
2. Verify no SELECT errors, no unexpected RLS denials
3. Monitor for 24h, then promote

### Production
1. **Pre-migration checklist**:
   - Backup RDS database
   - Run code review on migration SQL
   - Staging has been running RLS for 48h+

2. **Migration window**:
   - Apply Alembic migration (as owner, during low-traffic window)
   - Test with a small canary set of app_role connections
   - Once validated, blue-green deploy new app code (with RLS setting)
   - Monitor error rates, query latency

3. **Rollback plan**:
   - Revert `get_db()` RLS setting (stop setting company_ids)
   - Drop policies if needed (Alembic down, or manual ALTER TABLE ... DISABLE RLS)
   - Redeploy app pointing to master user again

---

## Notes

- **Setting company_ids**: Store in ContextVar during `get_current_user`, pass to `get_db()` caller via dependency injection or middleware
- **NULL company_id**: Tables with NULL company_id (if any) need separate policy or index consideration
- **Relationship access**: RLS applies to joins and subqueries automatically
- **Performance**: Indexed queries on company_id benefit from RLS policies; monitor EXPLAIN output
- **Audit trail**: All policy denials logged by Postgres; query PostgreSQL logs for compliance

---

## Related Issues
- #125 Part 1: Automatic ORM tenant filtering (merged in PR #164)
- Previous knowledge: Part 1 blocks missing WHERE; Part 2 blocks raw SQL, scripts, ORM bypasses

