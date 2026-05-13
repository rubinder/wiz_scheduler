from datetime import datetime

from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class OwnershipGroup(Base):
    __tablename__ = "ownership_groups"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_credits_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    autoreload_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    autoreload_threshold_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("2.0")
    )
    autoreload_amount_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("10.0")
    )
    autoreload_failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    default_payment_method_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Which external integration feeds data into this group (e.g. "7shifts").
    # NULL means the group manages data manually without an integration.
    api_integration: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
