"""agent_daily_metrics (#259 step 10): one row per location per UTC day,
computed by backend/analytics/metrics.py from the same extracted rows the
lake lands, so the lake and the metrics agree. Company data (RLS keyed on
company, migration 0066), retained on the reservations window
(services/data_retention). The app reads these rows; it never queries the
lake at request time."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class AgentDailyMetric(Base):
    __tablename__ = "agent_daily_metrics"
    __table_args__ = (
        UniqueConstraint("location_id", "day", name="uq_agent_daily_metrics_location_day"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Conversations that started this UTC day, by outcome.
    conversations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # booked+modified+cancelled
    booked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handed_off: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abandoned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Of those, the ones that called find_slots (the booking conversion's
    # denominator).
    booking_intent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Handoff events created this day, by reason code ("other" for free text).
    handoff_reasons: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    llm_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_p95_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_p95_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    billed_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    screened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Service period name -> covers of booked reservations starting this
    # (location-local) day.
    covers_by_period: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
