from sqlalchemy import CheckConstraint, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    region_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("regions.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    geo_coord: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Minimum rest (hours) required between an employee's shifts on different
    # days. NULL = no constraint. Set to 11 for NYC Fair Workweek "clopening"
    # compliance (fast food). Enforced as a hard constraint by the scheduler.
    min_rest_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Per-location override of the company default. NULL = inherit.
    overtime_threshold_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    overtime_premium_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Default signal weights for scheduling at this location.
    # Overrides company defaults when set. NULL = inherit from company.
    # {"seniority_weight": 0.0, "pay_weight": 0.0, "overtime_weight": 0.0, "affinity_weight": 0.0}
    signal_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "overtime_premium_multiplier IS NULL OR overtime_premium_multiplier >= 1",
            name="ck_locations_overtime_premium_multiplier",
        ),
    )
