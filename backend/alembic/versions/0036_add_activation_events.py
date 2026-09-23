"""add activation_events table for the activation funnel (#115)

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-23 00:00:00.000000

Records five milestones per ownership group: signup, first_location,
first_employee, first_generation, upgraded. See backend/services/
activation.py for how rows are written (idempotent per
(ownership_group_id, event), never raises into the calling request) and
backend/scripts/run_activation_report.py for what reads them. Both the
table and the report are observation-only: nothing gates or changes
product behavior on this data, the same posture as 0033's signup signals.

Backfill is best effort, run once here, and deliberately partial:

  * signup <- ownership_groups.created_at. Always available — every group
    exists because someone registered it.
  * upgraded <- ownership_groups.created_at, for a currently-paid group
    only (stripe_subscription_id IS NOT NULL AND canceled_at IS NULL). This
    is a floor, not the true upgrade moment: nothing in the schema records
    when a subscription actually started (billing_charges only covers
    metered credit/overage charges, not the base subscription), so
    created_at is "whatever timestamp exists" per the design. A group that
    upgraded and later canceled is skipped rather than guessed at, since
    neither of its transition times is recoverable.
  * first_location / first_employee are SKIPPED entirely. Both `locations`
    and `employees` have no `created_at` column (see 0001_initial_schema.py
    and models/location.py, models/employee.py) and never have — there is
    no timestamp, sequence, or other ordering signal to recover "the
    earliest row" from. Inventing one (e.g. stamping every existing row
    with the group's signup time) would make every historical cohort look
    like it activated instantly, which is worse than an honest gap: new
    signups get both milestones recorded live going forward via
    services/activation.py, and old cohorts simply have no data point here
    (same reasoning 0033 gave for not backfilling signup signals at all).
  * first_generation is never backfilled — there is no record of *when* a
    location result last succeeded historically, and the acceptance
    criteria explicitly rule out fabricating it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activation_events",
        sa.Column("id", sa.String(length=8), nullable=False),
        sa.Column("ownership_group_id", sa.String(length=8), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("user_id", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(["ownership_group_id"], ["ownership_groups.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ownership_group_id", "event",
            name="uq_activation_events_group_event",
        ),
        sa.CheckConstraint(
            "event IN ('signup', 'first_location', 'first_employee', "
            "'first_generation', 'upgraded')",
            name="ck_activation_events_event",
        ),
    )
    op.create_index(
        "ix_activation_events_ownership_group_id",
        "activation_events", ["ownership_group_id"],
    )

    conn = op.get_bind()

    # Best-effort backfill — see module docstring for exactly what each
    # branch does and does not cover. The 8-char id can't come from a
    # column default (generate_short_id is Python-side only), so it's
    # generated in SQL; a one-time backfill colliding on 8 hex chars is
    # astronomically unlikely, and ON CONFLICT DO NOTHING makes a collision
    # merely skip a row rather than fail the migration.
    conn.execute(sa.text("""
        INSERT INTO activation_events (id, ownership_group_id, event, occurred_at, user_id)
        SELECT substr(md5(random()::text || clock_timestamp()::text || og.id || 's'), 1, 8),
               og.id, 'signup', og.created_at, NULL
        FROM ownership_groups og
        ON CONFLICT (ownership_group_id, event) DO NOTHING
    """))

    conn.execute(sa.text("""
        INSERT INTO activation_events (id, ownership_group_id, event, occurred_at, user_id)
        SELECT substr(md5(random()::text || clock_timestamp()::text || og.id || 'u'), 1, 8),
               og.id, 'upgraded', og.created_at, NULL
        FROM ownership_groups og
        WHERE og.stripe_subscription_id IS NOT NULL AND og.canceled_at IS NULL
        ON CONFLICT (ownership_group_id, event) DO NOTHING
    """))


def downgrade() -> None:
    op.drop_index(
        "ix_activation_events_ownership_group_id", table_name="activation_events"
    )
    op.drop_table("activation_events")
