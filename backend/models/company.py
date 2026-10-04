from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Overtime scheduling defaults for this company (#134). NULL = fall back
    # to the next level down (location, then a 40h/1.5x code default) — see
    # backend/scheduling/cost_seniority.resolve_overtime_threshold. Paid-plan
    # gated at the API write layer.
    overtime_threshold_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    overtime_premium_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Default signal weights for scheduling at this company.
    # {"seniority_weight": 0.0, "pay_weight": 0.0, "overtime_weight": 0.0, "affinity_weight": 0.0}
    # NULL = no defaults set; location can still have its own defaults or schedules can override.
    signal_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
            name="ck_companies_overtime_premium_multiplier",
        ),
    )
