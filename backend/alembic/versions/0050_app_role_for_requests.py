"""Let authenticated requests run as app_role (#183).

With RLS_ENFORCE on, each transaction of an authenticated request does
SET LOCAL ROLE app_role. That needs:

- the connecting (owner) role to be a member of app_role, for SET ROLE;
- app_role to have DML on every table the app touches, not only the tenant
  tables 0044 granted. Tables without company_id (companies, ownership
  groups, billing, tokens) have no policy and stay readable as before;
- the same for tables and sequences created by later migrations.

Nothing changes while RLS_ENFORCE is off: the app keeps connecting as the
owner, which the policies do not apply to.

Revision ID: 0050
Revises: 0049
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT app_role TO CURRENT_USER")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_role")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_role")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_role"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO app_role"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM app_role"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_role"
    )
    # Revoke exactly what upgrade added: tables without company_id. The
    # tenant tables' grants (and the sequences') are 0044's, and its own
    # downgrade revokes them before dropping the role.
    op.execute("""
        DO $$
        DECLARE t text;
        BEGIN
            FOR t IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename NOT IN (
                      SELECT table_name FROM information_schema.columns
                      WHERE table_schema = 'public' AND column_name = 'company_id'
                  )
            LOOP
                EXECUTE format(
                    'REVOKE SELECT, INSERT, UPDATE, DELETE ON %I FROM app_role', t
                );
            END LOOP;
        END $$;
    """)
    op.execute("REVOKE app_role FROM CURRENT_USER")
