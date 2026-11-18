"""AI host voice channel (#259 step 6): numbers per location.

voice_numbers: one Amazon Connect phone number per location, following the
ownership group's AI host subscription (requested -> active -> releasing ->
released). Company-keyed, so it gets 0058's three policies (app, analytics,
admin), as agent_usage in 0059.

agent_conversations.external_ref: the channel's id for a contact (the
Connect contact id for a call), unique per location where set.

Revision ID: 0060
Revises: 0059
"""
from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

STATUSES = ("requested", "active", "releasing", "released")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "voice_numbers",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("ownership_group_id", sa.String(8),
                  sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=False),
        sa.Column("e164", sa.String(20), nullable=True),
        sa.Column("connect_phone_number_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("release_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("location_id", name="voice_numbers_location_id_key"),
        sa.UniqueConstraint("e164", name="voice_numbers_e164_key"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_voice_numbers_status"),
    )
    op.create_index("ix_voice_numbers_company_id", "voice_numbers", ["company_id"])
    op.create_index("ix_voice_numbers_ownership_group_id", "voice_numbers",
                    ["ownership_group_id"])
    _company_rls("voice_numbers")

    op.add_column("agent_conversations",
                  sa.Column("external_ref", sa.String(128), nullable=True))
    op.create_index("uq_agent_conversations_location_external_ref",
                    "agent_conversations", ["location_id", "external_ref"],
                    unique=True, postgresql_where=sa.text("external_ref IS NOT NULL"))


def _company_rls(T: str) -> None:
    """0058's policies for a table with a company_id."""
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
            USING (company_id = ANY(current_setting('app.company_ids', true)::text[]))
            WITH CHECK (company_id = ANY(current_setting('app.company_ids', true)::text[]));
    """)
    op.execute(f"DROP POLICY IF EXISTS analytics_read_all_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY analytics_read_all_{T} ON {T}
            FOR SELECT TO analytics_role USING (true);
    """)
    op.execute(f"DROP POLICY IF EXISTS admin_access_{T} ON {T};")
    op.execute(f"""
        CREATE POLICY admin_access_{T} ON {T}
            FOR ALL TO admin_role USING (true) WITH CHECK (true);
    """)


def downgrade() -> None:
    # A claimed number is billed until released in Connect: refuse rather
    # than forget it.
    conn = op.get_bind()
    n = conn.execute(sa.text(
        "SELECT count(*) FROM voice_numbers "
        "WHERE status <> 'released' AND connect_phone_number_id IS NOT NULL")).scalar()
    if n:
        raise RuntimeError(
            f"{n} voice numbers are still claimed in Amazon Connect; release "
            "them before downgrading 0060")
    op.drop_index("uq_agent_conversations_location_external_ref",
                  table_name="agent_conversations")
    op.drop_column("agent_conversations", "external_ref")
    op.drop_table("voice_numbers")  # its indexes and policies go with it
