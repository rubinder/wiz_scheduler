"""Restore ON DELETE CASCADE lost during UUID-to-short-ID migration

The bfbb0671ec23 migration recreated all foreign keys without preserving
ondelete="CASCADE" on the three FKs that originally had it.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, ref_table, ref_column, fk_name)
CASCADE_FKS = [
    ("condensed_role_mappings", "condensed_role_id", "condensed_roles", "id", "fk_condensed_role_mappings_condensed_role_id"),
    ("condensed_role_mappings", "role_id", "roles", "id", "fk_condensed_role_mappings_role_id"),
    ("employee_invites", "employee_id", "employees", "id", "fk_employee_invites_employee_id"),
]


def upgrade() -> None:
    for table, col, ref_table, ref_col, fk_name in CASCADE_FKS:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name, table, ref_table, [col], [ref_col], ondelete="CASCADE"
        )


def downgrade() -> None:
    for table, col, ref_table, ref_col, fk_name in CASCADE_FKS:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name, table, ref_table, [col], [ref_col]
        )
