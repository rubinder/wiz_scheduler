"""AI host voice channel (#259 step 6): bridge turn dedupe.

agent_conversations.last_seq / last_reply: the voice bridge's last answered
turn (the Lambda's sequence number) and the reply it got, so a retried turn
is answered again without running the agent twice.

Revision ID: 0061
Revises: 0060
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_conversations",
                  sa.Column("last_seq", sa.Integer(), nullable=True))
    op.add_column("agent_conversations",
                  sa.Column("last_reply", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_conversations", "last_reply")
    op.drop_column("agent_conversations", "last_seq")
