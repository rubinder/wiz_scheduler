"""Location-scoped schedule locks (#180).

schedule_locks goes from one row per company to one row per
(company_id, location_id). An operation inserts one row per location in
its lock set, all sharing a lock_group so the set is released together.

Existing rows are deleted rather than backfilled: they are short-lived
(TTL of seconds to two minutes) and have no location to carry over. RLS
already covers the table (0044), so nothing changes there.

Revision ID: 0054
Revises: 0053
"""
from alembic import op
import sqlalchemy as sa

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

T = "schedule_locks"


def upgrade() -> None:
    op.execute(f"DELETE FROM {T}")
    op.drop_constraint("uq_schedule_locks_company_id", T, type_="unique")
    op.add_column(T, sa.Column("location_id", sa.String(8), nullable=False))
    op.add_column(T, sa.Column("lock_group", sa.String(8), nullable=False))
    op.create_foreign_key(
        "fk_schedule_locks_location_id", T, "locations",
        ["location_id"], ["id"], ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_schedule_locks_company_location", T, ["company_id", "location_id"]
    )
    op.create_index("ix_schedule_locks_lock_group", T, ["lock_group"])


def downgrade() -> None:
    # One row per company cannot hold a multi-location set; locks are
    # short-lived, so drop them rather than pick a survivor.
    op.execute(f"DELETE FROM {T}")
    op.drop_index("ix_schedule_locks_lock_group", table_name=T)
    op.drop_constraint("uq_schedule_locks_company_location", T, type_="unique")
    op.drop_constraint("fk_schedule_locks_location_id", T, type_="foreignkey")
    op.drop_column(T, "lock_group")
    op.drop_column(T, "location_id")
    op.create_unique_constraint("uq_schedule_locks_company_id", T, ["company_id"])
