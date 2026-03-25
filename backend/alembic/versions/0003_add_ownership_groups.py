"""Add ownership groups, company.ownership_group_id, and employee_companies junction table

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ownership_groups table
    op.create_table(
        "ownership_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # Add ownership_group_id FK to companies
    op.add_column(
        "companies",
        sa.Column("ownership_group_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_companies_ownership_group_id",
        "companies",
        "ownership_groups",
        ["ownership_group_id"],
        ["id"],
    )
    op.create_index("ix_companies_ownership_group_id", "companies", ["ownership_group_id"])

    # Create employee_companies junction table
    op.create_table(
        "employee_companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.UniqueConstraint("employee_id", "company_id", name="uq_employee_company"),
    )
    op.create_index("ix_employee_companies_employee_id", "employee_companies", ["employee_id"])
    op.create_index("ix_employee_companies_company_id", "employee_companies", ["company_id"])


def downgrade() -> None:
    op.drop_table("employee_companies")
    op.drop_index("ix_companies_ownership_group_id", table_name="companies")
    op.drop_constraint("fk_companies_ownership_group_id", "companies", type_="foreignkey")
    op.drop_column("companies", "ownership_group_id")
    op.drop_table("ownership_groups")
