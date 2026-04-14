"""Add api_integration column to ownership_groups

Nullable string that records which external integration (e.g. "7shifts")
an ownership group uses to import data. NULL means no integration / manual
data entry only.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ownership_groups",
        sa.Column("api_integration", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ownership_groups", "api_integration")
