"""add special_hours_days and shift_templates.specific_date

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shift_templates",
        sa.Column("specific_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_shift_templates_location_specific_date",
        "shift_templates",
        ["location_id", "specific_date"],
        postgresql_where=sa.text("specific_date IS NOT NULL"),
    )

    op.create_table(
        "special_hours_days",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column("company_id", sa.String(length=8),
                  sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", sa.String(length=8),
                  sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=False),
        sa.Column("close_time", sa.Time(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("shift_template_id", sa.String(length=8),
                  sa.ForeignKey("shift_templates.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("location_id", "date",
                            name="uq_special_hours_days_location_date"),
    )
    op.create_index("ix_special_hours_days_company_id",
                    "special_hours_days", ["company_id"])
    op.create_index("ix_special_hours_days_location_id",
                    "special_hours_days", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_special_hours_days_location_id",
                  table_name="special_hours_days")
    op.drop_index("ix_special_hours_days_company_id",
                  table_name="special_hours_days")
    op.drop_table("special_hours_days")
    op.drop_index("ix_shift_templates_location_specific_date",
                  table_name="shift_templates")
    op.drop_column("shift_templates", "specific_date")
