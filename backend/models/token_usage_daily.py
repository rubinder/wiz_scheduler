from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class TokenUsageDaily(Base):
    """Per-day Anthropic spend aggregate, one row per (OG, day).

    Sister table to TokenUsage (monthly). Written alongside TokenUsage in
    record_token_usage() so the daily circuit breaker has a real
    last-24h figure to query without summing per-call logs we don't keep.
    """
    __tablename__ = "token_usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "ownership_group_id", "usage_date",
            name="uq_token_usage_daily_group_date",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cost_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
