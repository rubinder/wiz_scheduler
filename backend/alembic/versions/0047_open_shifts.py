"""Open shifts (#174): shifts.employee_id becomes nullable; NULL = open.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "shifts", "employee_id", existing_type=sa.String(8), nullable=True
    )


def downgrade() -> None:
    # Refuse rather than delete: an open shift is a slot a manager still has
    # to fill, and dropping it silently is exactly the bug #174 fixes.
    open_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM shifts WHERE employee_id IS NULL")
    ).scalar()
    if open_count:
        raise RuntimeError(
            f"{open_count} open shift(s) exist; fill or delete them before "
            "downgrading past 0047"
        )
    op.alter_column(
        "shifts", "employee_id", existing_type=sa.String(8), nullable=False
    )
