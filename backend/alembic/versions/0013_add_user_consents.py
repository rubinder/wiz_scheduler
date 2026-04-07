"""Add user_consents table

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("user_id", sa.String(8), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("consent_type", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_consents")
