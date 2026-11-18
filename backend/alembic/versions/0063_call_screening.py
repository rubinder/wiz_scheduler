"""AI host call screening (#259 step 7).

number_rules: a manager's allow/block entries for caller numbers, per
location or (location_id NULL) company-wide. screened_calls: one row per
screening decision, for analytics. Both company-keyed, so they get 0058's
three policies (app, analytics, admin), as voice_numbers in 0060.

spam_numbers / spam_ingest_files: the FTC Do Not Call complaint list and
the files the ingest has handled. Global, not tenant data: no company
column, and RLS only so the "every table has RLS" invariant holds, with an
app_role policy that reads every row (FOR SELECT USING (true)). app_role
loses the INSERT/UPDATE/DELETE that 0050's default privileges gave it: a
request may read the list, never write it. The ingest writes them from its
own session, which carries no RLS binding and so runs as the table owner
(like other scheduled scripts); the owner is not subject to the
unforced RLS.

Revision ID: 0063
Revises: 0062
"""
from alembic import op
import sqlalchemy as sa

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None

GLOBAL_TABLES = ("spam_numbers", "spam_ingest_files")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "number_rules",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=True),
        sa.Column("e164", sa.String(20), nullable=False),
        sa.Column("action", sa.String(5), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_user_id", sa.String(8),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_in("action", ("allow", "block")),
                           name="ck_number_rules_action"),
    )
    op.create_index("ix_number_rules_company_id_e164", "number_rules",
                    ["company_id", "e164"])
    # One permanent entry per scope and number (number_rules.py backstop).
    op.execute(
        "CREATE UNIQUE INDEX uq_number_rules_permanent ON number_rules "
        "(company_id, coalesce(location_id, ''), e164) WHERE expires_at IS NULL")
    _company_rls("number_rules")

    op.create_table(
        "screened_calls",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=False),
        sa.Column("caller_key", sa.String(64), nullable=True),
        sa.Column("decision", sa.String(6), nullable=False),
        sa.Column("reason", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(_in("decision", ("answer", "block")),
                           name="ck_screened_calls_decision"),
        sa.CheckConstraint(
            _in("reason", ("allow_list", "block_list", "ftc", "default", "withheld")),
            name="ck_screened_calls_reason"),
    )
    op.create_index("ix_screened_calls_company_id", "screened_calls", ["company_id"])
    op.create_index("ix_screened_calls_location_id_created_at", "screened_calls",
                    ["location_id", "created_at"])
    _company_rls("screened_calls")

    op.create_table(
        "spam_numbers",
        sa.Column("e164", sa.String(20), primary_key=True),
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column("reports", sa.Integer(), nullable=False, server_default="1"),
    )
    # No separate e164 index: the primary key leads with it.
    op.create_table(
        "spam_ingest_files",
        sa.Column("file_date", sa.Date(), primary_key=True),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    for T in GLOBAL_TABLES:
        _global_read_only(T)


def _company_rls(T: str) -> None:
    """0058's policies for a table with a company_id."""
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {T} TO app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"GRANT SELECT, UPDATE ON {T} TO admin_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY app_tenant_isolation_{T}
            ON {T}
            FOR ALL
            TO app_role
            USING (company_id = ANY(current_setting('app.company_ids', true)::text[]))
            WITH CHECK (company_id = ANY(current_setting('app.company_ids', true)::text[]));
    """)
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T} ON {T}
            FOR SELECT TO analytics_role USING (true);
    """)
    op.execute(f"""
        CREATE POLICY admin_access_{T} ON {T}
            FOR ALL TO admin_role USING (true) WITH CHECK (true);
    """)


def _global_read_only(T: str) -> None:
    """Readable by every request, written only by the owner (the ingest)."""
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {T} FROM app_role;")
    op.execute(f"GRANT SELECT ON {T} TO app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"""
        CREATE POLICY app_read_all_{T} ON {T}
            FOR SELECT TO app_role USING (true);
    """)
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T} ON {T}
            FOR SELECT TO analytics_role USING (true);
    """)


def downgrade() -> None:
    # Their indexes and policies go with them.
    op.drop_table("spam_ingest_files")
    op.drop_table("spam_numbers")
    op.drop_table("screened_calls")
    op.drop_table("number_rules")
