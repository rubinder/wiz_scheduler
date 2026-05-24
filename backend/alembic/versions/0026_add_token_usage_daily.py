"""add token_usage_daily for per-day Anthropic spend circuit breaker

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_usage_daily",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "ownership_group_id",
            sa.String(length=8),
            sa.ForeignKey("ownership_groups.id"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_usd", sa.Float(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "ownership_group_id", "usage_date",
            name="uq_token_usage_daily_group_date",
        ),
    )
    op.create_index(
        "ix_token_usage_daily_group_date",
        "token_usage_daily",
        ["ownership_group_id", "usage_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_token_usage_daily_group_date", table_name="token_usage_daily")
    op.drop_table("token_usage_daily")
