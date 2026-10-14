"""Add PostgreSQL Row-Level Security policies for defense-in-depth tenant isolation.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-13 00:00:00.000000

This migration:
1. Creates database roles: app_role, analytics_role, admin_role
2. Grants appropriate permissions to each role
3. Enables Row-Level Security on every table with a company_id column
4. Creates RLS policies for each role

Background (Issue #125):
- Part 1 (PR #164): Application-level tenant filtering via ContextVar
- Part 2 (this): Postgres RLS for defense-in-depth

Inert until the app connects as app_role. RLS is ENABLEd, not FORCEd: the
tables are owned by the migration user, which is also what the app connects
as today (DATABASE_URL), and a table owner bypasses RLS unless it is forced.
That user is rds_superuser but has neither SUPERUSER nor BYPASSRLS, and RDS
cannot grant BYPASSRLS, so FORCE with no policy for the owner would make every
tenant table read as empty and reject every insert. FORCE only becomes safe
once nothing but Alembic connects as the owner.

The table list is read from information_schema at migration time rather than
hand-maintained: the first version of this migration named five tables that
do not exist and failed the production deploy, while missing sixteen that do.

Switching the app onto app_role is a separate change; see
docs/superpowers/specs/2026-10-14-rls-migration-fix-design.md (Phase 2), which
also covers the app.company_ids format the app_role policy expects.

RLS policy design:
- app_role: SELECT/INSERT/UPDATE/DELETE filtered by company_id
- analytics_role: SELECT only, sees all data (for reports/dashboards)
- admin_role: SELECT/UPDATE, sees all data (for support/debugging)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


ROLES = ("app_role", "analytics_role", "admin_role")


def _tenant_tables() -> list[str]:
    """Every public table with a company_id column, as the database has it now."""
    return list(op.get_bind().execute(sa.text("""
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND c.column_name = 'company_id'
          AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name
    """)).scalars())


def upgrade() -> None:
    """Apply RLS policies and create application roles."""
    tables = _tenant_tables()

    for role in ROLES:
        op.execute(f"""
            DO $$ BEGIN
                CREATE ROLE {role} WITH LOGIN;
            EXCEPTION WHEN duplicate_object THEN
                NULL;
            END $$;
        """)

    for table in tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_role;")
        op.execute(f"GRANT SELECT ON {table} TO analytics_role;")
        op.execute(f"GRANT SELECT, UPDATE ON {table} TO admin_role;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role;")

    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")

        op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{table} ON {table};")
        op.execute(f"""
            CREATE POLICY app_tenant_isolation_{table}
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

        op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{table} ON {table};")
        op.execute(f"""
            CREATE POLICY analytics_read_all_{table}
                ON {table}
                FOR SELECT
                TO analytics_role
                USING (true);
        """)

        op.execute(f"DROP POLICY IF EXISTS admin_access_{table} ON {table};")
        op.execute(f"""
            CREATE POLICY admin_access_{table}
                ON {table}
                FOR ALL
                TO admin_role
                USING (true)
                WITH CHECK (true);
        """)


def downgrade() -> None:
    """Remove RLS policies and drop application roles."""
    tables = _tenant_tables()

    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{table} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{table} ON {table};")
        op.execute(f"DROP POLICY IF EXISTS admin_access_{table} ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM app_role;")
        op.execute(f"REVOKE SELECT ON {table} FROM analytics_role;")
        op.execute(f"REVOKE SELECT, UPDATE ON {table} FROM admin_role;")

    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM app_role;")

    for role in ROLES:
        op.execute(f"DROP ROLE IF EXISTS {role};")
