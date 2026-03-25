"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON, ARRAY

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- companies ---
    op.create_table(
        "companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("email", sa.String, unique=True, nullable=False),
        sa.Column("hashed_password", sa.String, nullable=False),
        sa.Column("full_name", sa.String, nullable=True),
        sa.Column("user_role", sa.String, nullable=False),
    )
    op.create_index("ix_users_company_id", "users", ["company_id"])

    # --- regions ---
    op.create_table(
        "regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("geo_bounds", JSON, nullable=True),
    )

    # --- locations ---
    op.create_table(
        "locations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("region_id", UUID(as_uuid=True), sa.ForeignKey("regions.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("address", sa.String, nullable=True),
        sa.Column("geo_coord", JSON, nullable=True),
        sa.Column("timezone", sa.String, nullable=False),
    )

    # --- roles ---
    op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=True),
    )

    # --- employees ---
    op.create_table(
        "employees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("full_name", sa.String, nullable=False),
        sa.Column("email", sa.String, nullable=True),
        sa.Column("location_ids", ARRAY(UUID(as_uuid=True)), nullable=True),
    )

    # --- employee_roles ---
    op.create_table(
        "employee_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("skill_level", sa.Integer, nullable=False),
    )
    op.create_index("ix_employee_roles_company_id", "employee_roles", ["company_id"])
    op.create_index("ix_employee_roles_employee_id", "employee_roles", ["employee_id"])

    # --- employee_affinities ---
    op.create_table(
        "employee_affinities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("target_employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("level", sa.Numeric, nullable=False),
    )
    op.create_index("ix_employee_affinities_company_id", "employee_affinities", ["company_id"])

    # --- employee_availability ---
    op.create_table(
        "employee_availability",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("day", sa.Integer, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_employee_availability_company_id", "employee_availability", ["company_id"])
    op.create_index("ix_employee_availability_employee_id", "employee_availability", ["employee_id"])

    # --- shift_templates ---
    op.create_table(
        "shift_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("weekly_schedule", JSON, nullable=False),
    )
    op.create_index("ix_shift_templates_company_id", "shift_templates", ["company_id"])

    # --- shift_schedules ---
    op.create_table(
        "shift_schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("week_start_date", sa.Date, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="draft"),
        sa.Column("raw_llm_output", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_shift_schedules_company_id", "shift_schedules", ["company_id"])

    # --- shifts ---
    op.create_table(
        "shifts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("shift_schedule_id", UUID(as_uuid=True), sa.ForeignKey("shift_schedules.id"), nullable=False),
        sa.Column("location_id", UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("role_name", sa.String, nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shifts_company_id", "shifts", ["company_id"])
    op.create_index("ix_shifts_shift_schedule_id", "shifts", ["shift_schedule_id"])
    op.create_index("ix_shifts_employee_id", "shifts", ["employee_id"])


def downgrade() -> None:
    op.drop_table("shifts")
    op.drop_table("shift_schedules")
    op.drop_table("shift_templates")
    op.drop_table("employee_availability")
    op.drop_table("employee_affinities")
    op.drop_table("employee_roles")
    op.drop_table("employees")
    op.drop_table("roles")
    op.drop_table("locations")
    op.drop_table("regions")
    op.drop_table("users")
    op.drop_table("companies")
