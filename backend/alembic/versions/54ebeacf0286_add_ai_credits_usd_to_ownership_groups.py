"""add ai_credits_usd to ownership_groups

Revision ID: 54ebeacf0286
Revises: 0016
Create Date: 2026-04-06 11:12:25.980682

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '54ebeacf0286'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ownership_groups', sa.Column('stripe_customer_id', sa.String(), nullable=True))
    op.add_column('ownership_groups', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.add_column('ownership_groups', sa.Column('ai_credits_usd', sa.Float(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    op.drop_column('ownership_groups', 'ai_credits_usd')
    op.drop_column('ownership_groups', 'stripe_subscription_id')
    op.drop_column('ownership_groups', 'stripe_customer_id')
