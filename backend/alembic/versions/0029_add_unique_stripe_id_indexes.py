"""Add partial unique indexes on ownership_groups Stripe id columns

Prevents the same Stripe customer or subscription from being attached to
two ownership groups simultaneously (e.g. via webhook replay, the
reactivate/upgrade flows, or a manual DB edit). Partial (WHERE ... IS NOT
NULL) so free-tier ownership groups, which have NULL in both columns, don't
collide with each other under the unique constraint.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_ownership_groups_stripe_customer_id",
        "ownership_groups",
        ["stripe_customer_id"],
        unique=True,
        postgresql_where=sa.text("stripe_customer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ownership_groups_stripe_subscription_id",
        "ownership_groups",
        ["stripe_subscription_id"],
        unique=True,
        postgresql_where=sa.text("stripe_subscription_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ownership_groups_stripe_subscription_id",
        table_name="ownership_groups",
    )
    op.drop_index(
        "uq_ownership_groups_stripe_customer_id",
        table_name="ownership_groups",
    )
