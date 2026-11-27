"""reservation_rules.flows_version (decision 104, #259 follow-up).

The conversation-flow page's stale-save check: bumped only when the flows or
their editors change, so a save from a copy loaded before a colleague's flow
save is 409 flow_conflict, while a colleague's hours or greeting edit (which
bumps `version`) is not a conflict. Existing rows start at 0, which is what
every open flow page loaded (the field did not exist before).

Revision ID: 0070
Revises: 0069
"""
from alembic import op
import sqlalchemy as sa

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reservation_rules", sa.Column(
        "flows_version", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("reservation_rules", "flows_version")
