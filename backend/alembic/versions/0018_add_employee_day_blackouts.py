"""Add employee_day_blackouts table

Stores recurring per-day-of-week time windows during which an employee
must NOT be scheduled. E.g. "employee X cannot work 20:00-22:00 on Mondays".

Day of week is stored as 0..6 where 0 = Monday (matches Python's
datetime.weekday()). Times are stored as zero-padded HH:MM strings.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_day_blackouts",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("company_id", sa.String(8), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "employee_id",
            sa.String(8),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),  # "HH:MM"
        sa.Column("end_time", sa.String(5), nullable=False),    # "HH:MM"
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6",
            name="ck_employee_day_blackouts_dow",
        ),
    )
    op.create_index(
        "ix_employee_day_blackouts_employee_id",
        "employee_day_blackouts",
        ["employee_id"],
    )
    op.create_index(
        "ix_employee_day_blackouts_company_id",
        "employee_day_blackouts",
        ["company_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_employee_day_blackouts_company_id", table_name="employee_day_blackouts")
    op.drop_index("ix_employee_day_blackouts_employee_id", table_name="employee_day_blackouts")
    op.drop_table("employee_day_blackouts")
