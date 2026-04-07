from datetime import datetime

from typing import Optional

from sqlalchemy import DateTime, Float, String, text
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
