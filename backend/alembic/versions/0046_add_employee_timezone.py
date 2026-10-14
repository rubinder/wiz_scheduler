"""Add employees.timezone (optional; NULL = availability is location-local).

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "timezone")
