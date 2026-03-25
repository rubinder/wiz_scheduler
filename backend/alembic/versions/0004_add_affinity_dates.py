"""Add entry_date and expiration_date to employee_affinities

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employee_affinities",
        sa.Column("entry_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
    )
    op.add_column(
        "employee_affinities",
        sa.Column("expiration_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employee_affinities", "expiration_date")
    op.drop_column("employee_affinities", "entry_date")
