"""Add strategy tracking to shift_schedules and employee_role_minutes table

Revision ID: 0011
Revises: bfbb0671ec23
Create Date: 2026-04-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "bfbb0671ec23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add strategy columns to shift_schedules
    op.add_column("shift_schedules", sa.Column("strategy", sa.String(), nullable=True))
    op.add_column("shift_schedules", sa.Column("strategy_param", sa.Float(), nullable=True))

    # Create employee_role_minutes table
    op.create_table(
        "employee_role_minutes",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.String(8), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("role_id", sa.String(8), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("total_minutes", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "company_id", "employee_id", "role_id", "month_start",
            name="uq_emp_role_month",
        ),
    )
    op.create_index(
        "ix_employee_role_minutes_company_id",
        "employee_role_minutes",
        ["company_id"],
    )
    op.create_index(
        "ix_employee_role_minutes_employee_role",
        "employee_role_minutes",
        ["employee_id", "role_id"],
    )


def downgrade() -> None:
    op.drop_table("employee_role_minutes")
    op.drop_column("shift_schedules", "strategy_param")
    op.drop_column("shift_schedules", "strategy")
