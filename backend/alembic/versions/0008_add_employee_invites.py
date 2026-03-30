"""add employee_invites table

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_invites",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.UniqueConstraint("token", name="uq_employee_invite_token"),
    )
    op.create_index("ix_employee_invites_company_id", "employee_invites", ["company_id"])
    op.create_index("ix_employee_invites_employee_id", "employee_invites", ["employee_id"])
    op.create_index("ix_employee_invites_token", "employee_invites", ["token"])


def downgrade() -> None:
    op.drop_index("ix_employee_invites_token")
    op.drop_index("ix_employee_invites_employee_id")
    op.drop_index("ix_employee_invites_company_id")
    op.drop_table("employee_invites")
