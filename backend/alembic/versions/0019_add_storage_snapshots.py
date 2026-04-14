"""Add storage_snapshots table

Records daily storage usage per ownership group so billing can use
historical data rather than only point-in-time queries.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "storage_snapshots",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column(
            "ownership_group_id",
            sa.String(8),
            sa.ForeignKey("ownership_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_gb", sa.Float(), nullable=False),
        sa.Column("charged_usd", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "ownership_group_id", "snapshot_date",
            name="uq_storage_snapshot_group_date",
        ),
    )
    op.create_index(
        "ix_storage_snapshots_og_id",
        "storage_snapshots",
        ["ownership_group_id"],
    )
    op.create_index(
        "ix_storage_snapshots_measured_at",
        "storage_snapshots",
        ["measured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_storage_snapshots_measured_at", table_name="storage_snapshots")
    op.drop_index("ix_storage_snapshots_og_id", table_name="storage_snapshots")
    op.drop_table("storage_snapshots")
