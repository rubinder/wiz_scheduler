"""Tables of the AI host subscription (#259 step 5); the rules that read
them live in backend/reservations/subscription.py. Kept apart from it so
backend.models can register them without an import cycle."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

PRODUCT = "reservation_agent"
STATUSES = ("incomplete", "active", "trialing", "past_due", "canceled", "unpaid")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class ReservationSubscription(Base):
    __tablename__ = "reservation_subscriptions"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES),
                        name="ck_reservation_subscriptions_status"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id", ondelete="CASCADE"),
        nullable=False, unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class AgentUsage(Base):
    __tablename__ = "agent_usage"
    __table_args__ = (
        CheckConstraint(_in("product", (PRODUCT,)), name="ck_agent_usage_product"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id", ondelete="CASCADE"),
        nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("agent_conversations.id", ondelete="SET NULL"),
        nullable=True, unique=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    billed_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    product: Mapped[str] = mapped_column(
        String, nullable=False, default=PRODUCT, server_default=text(f"'{PRODUCT}'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
