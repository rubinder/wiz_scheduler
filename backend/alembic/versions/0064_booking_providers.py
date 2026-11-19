"""AI host booking providers (#259 step 9).

agent_conversations.provider: the booking provider a conversation books
through, fixed when it starts. Existing rows are WizScheduler's.

Revision ID: 0064
Revises: 0063
"""
from alembic import op
import sqlalchemy as sa

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_conversations",
                  sa.Column("provider", sa.String(), nullable=False,
                            server_default=sa.text("'wizscheduler'")))
    op.create_check_constraint(
        "ck_agent_conversations_provider", "agent_conversations",
        "provider IN ('wizscheduler', 'mock_external')")


def downgrade() -> None:
    op.drop_constraint("ck_agent_conversations_provider", "agent_conversations",
                       type_="check")
    op.drop_column("agent_conversations", "provider")
