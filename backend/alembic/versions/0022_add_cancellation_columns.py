"""Add cancellation lifecycle columns to ownership_groups.

- canceled_at: set by customer.subscription.deleted webhook. Presence
  indicates the OG is in read_only_grace state.
- notified_subscription_ended_at / notified_deletion_reminder_at /
  notified_data_deleted_at: idempotency timestamps for the three
  lifecycle emails (subscription ended, 14-day reminder, deleted).
  Cleared on reactivation so a future re-cancel re-fires correctly.

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_subscription_ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_deletion_reminder_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("notified_data_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ownership_groups", "notified_data_deleted_at")
    op.drop_column("ownership_groups", "notified_deletion_reminder_at")
    op.drop_column("ownership_groups", "notified_subscription_ended_at")
    op.drop_column("ownership_groups", "canceled_at")
