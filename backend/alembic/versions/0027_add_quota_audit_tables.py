"""add og_email_send_log, integration_imports, gdpr_export_log

Three audit tables backing the Tier-2 quota guards (#42, #44, #45). All
keyed by OG or user with a single timestamp column + index — enough to
answer "how many in the last N minutes/hours?" without a separate
counter cache.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "og_email_send_log",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "ownership_group_id",
            sa.String(length=8),
            sa.ForeignKey("ownership_groups.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_og_email_send_log_group_sent_at",
        "og_email_send_log",
        ["ownership_group_id", "sent_at"],
    )

    op.create_table(
        "integration_imports",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "ownership_group_id",
            sa.String(length=8),
            sa.ForeignKey("ownership_groups.id"),
            nullable=False,
        ),
        sa.Column("integration", sa.String(length=64), nullable=False),
        sa.Column(
            "location_id",
            sa.String(length=8),
            sa.ForeignKey("locations.id"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_integration_imports_group_integration_started",
        "integration_imports",
        ["ownership_group_id", "integration", "started_at"],
    )

    op.create_table(
        "gdpr_export_log",
        sa.Column("id", sa.String(length=8), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=8),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "exported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_gdpr_export_log_user_exported_at",
        "gdpr_export_log",
        ["user_id", "exported_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_gdpr_export_log_user_exported_at", table_name="gdpr_export_log")
    op.drop_table("gdpr_export_log")

    op.drop_index(
        "ix_integration_imports_group_integration_started",
        table_name="integration_imports",
    )
    op.drop_table("integration_imports")

    op.drop_index("ix_og_email_send_log_group_sent_at", table_name="og_email_send_log")
    op.drop_table("og_email_send_log")
