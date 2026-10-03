"""add cost and seniority scheduling fields

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-30 00:00:00.000000

Pay rate, overtime threshold/multiplier, and seniority as opt-in scheduling
signals (#134). All columns nullable, no backfill — NULL is the "manager
hasn't opted in" state and must produce unchanged scheduling behavior.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("pay_rate", sa.Numeric(8, 2), nullable=True))
    op.add_column("employees", sa.Column("hire_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("seniority_rank", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "ck_employees_seniority_rank", "employees", "seniority_rank > 0"
    )

    op.add_column("companies", sa.Column("overtime_threshold_hours", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("overtime_premium_multiplier", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_companies_overtime_premium_multiplier", "companies",
        "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
    )

    op.add_column("locations", sa.Column("overtime_threshold_hours", sa.Float(), nullable=True))
    op.add_column("locations", sa.Column("overtime_premium_multiplier", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_locations_overtime_premium_multiplier", "locations",
        "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_locations_overtime_premium_multiplier", "locations", type_="check")
    op.drop_column("locations", "overtime_premium_multiplier")
    op.drop_column("locations", "overtime_threshold_hours")

    op.drop_constraint("ck_companies_overtime_premium_multiplier", "companies", type_="check")
    op.drop_column("companies", "overtime_premium_multiplier")
    op.drop_column("companies", "overtime_threshold_hours")

    op.drop_constraint("ck_employees_seniority_rank", "employees", type_="check")
    op.drop_column("employees", "seniority_rank")
    op.drop_column("employees", "hire_date")
    op.drop_column("employees", "pay_rate")
