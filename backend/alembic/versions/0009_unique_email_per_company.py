"""change users unique email to unique (email, company_id)

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-26
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.create_unique_constraint("uq_users_email_company", "users", ["email", "company_id"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email_company", "users", type_="unique")
    op.create_unique_constraint("users_email_key", "users", ["email"])
