"""add email verification tokens and user verification columns

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-31 00:00:00.000000

Backfill note: every EXISTING user is stamped verified. They predate the
gate, and the alternative is locking the whole install out of schedule
generation until each person happens to click a link nobody warned them
about. New signups from here on must prove the address.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=8),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token", name="uq_email_verification_tokens_token"),
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_email_verification_tokens_token",
        "email_verification_tokens",
        ["token"],
    )

    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users", sa.Column("email_normalized", sa.String(), nullable=True)
    )
    # NOT unique: one address legitimately owns several ownership groups
    # (see /auth/login's multiple_ownership_groups flow). Indexed for the
    # clustering queries only.
    op.create_index(
        "ix_users_email_normalized", "users", ["email_normalized"]
    )

    # Grandfather existing accounts (see module docstring).
    op.execute("UPDATE users SET email_verified_at = now()")

    # Backfill the normalized form for the same rows. Mirrors
    # backend/utils/email_normalize.normalize_email: lowercase, drop the
    # +tag, and additionally drop dots + fold googlemail for Google.
    op.execute(
        """
        UPDATE users
        SET email_normalized = CASE
            WHEN split_part(lower(email), '@', 2)
                 IN ('gmail.com', 'googlemail.com')
            THEN replace(
                     split_part(split_part(lower(email), '@', 1), '+', 1),
                     '.', ''
                 ) || '@gmail.com'
            ELSE split_part(split_part(lower(email), '@', 1), '+', 1)
                 || '@' || split_part(lower(email), '@', 2)
        END
        WHERE email LIKE '%@%'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_email_normalized", table_name="users")
    op.drop_column("users", "email_normalized")
    op.drop_column("users", "email_verified_at")
    op.drop_index(
        "ix_email_verification_tokens_token",
        table_name="email_verification_tokens",
    )
    op.drop_index(
        "ix_email_verification_tokens_user_id",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")
