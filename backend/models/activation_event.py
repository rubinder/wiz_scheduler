from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# Kept here (not just in services/activation.py) so the DB-level CHECK
# constraint in the 0036 migration and the Python-side callers can't drift
# apart without a diff touching both.
ACTIVATION_EVENTS = (
    "signup",
    "first_location",
    "first_employee",
    "first_generation",
    "upgraded",
)


class ActivationEvent(Base):
    """One row per (ownership_group, milestone) reached in the activation
    funnel: signup -> first_location -> first_employee -> first_generation
    -> upgraded.

    Unique on (ownership_group_id, event) so recording is idempotent by
    construction: a repeat action for a milestone already reached inserts
    nothing, rather than trusting every call site to check first. Written
    by services/activation.record_milestone, read by
    scripts/run_activation_report.py. Neither this table nor anything that
    reads it may gate or change product behavior — it is observation only,
    the same posture as services/signup_signals.py and services/
    abuse_report.py.
    """

    __tablename__ = "activation_events"
    __table_args__ = (
        UniqueConstraint(
            "ownership_group_id", "event", name="uq_activation_events_group_event"
        ),
        CheckConstraint(
            "event IN ('signup', 'first_location', 'first_employee', "
            "'first_generation', 'upgraded')",
            name="ck_activation_events_event",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Who triggered it, when known. NULL for backfilled rows and for
    # webhook-driven milestones (upgraded) where no single user initiated it
    # from an authenticated request.
    user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
