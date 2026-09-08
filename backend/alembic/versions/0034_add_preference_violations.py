"""add preference_violations to shifts and preference_summary to shift_schedules

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-04 00:00:00.000000

No backfill: the evaluation needs the preferences as they stood when the
schedule was generated, which nothing recorded. Existing rows stay NULL
and render without an asterisk (#99).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shifts", sa.Column("preference_violations", sa.JSON(), nullable=True))
    op.add_column("shift_schedules", sa.Column("preference_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shift_schedules", "preference_summary")
    op.drop_column("shifts", "preference_violations")
