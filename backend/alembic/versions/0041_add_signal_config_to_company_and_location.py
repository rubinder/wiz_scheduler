"""Add signal_config to company and location for default signal weight settings

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add signal_config column to companies table
    op.add_column('companies', sa.Column('signal_config', sa.JSON(), nullable=True))

    # Add signal_config column to locations table
    op.add_column('locations', sa.Column('signal_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove signal_config column from locations table
    op.drop_column('locations', 'signal_config')

    # Remove signal_config column from companies table
    op.drop_column('companies', 'signal_config')
