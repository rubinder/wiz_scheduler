"""Add feedback_submissions table for quota tracking.

Revision ID: 0043_add_feedback_submissions
Revises: 0042_add_manager_type_and_locations
Create Date: 2026-10-12 16:55:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "0043_add_feedback_submissions"
down_revision = "0042_add_manager_type_and_locations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback_submissions",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("feedback_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_user_id_created_at", "feedback_submissions", ["user_id", "created_at"])
    op.create_index("ix_feedback_company_id_created_at", "feedback_submissions", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_company_id_created_at", table_name="feedback_submissions")
    op.drop_index("ix_feedback_user_id_created_at", table_name="feedback_submissions")
    op.drop_table("feedback_submissions")
