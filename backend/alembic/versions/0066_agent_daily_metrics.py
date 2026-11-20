"""agent_daily_metrics (#259 step 10): one row per location per UTC day of
AI host metrics, written by the nightly analytics job
(backend/scripts/run_agent_analytics.py) from the rows it lands in the lake.
Company data, so it gets 0058's three policies (app, analytics, admin), as
0063 and 0065 do. The job writes from its own session, which carries no RLS
binding and runs as the table owner.

Revision ID: 0066
Revises: 0065
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

T = "agent_daily_metrics"


def upgrade() -> None:
    op.create_table(
        T,
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("conversations", sa.Integer(), nullable=False),
        sa.Column("resolved", sa.Integer(), nullable=False),
        sa.Column("booked", sa.Integer(), nullable=False),
        sa.Column("handed_off", sa.Integer(), nullable=False),
        sa.Column("abandoned", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("booking_intent", sa.Integer(), nullable=False),
        sa.Column("handoff_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("llm_p50_ms", sa.Integer(), nullable=True),
        sa.Column("llm_p95_ms", sa.Integer(), nullable=True),
        sa.Column("turn_p50_ms", sa.Integer(), nullable=True),
        sa.Column("turn_p95_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("billed_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("screened", sa.Integer(), nullable=False),
        sa.Column("blocked", sa.Integer(), nullable=False),
        sa.Column("covers_by_period", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("location_id", "day", name="uq_agent_daily_metrics_location_day"),
    )
    op.create_index("ix_agent_daily_metrics_company_id", T, ["company_id"])
    op.create_index("ix_agent_daily_metrics_day", T, ["day"])
    _company_rls(T)


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


def downgrade() -> None:
    # Its indexes and policies go with it.
    op.drop_table(T)
