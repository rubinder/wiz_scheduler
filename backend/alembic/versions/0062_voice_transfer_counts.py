"""AI host voice channel (#259 step 6): the daily staff-transfer counter.

voice_transfer_counts: transfers the AI host made per location per local
date (reservations.voice_transfers.reserve), the toll-fraud cap on the
outbound staff handoff. Company-keyed, so it gets 0058's three policies
(app, analytics, admin), as voice_numbers in 0060.

Revision ID: 0062
Revises: 0061
"""
from alembic import op
import sqlalchemy as sa

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

T = "voice_transfer_counts"


def upgrade() -> None:
    op.create_table(
        T,
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  primary_key=True),
        sa.Column("local_date", sa.Date(), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_voice_transfer_counts_company_id", T, ["company_id"])
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
    op.drop_table(T)  # its index and policies go with it
