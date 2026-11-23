"""AI floor-plan drafts (decision 101).

floor_plan_drafts: one row per "Draft from photos (AI)" request: status, the
proposed floor (FloorIn JSON), warnings, confidence, the at-cost charge and
an error code. The uploaded files and notes are never stored. Company-keyed,
so it gets 0058's three policies (app, analytics, admin), as in 0065. Both
foreign keys cascade: a draft is a 7-day cache, so a hard delete of its
company or location (group deletion, the demo prune) takes it along.

Revision ID: 0068
Revises: 0067
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

T = "floor_plan_drafts"


def upgrade() -> None:
    op.create_table(
        T,
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.String(8),
                  sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.String(6), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')",
                           name="ck_floor_plan_drafts_status"),
    )
    op.create_index("ix_floor_plan_drafts_company_id", T, ["company_id"])
    op.create_index("ix_floor_plan_drafts_location_id", T, ["location_id"])
    op.create_index("ix_floor_plan_drafts_created_at", T, ["created_at"])
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
    # Indexes and policies go with the table.
    op.drop_table(T)
