"""Add condensed_roles and condensed_role_mappings tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "condensed_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
    )
    op.create_index("ix_condensed_roles_company_id", "condensed_roles", ["company_id"])

    op.create_table(
        "condensed_role_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "condensed_role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("condensed_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_condensed_role_mappings_condensed_role_id", "condensed_role_mappings", ["condensed_role_id"])
    op.create_index("ix_condensed_role_mappings_role_id", "condensed_role_mappings", ["role_id"])


def downgrade() -> None:
    op.drop_table("condensed_role_mappings")
    op.drop_table("condensed_roles")
