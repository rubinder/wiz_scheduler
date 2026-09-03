"""add observe-only signup signals to ownership_groups

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-31 00:00:00.000000

No backfill. These are recorded at /auth/register from request data that
only exists at that moment; existing groups have nothing to derive them
from, and inventing values would poison the very distribution the columns
exist to measure.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("signup_ip_masked", sa.String(), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("signup_email_normalized", sa.String(), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("signup_device_id", sa.String(), nullable=True),
    )
    op.add_column(
        "ownership_groups",
        sa.Column("signup_user_agent_hash", sa.String(length=64), nullable=True),
    )
    # Indexed for the clustering queries ("how many groups share this?").
    # Not unique — sharing a value is the thing being measured, not an error.
    op.create_index(
        "ix_ownership_groups_signup_email_normalized",
        "ownership_groups",
        ["signup_email_normalized"],
    )
    op.create_index(
        "ix_ownership_groups_signup_device_id",
        "ownership_groups",
        ["signup_device_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ownership_groups_signup_device_id", table_name="ownership_groups"
    )
    op.drop_index(
        "ix_ownership_groups_signup_email_normalized",
        table_name="ownership_groups",
    )
    op.drop_column("ownership_groups", "signup_user_agent_hash")
    op.drop_column("ownership_groups", "signup_device_id")
    op.drop_column("ownership_groups", "signup_email_normalized")
    op.drop_column("ownership_groups", "signup_ip_masked")
