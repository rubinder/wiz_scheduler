"""Add signal_config to shift_schedules for scheduling signal audit trail

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add signal_config column to store signal weights used when generating schedule
    op.add_column('shift_schedules', sa.Column('signal_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove signal_config column
    op.drop_column('shift_schedules', 'signal_config')
