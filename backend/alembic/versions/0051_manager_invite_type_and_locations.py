"""Manager invites choose the manager type and initial locations (#149, #150).

An invite now records whether it creates an 'admin' or a 'regular' manager
and, for a regular one, the locations to assign on acceptance. Every
existing invite was created as an admin invite, which the server default
keeps.

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manager_invites",
        sa.Column("manager_type", sa.String(), nullable=False, server_default="admin"),
    )
    op.add_column(
        "manager_invites",
        sa.Column("location_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("manager_invites", "location_ids")
    op.drop_column("manager_invites", "manager_type")
