"""paid AI credits: auto-reload opt-in, 'purchase' charge kind

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-15 00:00:00.000000

Two changes for #64. The default for ownership_groups.autoreload_enabled
flips to false so a new group is never charged automatically until it
opts in from the purchase modal; existing rows keep their current value.
billing_charges.kind gains 'purchase' for explicit credit-pack charges.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KINDS_NEW = "kind IN ('autoreload', 'purchase', 'invoice_item_storage', 'invoice_item_employees')"
KINDS_OLD = "kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees')"


def upgrade() -> None:
    op.alter_column(
        "ownership_groups", "autoreload_enabled",
        server_default=sa.text("false"), existing_type=sa.Boolean(), existing_nullable=False,
    )
    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_NEW)


def downgrade() -> None:
    # Rows with kind='purchase' would violate the old constraint; that is the
    # correct signal rather than something to delete silently.
    op.drop_constraint("billing_charges_kind_check", "billing_charges", type_="check")
    op.create_check_constraint("billing_charges_kind_check", "billing_charges", KINDS_OLD)
    op.alter_column(
        "ownership_groups", "autoreload_enabled",
        server_default=sa.text("true"), existing_type=sa.Boolean(), existing_nullable=False,
    )
