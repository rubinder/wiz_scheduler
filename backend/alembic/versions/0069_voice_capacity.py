"""Voice call capacity (decision 103, #259 follow-up).

voice_company_settings: a company's simultaneous-call allocation and its
default staff transfer number (no row = the defaults).
voice_capacity_daily: a company's daily peak of simultaneous voice calls.
voice_capacity_requests: a company admin's request for more capacity.
All three are company-keyed with ON DELETE CASCADE from companies, so they
get 0058's three policies (app, analytics, admin), as 0066 does.

voice_platform_daily and voice_quota_requests are platform data: no company
column, written only by the daily job and the operator script, which run as
the table owner. RLS is enabled with no app_role policy and app_role loses
the privileges 0050's default privileges gave it, plus a policy that
matches no row (every table keeps an app_role policy), so no request reads
or writes them.

Revision ID: 0069
Revises: 0068
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

COMPANY_TABLES = ("voice_company_settings", "voice_capacity_daily",
                  "voice_capacity_requests")
PLATFORM_TABLES = ("voice_platform_daily", "voice_quota_requests")


def _company_fk() -> sa.ForeignKey:
    return sa.ForeignKey("companies.id", ondelete="CASCADE")


def _now() -> sa.TextClause:
    return sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "voice_company_settings",
        sa.Column("company_id", sa.String(8), _company_fk(), primary_key=True),
        sa.Column("voice_call_allocation", sa.Integer(), nullable=False),
        sa.Column("allocation_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("default_staff_transfer_number", sa.String(20), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
        sa.CheckConstraint("voice_call_allocation >= 1",
                           name="ck_voice_company_settings_allocation"),
    )
    op.create_table(
        "voice_capacity_daily",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), _company_fk(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("peak_concurrent", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
        sa.UniqueConstraint("company_id", "day", name="uq_voice_capacity_daily_company_day"),
    )
    op.create_index("ix_voice_capacity_daily_company_id", "voice_capacity_daily",
                    ["company_id"])
    op.create_index("ix_voice_capacity_daily_day", "voice_capacity_daily", ["day"])
    op.create_table(
        "voice_capacity_requests",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), _company_fk(), nullable=False),
        sa.Column("requested_by", sa.String(8),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("location_ids", postgresql.JSONB(), nullable=False),
        sa.Column("expected_peak", sa.Integer(), nullable=False),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("granted_allocation", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
        sa.CheckConstraint("status IN ('pending', 'approved', 'declined')",
                           name="ck_voice_capacity_requests_status"),
        sa.CheckConstraint("expected_peak BETWEEN 1 AND 500",
                           name="ck_voice_capacity_requests_expected_peak"),
    )
    op.create_index("ix_voice_capacity_requests_company_id", "voice_capacity_requests",
                    ["company_id"])
    op.create_index("uq_voice_capacity_requests_company_pending", "voice_capacity_requests",
                    ["company_id"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))
    for T in COMPANY_TABLES:
        _company_rls(T)

    op.create_table(
        "voice_platform_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("peak_concurrent", sa.Integer(), nullable=False),
        sa.Column("cloudwatch_peak", sa.Integer(), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
    )
    op.create_table(
        "voice_quota_requests",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("desired", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("aws_request_id", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=_now()),
        sa.CheckConstraint(
            "status IN ('pending', 'manual', 'approved', 'denied', 'failed', 'superseded')",
            name="ck_voice_quota_requests_status"),
    )
    for T in PLATFORM_TABLES:
        _owner_only(T)


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


def _owner_only(T: str) -> None:
    """Platform data: the owner (daily job, operator script) only; analysts
    may read it."""
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {T} FROM app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    # An app_role policy that matches nothing, so the "every table has an
    # app_role policy" invariant holds while a request still sees no row.
    op.execute(f"""
        CREATE POLICY app_no_rows_{T} ON {T}
            FOR SELECT TO app_role USING (false);
    """)
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T} ON {T}
            FOR SELECT TO analytics_role USING (true);
    """)


def downgrade() -> None:
    # Their indexes and policies go with them.
    for T in PLATFORM_TABLES + tuple(reversed(COMPANY_TABLES)):
        op.drop_table(T)
