"""Comped AI host subscriptions (#259 step 11).

reservation_subscriptions.status may be 'comped': a group the operator gives
the AI host free of charge (the demo tenant). The row has no Stripe
subscription, and no Stripe path acts on it.

Downgrade refuses while a comped row exists, like 0059 refuses with agent
charges: converting it to a Stripe status would either switch the agent off
for that group ('canceled') or claim a Stripe subscription that does not
exist ('active'), and the choice belongs to the operator, not the migration.

Revision ID: 0067
Revises: 0066
"""
from alembic import op
import sqlalchemy as sa

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_reservation_subscriptions_status"
TABLE = "reservation_subscriptions"
STATUSES_OLD = ("incomplete", "incomplete_expired", "active", "trialing", "past_due",
                "canceled", "unpaid", "paused")
STATUSES_NEW = STATUSES_OLD + ("comped",)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _in("status", STATUSES_NEW))


def downgrade() -> None:
    n = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE status = 'comped'")).scalar()
    if n:
        raise RuntimeError(
            f"{n} comped reservation_subscriptions row(s) exist; downgrading 0067 "
            f"would violate {CONSTRAINT}. Delete them or give them a Stripe "
            "status first.")
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _in("status", STATUSES_OLD))
