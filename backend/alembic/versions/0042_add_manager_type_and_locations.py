"""Add manager_type to users and create manager_locations junction table

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add manager_type column to users table
    op.add_column('users', sa.Column('manager_type', sa.String(), nullable=True))

    # Create manager_locations junction table
    op.create_table(
        'manager_locations',
        sa.Column('id', sa.String(8), nullable=False),
        sa.Column('manager_id', sa.String(8), nullable=False),
        sa.Column('location_id', sa.String(8), nullable=False),
        sa.ForeignKeyConstraint(['manager_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('manager_id', 'location_id', name='uq_manager_locations')
    )
    op.create_index('ix_manager_locations_manager_id', 'manager_locations', ['manager_id'])
    op.create_index('ix_manager_locations_location_id', 'manager_locations', ['location_id'])

    # Backfill: Set all existing managers to 'admin' with access to all locations
    # First, set manager_type = 'admin' for all users with user_role = 'manager'
    op.execute(
        "UPDATE users SET manager_type = 'admin' WHERE user_role = 'manager'"
    )

    # For each admin manager, grant access to all locations in their company
    op.execute(
        """
        INSERT INTO manager_locations (id, manager_id, location_id)
        SELECT
            substr(md5(random()::text), 1, 8),
            u.id,
            l.id
        FROM users u
        JOIN locations l ON u.company_id = l.company_id
        WHERE u.user_role = 'manager' AND u.manager_type = 'admin'
        ON CONFLICT (manager_id, location_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Drop manager_locations table
    op.drop_index('ix_manager_locations_location_id', table_name='manager_locations')
    op.drop_index('ix_manager_locations_manager_id', table_name='manager_locations')
    op.drop_table('manager_locations')

    # Remove manager_type column from users table
    op.drop_column('users', 'manager_type')
