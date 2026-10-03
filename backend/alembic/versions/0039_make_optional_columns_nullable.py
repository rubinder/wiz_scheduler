"""Make optional columns nullable for external imports (7shifts, Deputy)

Revision ID: 0039_make_optional_columns_nullable
Revises: 0038b_make_employee_roles_skill_level_nullable
Create Date: 2026-10-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0039_make_optional_columns_nullable'
down_revision = '0038b_make_employee_roles_skill_level_nullable'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make employee columns nullable for import compatibility
    op.alter_column('employees', 'user_id',
               existing_type=sa.String(length=8),
               nullable=True)

    op.alter_column('employees', 'location_ids',
               existing_type=sa.JSON(),
               nullable=True)

    op.alter_column('employees', 'max_hours_per_week',
               existing_type=sa.Numeric(precision=5, scale=2),
               nullable=True)

    op.alter_column('employees', 'pay_rate',
               existing_type=sa.Numeric(precision=10, scale=2),
               nullable=True)

    op.alter_column('employees', 'hire_date',
               existing_type=sa.Date(),
               nullable=True)

    op.alter_column('employees', 'seniority_rank',
               existing_type=sa.Integer(),
               nullable=True)

    # Make location columns nullable for import compatibility
    op.alter_column('locations', 'address',
               existing_type=sa.String(),
               nullable=True)

    op.alter_column('locations', 'geo_coord',
               existing_type=sa.String(),
               nullable=True)

    op.alter_column('locations', 'min_rest_hours',
               existing_type=sa.Numeric(precision=3, scale=1),
               nullable=True)

    op.alter_column('locations', 'overtime_threshold_hours',
               existing_type=sa.Numeric(precision=5, scale=2),
               nullable=True)

    op.alter_column('locations', 'overtime_premium_multiplier',
               existing_type=sa.Numeric(precision=3, scale=2),
               nullable=True)

    # Make role columns nullable for import compatibility
    op.alter_column('roles', 'description',
               existing_type=sa.String(),
               nullable=True)

    # Make employee_availability columns nullable for import compatibility
    op.alter_column('employee_availability', 'start_time',
               existing_type=sa.Time(),
               nullable=True)

    op.alter_column('employee_availability', 'end_time',
               existing_type=sa.Time(),
               nullable=True)


def downgrade() -> None:
    # Revert all to NOT NULL
    op.alter_column('employees', 'user_id', existing_type=sa.String(length=8), nullable=False)
    op.alter_column('employees', 'location_ids', existing_type=sa.JSON(), nullable=False)
    op.alter_column('employees', 'max_hours_per_week', existing_type=sa.Numeric(precision=5, scale=2), nullable=False)
    op.alter_column('employees', 'pay_rate', existing_type=sa.Numeric(precision=10, scale=2), nullable=False)
    op.alter_column('employees', 'hire_date', existing_type=sa.Date(), nullable=False)
    op.alter_column('employees', 'seniority_rank', existing_type=sa.Integer(), nullable=False)
    op.alter_column('locations', 'address', existing_type=sa.String(), nullable=False)
    op.alter_column('locations', 'geo_coord', existing_type=sa.String(), nullable=False)
    op.alter_column('locations', 'min_rest_hours', existing_type=sa.Numeric(precision=3, scale=1), nullable=False)
    op.alter_column('locations', 'overtime_threshold_hours', existing_type=sa.Numeric(precision=5, scale=2), nullable=False)
    op.alter_column('locations', 'overtime_premium_multiplier', existing_type=sa.Numeric(precision=3, scale=2), nullable=False)
    op.alter_column('roles', 'description', existing_type=sa.String(), nullable=False)
    op.alter_column('employee_availability', 'start_time', existing_type=sa.Time(), nullable=False)
    op.alter_column('employee_availability', 'end_time', existing_type=sa.Time(), nullable=False)
