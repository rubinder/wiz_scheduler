"""Row-level security for tenant tables without a company_id (#183).

0044 put policies on every table with a company_id column. The rest of the
tenant data is keyed another way, and 0050 granted app_role DML on all of
it, so an authenticated request (which runs as app_role) was limited by the
application's queries alone on these tables:

- by ownership group: ownership_groups, companies, activation_events,
  billing_charges, integration_imports, manager_invites, og_email_send_log,
  storage_snapshots, token_usage, token_usage_daily
- by a parent that is itself RLS-limited: manager_locations (location),
  condensed_role_mappings (condensed role), email_verification_tokens,
  password_reset_tokens, gdpr_export_log (user)

Each gets RLS enabled (not forced: the owner, which runs migrations,
background jobs and unauthenticated routes, keeps full access) and one
app_role policy. Ownership groups come from app_ownership_group_ids(), a
SECURITY DEFINER function: it runs as the owner, so it reads companies
without recursing into the companies policy.

Only alembic_version stays without RLS; tests/test_migrations.py enforces
that every other table has it.

`STATEMENTS` is also executed by tests/conftest.py in RLS_TEST mode, so the
suite runs against exactly these policies.
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_COMPANY_IDS = "current_setting('app.company_ids', true)::text[]"
_OG_IDS = "app_ownership_group_ids()"

OG_TABLES = (
    "activation_events",
    "billing_charges",
    "integration_imports",
    "manager_invites",
    "og_email_send_log",
    "storage_snapshots",
    "token_usage",
    "token_usage_daily",
)

# table -> USING / WITH CHECK expression for app_role
POLICIES: dict[str, str] = {
    **{t: f"ownership_group_id = ANY({_OG_IDS})" for t in OG_TABLES},
    "ownership_groups": f"id = ANY({_OG_IDS})",
    # A request may create a company in its own ownership group, whose id
    # is not yet among its company ids.
    "companies": f"id = ANY({_COMPANY_IDS}) OR ownership_group_id = ANY({_OG_IDS})",
    "manager_locations": "EXISTS (SELECT 1 FROM locations l WHERE l.id = location_id)",
    "condensed_role_mappings": (
        "EXISTS (SELECT 1 FROM condensed_roles c WHERE c.id = condensed_role_id)"
    ),
    "email_verification_tokens": "EXISTS (SELECT 1 FROM users u WHERE u.id = user_id)",
    "password_reset_tokens": "EXISTS (SELECT 1 FROM users u WHERE u.id = user_id)",
    "gdpr_export_log": "EXISTS (SELECT 1 FROM users u WHERE u.id = user_id)",
}

STATEMENTS: list[str] = [
    f"""
    CREATE OR REPLACE FUNCTION app_ownership_group_ids() RETURNS text[]
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT coalesce(
                array_agg(DISTINCT ownership_group_id)
                    FILTER (WHERE ownership_group_id IS NOT NULL),
                '{{}}'::text[])
            FROM companies
            WHERE id = ANY({_COMPANY_IDS})
        $$
    """,
    "REVOKE ALL ON FUNCTION app_ownership_group_ids() FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION app_ownership_group_ids() TO app_role",
]
for _table, _expr in POLICIES.items():
    STATEMENTS += [
        f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS app_tenant_isolation_{_table} ON {_table}",
        f"""
        CREATE POLICY app_tenant_isolation_{_table} ON {_table}
            FOR ALL TO app_role
            USING ({_expr})
            WITH CHECK ({_expr})
        """,
    ]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_ownership_group_ids()")
