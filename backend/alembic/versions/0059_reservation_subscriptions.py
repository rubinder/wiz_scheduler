"""AI host subscription and usage (#259, step 5).

reservation_subscriptions: one separately billed agent subscription per
ownership group, kept current by the Stripe webhook. agent_usage: one row per
billable finished conversation (cost, billed amount, period); it carries no
diner data and outlives the conversation, whose retention deletion sets
conversation_id NULL. billing_charges.kind gains
'invoice_item_reservation_agent' for the reconciled overage InvoiceItem and
'agent_duplicate_refund' for a duplicate agent subscription cancelled and
refunded by the webhook backstop.

RLS: agent_usage has a company_id and gets 0058's policies;
reservation_subscriptions is keyed by ownership group and follows 0052/0056
(enabled, not forced; one app_role policy). `STATEMENTS` is also executed by
tests/conftest.py in RLS_TEST mode.

Revision ID: 0059
Revises: 0058
"""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

KINDS_OLD = ("kind IN ('autoreload', 'purchase', 'invoice_item_storage', "
             "'invoice_item_employees')")
KINDS_NEW = ("kind IN ('autoreload', 'purchase', 'invoice_item_storage', "
             "'invoice_item_employees', 'invoice_item_reservation_agent', "
             "'agent_duplicate_refund')")

STATUSES = ("incomplete", "incomplete_expired", "active", "trialing", "past_due",
            "canceled", "unpaid", "paused")

POLICIES: dict[str, str] = {
    "reservation_subscriptions": "ownership_group_id = ANY(app_ownership_group_ids())",
}

STATEMENTS: list[str] = []
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


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "reservation_subscriptions",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("ownership_group_id", sa.String(8),
                  sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("ownership_group_id",
                            name="reservation_subscriptions_ownership_group_id_key"),
        sa.UniqueConstraint("stripe_subscription_id",
                            name="reservation_subscriptions_stripe_subscription_id_key"),
        sa.CheckConstraint(_in("status", STATUSES),
                           name="ck_reservation_subscriptions_status"),
    )
    op.create_table(
        "agent_usage",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"),
                  nullable=False),
        sa.Column("ownership_group_id", sa.String(8),
                  sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("location_id", sa.String(8), sa.ForeignKey("locations.id"),
                  nullable=False),
        sa.Column("conversation_id", sa.String(8),
                  sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("billed_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product", sa.String(), nullable=False,
                  server_default=sa.text("'reservation_agent'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("conversation_id", name="agent_usage_conversation_id_key"),
        sa.CheckConstraint("product IN ('reservation_agent')",
                           name="ck_agent_usage_product"),
    )
    op.create_index("ix_agent_usage_company_id", "agent_usage", ["company_id"])
    op.create_index("ix_agent_usage_ownership_group_id", "agent_usage",
                    ["ownership_group_id"])

    _company_rls("agent_usage")
    for statement in STATEMENTS:
        op.execute(statement)

    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_NEW)


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
    # A charge of the new kind would violate the restored check: refuse
    # rather than delete billing history.
    conn = op.get_bind()
    n = conn.execute(sa.text(
        "SELECT count(*) FROM billing_charges "
        "WHERE kind IN ('invoice_item_reservation_agent', "
        "'agent_duplicate_refund')")).scalar()
    if n:
        raise RuntimeError(
            f"{n} billing_charges rows of the reservation agent kinds "
            "exist; downgrading 0059 would violate billing_charges_kind_check")
    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_OLD)
    op.drop_table("agent_usage")  # its indexes and policies go with it
    op.drop_table("reservation_subscriptions")
