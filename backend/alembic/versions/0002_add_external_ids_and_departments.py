"""Add external_id columns and departments table

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add external_id to existing tables
    op.add_column("companies", sa.Column("external_id", sa.String, nullable=True))
    op.add_column("regions", sa.Column("external_id", sa.String, nullable=True))
    op.add_column("locations", sa.Column("external_id", sa.String, nullable=True))
    op.add_column("roles", sa.Column("external_id", sa.String, nullable=True))
    op.add_column("employees", sa.Column("external_id", sa.String, nullable=True))

    # Create departments table
    op.create_table(
        "departments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("external_id", sa.String, nullable=True),
    )
    op.create_index("ix_departments_company_id", "departments", ["company_id"])


def downgrade() -> None:
    op.drop_table("departments")
    op.drop_column("employees", "external_id")
    op.drop_column("roles", "external_id")
    op.drop_column("locations", "external_id")
    op.drop_column("regions", "external_id")
    op.drop_column("companies", "external_id")
