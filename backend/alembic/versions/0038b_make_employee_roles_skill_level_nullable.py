"""Make employee_roles.skill_level nullable

Revision ID: 0038b_make_employee_roles_skill_level_nullable
Revises: 0038_add_cost_and_seniority_fields
Create Date: 2026-10-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0038b'
down_revision = '0038'
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
