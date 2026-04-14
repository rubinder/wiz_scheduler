"""Add max_hours_per_week to employees

Adds a nullable float column capping how many hours an employee can be
scheduled for in a single week. NULL means "no cap".

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "54ebeacf0286"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("max_hours_per_week", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "max_hours_per_week")
