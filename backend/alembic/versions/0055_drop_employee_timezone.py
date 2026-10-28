"""Drop employees.timezone (#171).

Availability is always the location's local time: whoever enters it is
assumed to know the location's zone, so nothing converts per employee and
the optional employee zone added in 0046 (#170) has no use. Production had
0 of 319 employees with it set when this was written.

Revision ID: 0055
Revises: 0054
"""
from alembic import op
import sqlalchemy as sa

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("employees", "timezone")


def downgrade() -> None:
    op.add_column("employees", sa.Column("timezone", sa.String(64), nullable=True))
