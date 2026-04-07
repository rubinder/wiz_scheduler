"""Add cost_usd and charged_usd to token_usage

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("token_usage", sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"))
    op.add_column("token_usage", sa.Column("charged_usd", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("token_usage", "charged_usd")
    op.drop_column("token_usage", "cost_usd")
