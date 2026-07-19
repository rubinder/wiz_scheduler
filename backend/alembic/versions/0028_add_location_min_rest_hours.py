"""Add min_rest_hours to locations

Adds a nullable float column setting the minimum rest (in hours) required
between an employee's shifts on different days. NULL means "no constraint".
Used for NYC Fair Workweek "clopening" compliance (set to 11 for fast food).

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("min_rest_hours", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("locations", "min_rest_hours")
