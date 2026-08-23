"""add employee_check_ins

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_check_ins",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("shift_id", sa.String(length=8), nullable=True),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("minutes_from_start", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "local_date", "counter",
                            name="uq_employee_check_ins_location_date_counter"),
    )
    op.create_index("ix_employee_check_ins_company_id",
                    "employee_check_ins", ["company_id"])
    op.create_index("ix_employee_check_ins_location_id",
                    "employee_check_ins", ["location_id"])
    op.create_index("ix_employee_check_ins_employee_id",
                    "employee_check_ins", ["employee_id"])
    op.create_index("ix_employee_check_ins_location_date",
                    "employee_check_ins",
                    ["company_id", "location_id", "local_date"])
    op.create_index("ix_employee_check_ins_employee_date",
                    "employee_check_ins",
                    ["company_id", "employee_id", "local_date"])


def downgrade() -> None:
    op.drop_index("ix_employee_check_ins_employee_date",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_location_date",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_employee_id",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_location_id",
                  table_name="employee_check_ins")
    op.drop_index("ix_employee_check_ins_company_id",
                  table_name="employee_check_ins")
    op.drop_table("employee_check_ins")
