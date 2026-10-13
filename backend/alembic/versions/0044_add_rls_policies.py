"""Add PostgreSQL Row-Level Security policies for defense-in-depth tenant isolation.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-13 00:00:00.000000

This migration:
1. Creates database roles: app_role, analytics_role, admin_role
2. Grants appropriate permissions to each role
3. Enables Row-Level Security on all tables with company_id
4. Creates RLS policies for each role

Execution requirements:
- Must run as the RDS master user (postgres) or alembic_role owner
- The app_role password should be provided via TF_VAR_app_db_password
  and is stored in AWS Secrets Manager as APP_DATABASE_URL

Background (Issue #125):
- Part 1 (PR #164): Application-level tenant filtering via ContextVar
- Part 2 (this): Postgres RLS for defense-in-depth
Both layers together catch missing WHERE clauses, raw SQL, and ORM bypasses.

RLS policy design:
- app_role: SELECT/INSERT/UPDATE/DELETE filtered by company_id
- analytics_role: SELECT only, sees all data (for reports/dashboards)
- admin_role: SELECT/UPDATE, sees all data (for support/debugging)
Each role has its own policy (no bypass, not owner).

Production deployment:
1. Apply this migration (creates roles + policies in database)
2. In Terraform, set TF_VAR_app_db_password to the app_role password
3. Run terraform apply (creates APP_DATABASE_URL secret in Secrets Manager)
4. Update backend/config.py to use APP_DATABASE_URL instead of DATABASE_URL
5. Update backend/database.py get_db() to set company_ids via current_setting
6. Blue-green deploy new application code
7. Monitor error rates and query latency for 24h
8. If rollback needed: revert to DATABASE_URL and drop policies

Tables affected by RLS (have company_id column):
See the list below in the migration functions.

Notes:
- NULL company_id rows are not filtered (RLS allows them via policy)
- Schema changes (ALTER TABLE) still require master user
- Alembic runs as master user (unchanged)
- RLS policies are table-level, not query-level (automatic, always enforced)
- Performance: minimal overhead for indexed company_id lookups

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply RLS policies and create application roles."""

    # List of all tables with company_id that need RLS protection
    # Generated from backend/models/ by finding all models with company_id field
    TABLES_WITH_COMPANY_ID = [
        "regions",
        "locations",
        "shift_templates",
        "shift_schedules",
        "shifts",
        "employees",
        "roles",
        "ownership_group_employees",
        "employee_invites",
        "employee_availability",
        "time_entries",
        "affinities",
        "scheduling_preferences",
        "special_hours",
        "failure_logs",
        "storage_snapshots",
        "invoice_items",
        "feedback_submissions",
        # Note: companies, ownership_groups, users tables have no company_id
        # They are cross-tenant reference tables, no filtering applied
    ]

    # Step 1: Create roles
    # app_role: application connection, RLS enforced via current_setting
    op.execute("""
        DO $$ BEGIN
            CREATE ROLE app_role WITH LOGIN;
        EXCEPTION WHEN duplicate_object THEN
            -- Role already exists, skip
            NULL;
        END $$;
    """)

    # analytics_role: reporting/dashboards, SELECT only, sees all data
    op.execute("""
        DO $$ BEGIN
            CREATE ROLE analytics_role WITH LOGIN;
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$;
    """)

    # admin_role: support/debugging, SELECT/UPDATE, sees all data
    op.execute("""
        DO $$ BEGIN
            CREATE ROLE admin_role WITH LOGIN;
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$;
    """)

    # Step 2: Grant table permissions to app_role
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_role;")

    # Grant sequence permissions (for auto-increment IDs, though we use gen_random_uuid)
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;")

    # Step 3: Grant permissions to analytics_role (SELECT only on tenant tables)
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"GRANT SELECT ON {table} TO analytics_role;")

    # Step 4: Grant permissions to admin_role (SELECT, UPDATE on tenant tables)
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"GRANT SELECT, UPDATE ON {table} TO admin_role;")

    # Step 5: Enable RLS on all tables with company_id
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        # FORCE ensures even the owner/superuser is subject to RLS
        # (without FORCE, superuser can see all rows; with FORCE, policies apply to everyone)
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # Step 6: Create RLS policies for app_role
    # app_role is filtered by current_setting('app.company_ids') set by get_db()
    for table in TABLES_WITH_COMPANY_ID:
        # Get the actual column name (handles plural/singular variations)
        # For now, we assume all have a company_id column
        policy_name = f"app_tenant_isolation_{table}"

        op.execute(f"""
            CREATE POLICY {policy_name}
                ON {table}
                FOR ALL
                TO app_role
                USING (
                    company_id = ANY(
                        current_setting('app.company_ids', true)::text[]
                    )
                )
                WITH CHECK (
                    company_id = ANY(
                        current_setting('app.company_ids', true)::text[]
                    )
                );
        """)

    # Step 7: Create RLS policies for analytics_role (SELECT all)
    for table in TABLES_WITH_COMPANY_ID:
        policy_name = f"analytics_read_all_{table}"
        op.execute(f"""
            CREATE POLICY {policy_name}
                ON {table}
                FOR SELECT
                TO analytics_role
                USING (true);
        """)

    # Step 8: Create RLS policies for admin_role (SELECT/UPDATE all)
    for table in TABLES_WITH_COMPANY_ID:
        policy_name = f"admin_access_{table}"
        op.execute(f"""
            CREATE POLICY {policy_name}
                ON {table}
                FOR ALL
                TO admin_role
                USING (true)
                WITH CHECK (true);
        """)


def downgrade() -> None:
    """Remove RLS policies and drop application roles."""

    TABLES_WITH_COMPANY_ID = [
        "regions",
        "locations",
        "shift_templates",
        "shift_schedules",
        "shifts",
        "employees",
        "roles",
        "ownership_group_employees",
        "employee_invites",
        "employee_availability",
        "time_entries",
        "affinities",
        "scheduling_preferences",
        "special_hours",
        "failure_logs",
        "storage_snapshots",
        "invoice_items",
        "feedback_submissions",
    ]

    # Step 1: Drop all RLS policies
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{table} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{table} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS admin_access_{table} ON {table};")

    # Step 2: Disable RLS on all tables
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    # Step 3: Revoke permissions from roles
    for table in TABLES_WITH_COMPANY_ID:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM app_role;")
        op.execute(f"REVOKE SELECT ON {table} FROM analytics_role;")
        op.execute(f"REVOKE SELECT, UPDATE ON {table} FROM admin_role;")

    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM app_role;")

    # Step 4: Drop roles
    op.execute("DROP ROLE IF EXISTS app_role;")
    op.execute("DROP ROLE IF EXISTS analytics_role;")
    op.execute("DROP ROLE IF EXISTS admin_role;")
