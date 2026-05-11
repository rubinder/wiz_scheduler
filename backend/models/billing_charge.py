from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class BillingCharge(Base):
    __tablename__ = "billing_charges"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    stripe_object_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    period: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('autoreload', 'invoice_item_storage', 'invoice_item_employees')",
            name="billing_charges_kind_check",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'pending')",
            name="billing_charges_status_check",
        ),
        Index(
            "ix_billing_charges_og_kind_period",
            "ownership_group_id", "kind", "period",
        ),
        Index(
            "ix_billing_charges_og_created",
            "ownership_group_id", "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )
