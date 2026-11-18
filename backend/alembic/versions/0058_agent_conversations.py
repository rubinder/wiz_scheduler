"""AI host agent conversations, events and lookup failures (#259).

Revision ID: 0058
Revises: 0057
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

TABLES = ["agent_conversations", "agent_events", "reservation_lookup_failures"]

CHANNELS = ("web", "voice")
EVENT_TYPES = ("user_turn", "assistant_turn", "tool_call", "tool_result",
               "error", "handoff", "action")
OUTCOMES = ("booked", "modified", "cancelled", "handed_off", "abandoned",
            "failed", "blocked")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _resv(name: str) -> sa.Column:
    return sa.Column(name, sa.String(8),
                     sa.ForeignKey("reservations.id", ondelete="SET NULL"),
                     nullable=True)


def _int0(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("public_token", sa.String(43), nullable=False),
        sa.Column("caller_number", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("outcome", sa.String(), nullable=True),
        _resv("reservation_id"), _resv("held_reservation_id"),
        _resv("verified_reservation_id"), _resv("pending_cancel_id"),
        _int0("turn_count"),
        sa.Column("turn_started_at", sa.DateTime(timezone=True), nullable=True),
        _int0("failed_lookups"),
        sa.Column("offered_starts", JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("pending_modify", JSONB(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("public_token", name="uq_agent_conversations_public_token"),
        sa.CheckConstraint(_in("channel", CHANNELS), name="ck_agent_conversations_channel"),
        sa.CheckConstraint(_in("status", ("open", "closed")),
                           name="ck_agent_conversations_status"),
        sa.CheckConstraint("outcome IS NULL OR " + _in("outcome", OUTCOMES),
                           name="ck_agent_conversations_outcome"),
    )
    op.create_index("ix_agent_conversations_company_id", "agent_conversations", ["company_id"])
    op.create_index("ix_agent_conversations_location_id", "agent_conversations", ["location_id"])

    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("conversation_id", sa.String(8),
                  sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _int0("input_tokens"), _int0("output_tokens"),
        _int0("cache_read_tokens"), _int0("cache_write_tokens"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_agent_events_conv_seq"),
        sa.CheckConstraint(_in("type", EVENT_TYPES), name="ck_agent_events_type"),
    )
    op.create_index("ix_agent_events_company_id", "agent_events", ["company_id"])
    op.create_index("ix_agent_events_conversation_id", "agent_events", ["conversation_id"])

    op.create_table(
        "reservation_lookup_failures",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_reservation_lookup_failures_company_id",
                    "reservation_lookup_failures", ["company_id"])
    op.create_index("ix_reservation_lookup_failures_loc_phone_created",
                    "reservation_lookup_failures", ["location_id", "phone", "created_at"])

    for t in TABLES:
        _rls(t)


def _rls(T: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {T} TO app_role;")
    op.execute(f"GRANT SELECT ON {T} TO analytics_role;")
    op.execute(f"GRANT SELECT, UPDATE ON {T} TO admin_role;")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS app_tenant_isolation_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY app_tenant_isolation_{T}
            ON {T}
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
    op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T}
            ON {T}
            FOR SELECT
            TO analytics_role
            USING (true);
    """)
    op.execute(f"DROP POLICY IF EXISTS admin_access_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY admin_access_{T}
            ON {T}
            FOR ALL
            TO admin_role
            USING (true)
            WITH CHECK (true);
    """)


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
