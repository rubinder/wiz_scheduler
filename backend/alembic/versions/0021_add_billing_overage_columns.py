"""Add auto-reload columns to ownership_groups and create billing_charges table.

Adds the columns needed for the auto-reload prepaid balance flow:
- autoreload_enabled, autoreload_threshold_usd, autoreload_amount_usd: per-OG settings
- autoreload_failed_at: timestamp set when an auto-reload PaymentIntent fails;
  blocks further AI/schedule generation until cleared by a successful retry
- default_payment_method_id: cached from the customer's Stripe subscription
  so off-session PaymentIntents don't need to round-trip Stripe each time

The billing_charges table is an audit log + idempotency record for every charge
or invoice item the backend produces. PR 1 only writes `autoreload` rows; PR 2
adds `invoice_item_*` rows.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_threshold_usd", sa.Numeric(10, 4), nullable=False, server_default=sa.text("2.0")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_amount_usd", sa.Numeric(10, 4), nullable=False, server_default=sa.text("10.0")),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("autoreload_failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("default_payment_method_id", sa.String(), nullable=True),
    )

    op.create_table(
        "billing_charges",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("ownership_group_id", sa.String(8), sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("stripe_object_id", sa.String(), nullable=True),
        sa.Column("period", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees')",
            name="billing_charges_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'pending')",
            name="billing_charges_status_check",
        ),
    )
    op.create_index(
        "ix_billing_charges_og_kind_period",
        "billing_charges",
        ["ownership_group_id", "kind", "period"],
    )
    op.create_index(
        "ix_billing_charges_og_created",
        "billing_charges",
        ["ownership_group_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_billing_charges_og_created", table_name="billing_charges")
    op.drop_index("ix_billing_charges_og_kind_period", table_name="billing_charges")
    op.drop_table("billing_charges")
    op.drop_column("ownership_groups", "default_payment_method_id")
    op.drop_column("ownership_groups", "autoreload_failed_at")
    op.drop_column("ownership_groups", "autoreload_amount_usd")
    op.drop_column("ownership_groups", "autoreload_threshold_usd")
    op.drop_column("ownership_groups", "autoreload_enabled")
