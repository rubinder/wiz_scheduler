"""Add strategy_param2 to shift_schedules

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shift_schedules", sa.Column("strategy_param2", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("shift_schedules", "strategy_param2")
