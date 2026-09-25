"""add time_entries and payroll_exports

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-23 00:00:00.000000

Payroll slice 1 (#78). payroll_exports is created first because
time_entries.payroll_export_id references it.

No backfill: every entry is derived on demand from approved shifts and
existing check-ins, so history becomes available the moment a manager picks
a past range — bounded by RETENTION_CHECKINS_DAYS.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payroll_exports",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("ownership_group_id", sa.String(length=8), nullable=True),
        sa.Column("exported_by_user_id", sa.String(length=8), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=True),
        sa.Column("range_start", sa.Date(), nullable=False),
        sa.Column("range_end", sa.Date(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("paid_minutes_total", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["ownership_group_id"], ["ownership_groups.id"]),
        sa.ForeignKeyConstraint(["exported_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("format IN ('csv')",
                           name="payroll_exports_format_check"),
    )
    op.create_index("ix_payroll_exports_company_id", "payroll_exports",
                    ["company_id"])
    op.create_index("ix_payroll_exports_company_created", "payroll_exports",
                    ["company_id", "created_at"])

    op.create_table(
        "time_entries",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("company_id", sa.String(length=8), nullable=False),
        sa.Column("location_id", sa.String(length=8), nullable=False),
        sa.Column("employee_id", sa.String(length=8), nullable=False),
        sa.Column("shift_id", sa.String(length=8), nullable=False),
        sa.Column("role_id", sa.String(length=8), nullable=False),
        sa.Column("role_name", sa.String(), nullable=False),
        sa.Column("pay_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_minutes", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("check_in_id", sa.String(length=8), nullable=True),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lateness_minutes", sa.Integer(), nullable=True),
        sa.Column("attested_by_user_id", sa.String(length=8), nullable=True),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attestation_reason", sa.String(length=500), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=8), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payroll_export_id", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["shifts.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["check_in_id"], ["employee_check_ins.id"]),
        sa.ForeignKeyConstraint(["attested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["payroll_export_id"], ["payroll_exports.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shift_id", name="uq_time_entries_shift"),
        sa.CheckConstraint("source IN ('checked_in', 'manager_attested')",
                           name="time_entries_source_check"),
        sa.CheckConstraint(
            "source <> 'manager_attested' OR (attested_by_user_id IS NOT NULL "
            "AND attested_at IS NOT NULL)",
            name="time_entries_attested_check",
        ),
        sa.CheckConstraint("paid_minutes > 0",
                           name="time_entries_paid_minutes_check"),
    )
    op.create_index("ix_time_entries_company_id", "time_entries", ["company_id"])
    op.create_index("ix_time_entries_company_pay_date", "time_entries",
                    ["company_id", "pay_date"])
    op.create_index("ix_time_entries_company_location_pay_date", "time_entries",
                    ["company_id", "location_id", "pay_date"])
    op.create_index("ix_time_entries_company_employee_pay_date", "time_entries",
                    ["company_id", "employee_id", "pay_date"])


def downgrade() -> None:
    op.drop_index("ix_time_entries_company_employee_pay_date",
                  table_name="time_entries")
    op.drop_index("ix_time_entries_company_location_pay_date",
                  table_name="time_entries")
    op.drop_index("ix_time_entries_company_pay_date", table_name="time_entries")
    op.drop_index("ix_time_entries_company_id", table_name="time_entries")
    op.drop_table("time_entries")
    op.drop_index("ix_payroll_exports_company_created",
                  table_name="payroll_exports")
    op.drop_index("ix_payroll_exports_company_id", table_name="payroll_exports")
    op.drop_table("payroll_exports")
