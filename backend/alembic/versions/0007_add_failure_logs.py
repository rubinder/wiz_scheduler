"""add failure_logs table

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "failure_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_index("ix_failure_logs_company_id", "failure_logs", ["company_id"])
    op.create_index("ix_failure_logs_category", "failure_logs", ["category"])
    op.create_index("ix_failure_logs_created_at", "failure_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_failure_logs_created_at")
    op.drop_index("ix_failure_logs_category")
    op.drop_index("ix_failure_logs_company_id")
    op.drop_table("failure_logs")
