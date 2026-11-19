"""Mock external booking provider (#259 step 9).

mock_external_bookings: the mock external system's own inventory (holds and
bookings), never `reservations` or tables. mock_external_idempotency: the
first response to an idempotent book/modify/cancel, kept 24 hours. Both
company-keyed, so they get 0058's three policies (app, analytics, admin),
as number_rules in 0063.

agent_conversations' booking refs (reservation_id, held_reservation_id,
verified_reservation_id, pending_cancel_id) lose 0058's foreign keys to
reservations: they are refs of the conversation's provider, and a mock
conversation's refs are mock_external_bookings ids. Reservations are never
deleted outside group deletion, which deletes the conversations too, so
their ON DELETE SET NULL had nothing left to do.

Revision ID: 0065
Revises: 0064
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


REF_COLUMNS = ("reservation_id", "held_reservation_id", "verified_reservation_id",
               "pending_cancel_id")


def upgrade() -> None:
    for col in REF_COLUMNS:
        op.drop_constraint(f"agent_conversations_{col}_fkey", "agent_conversations",
                           type_="foreignkey")
    op.create_table(
        "mock_external_bookings",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("party_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("code", sa.String(6), nullable=True),
        sa.Column("diner_name", sa.String(200), nullable=True),
        sa.Column("diner_phone", sa.String(20), nullable=True),
        sa.Column("held_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('held', 'booked', 'cancelled')",
                           name="ck_mock_external_bookings_status"),
        sa.CheckConstraint("party_size >= 1",
                           name="ck_mock_external_bookings_party_size"),
    )
    op.create_index("ix_mock_external_bookings_company_id", "mock_external_bookings",
                    ["company_id"])
    op.create_index("ix_mock_external_bookings_location_id_start_at",
                    "mock_external_bookings", ["location_id", "start_at"])
    op.create_index("uq_mock_external_bookings_location_code", "mock_external_bookings",
                    ["location_id", "code"], unique=True,
                    postgresql_where=sa.text("code IS NOT NULL"))
    _company_rls("mock_external_bookings")

    op.create_table(
        "mock_external_idempotency",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_mock_external_idempotency_company_id",
                    "mock_external_idempotency", ["company_id"])
    op.create_index("ix_mock_external_idempotency_created_at",
                    "mock_external_idempotency", ["created_at"])
    _company_rls("mock_external_idempotency")


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
    # Their indexes and policies go with them.
    op.drop_table("mock_external_idempotency")
    op.drop_table("mock_external_bookings")
    # Mock refs point at the dropped table; any other dangling ref too.
    for col in REF_COLUMNS:
        op.execute(f"UPDATE agent_conversations SET {col} = NULL WHERE {col} IS NOT NULL "
                   f"AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.id = {col})")
        op.create_foreign_key(f"agent_conversations_{col}_fkey", "agent_conversations",
                              "reservations", [col], ["id"], ondelete="SET NULL")
