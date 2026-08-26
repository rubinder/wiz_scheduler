"""add scheduling preference tables

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_day_preferences",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column(
            "weight", sa.Numeric(2, 1), nullable=False, server_default=sa.text("0.7")
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "day_of_week BETWEEN 0 AND 6", name="ck_employee_day_preferences_dow"
        ),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 1", name="ck_employee_day_preferences_weight"
        ),
        sa.UniqueConstraint(
            "employee_id", "day_of_week", name="uq_employee_day_preferences"
        ),
    )
    op.create_index(
        "ix_employee_day_preferences_company_id",
        "employee_day_preferences",
        ["company_id"],
    )
    op.create_index(
        "ix_employee_day_preferences_employee_id",
        "employee_day_preferences",
        ["employee_id"],
    )

    for table, extra_checks in (
        ("employee_hour_range_preferences", []),
        (
            "employee_hour_range_caps",
            [sa.CheckConstraint("max_per_week >= 0", name="ck_employee_hour_range_caps_max")],
        ),
    ):
        columns = [
            sa.Column("id", sa.String(length=8), nullable=False),
            sa.Column("company_id", sa.String(length=8), nullable=False),
            sa.Column("employee_id", sa.String(length=8), nullable=False),
            sa.Column("start_time", sa.String(length=5), nullable=False),
            sa.Column("end_time", sa.String(length=5), nullable=False),
        ]
        if table == "employee_hour_range_caps":
            columns.append(sa.Column("max_per_week", sa.SmallInteger(), nullable=False))
        columns.append(
            sa.Column(
                "weight", sa.Numeric(2, 1), nullable=False, server_default=sa.text("0.7")
            )
        )
        op.create_table(
            table,
            *columns,
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("weight >= 0 AND weight <= 1", name=f"ck_{table}_weight"),
            *extra_checks,
            sa.UniqueConstraint(
                "employee_id", "start_time", "end_time", name=f"uq_{table}"
            ),
        )
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.create_index(f"ix_{table}_employee_id", table, ["employee_id"])


def downgrade() -> None:
    op.drop_table("employee_hour_range_caps")
    op.drop_table("employee_hour_range_preferences")
    op.drop_table("employee_day_preferences")
