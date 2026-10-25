"""Give managers created without a manager_type full access (#149).

0042 backfilled every existing manager to 'admin', but registration and
manager-invite acceptance kept creating managers with manager_type NULL,
which the location-permission helpers treat as having no locations.
Those rows become 'admin', matching every other manager.

Revision ID: 0049
Revises: 0048
"""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE users SET manager_type = 'admin' "
        "WHERE user_role = 'manager' AND manager_type IS NULL"
    )


def downgrade() -> None:
    # Which rows were NULL before is not recorded; 'admin' is what they
    # effectively had everywhere except the location helpers.
    pass
