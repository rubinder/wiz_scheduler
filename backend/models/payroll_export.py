"""Per-export audit row: who pulled which hours, when, covering what.

Shaped like integration_imports (#44) and gdpr_export_log (#45). This slice
has no cooldown — a CSV download costs nothing and hits no third party — so
the table is audit only, and the quota shape is available later without a
schema change.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class PayrollExport(Base):
    __tablename__ = "payroll_exports"
    __table_args__ = (
        CheckConstraint("format IN ('csv')", name="payroll_exports_format_check"),
        Index("ix_payroll_exports_company_created", "company_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    # NULL only for the seed/dev company with no ownership group, which
    # get_plan_state treats as unlimited and which therefore passes
    # assert_paid_plan.
    ownership_group_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=True
    )
    exported_by_user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL = every location in range.
    location_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=True
    )
    range_start: Mapped[date] = mapped_column(Date, nullable=False)
    range_end: Mapped[date] = mapped_column(Date, nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_minutes_total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
