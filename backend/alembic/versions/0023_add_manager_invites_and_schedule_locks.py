"""add manager_invites and schedule_locks

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manager_invites",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column("ownership_group_id", sa.String(length=8),
                  sa.ForeignKey("ownership_groups.id"), nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=8),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_company_id", sa.String(length=8),
                  sa.ForeignKey("companies.id"), nullable=True),
        sa.UniqueConstraint("token", name="uq_manager_invites_token"),
    )
    op.create_index("ix_manager_invites_og_id", "manager_invites",
                    ["ownership_group_id"])
    op.create_index("ix_manager_invites_token", "manager_invites", ["token"])

    op.create_table(
        "schedule_locks",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column("company_id", sa.String(length=8),
                  sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("locked_by_user_id", sa.String(length=8),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", name="uq_schedule_locks_company_id"),
    )


def downgrade() -> None:
    op.drop_table("schedule_locks")
    op.drop_index("ix_manager_invites_token", table_name="manager_invites")
    op.drop_index("ix_manager_invites_og_id", table_name="manager_invites")
    op.drop_table("manager_invites")
