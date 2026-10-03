"""Make employee_roles.skill_level nullable

Revision ID: 0038_make_employee_roles_skill_level_nullable
Revises: 0037_add_time_entries_and_payroll_exports
Create Date: 2026-10-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0038_make_employee_roles_skill_level_nullable'
down_revision = '0037_add_time_entries_and_payroll_exports'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make skill_level nullable
    op.alter_column('employee_roles', 'skill_level',
               existing_type=sa.Numeric(precision=3, scale=1),
               nullable=True)


def downgrade() -> None:
    # Revert to NOT NULL
    op.alter_column('employee_roles', 'skill_level',
               existing_type=sa.Numeric(precision=3, scale=1),
               nullable=False)
