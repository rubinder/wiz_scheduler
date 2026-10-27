"""Employee-submitted scheduling preferences awaiting manager approval (#189)."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

PREFERENCE_SUBMISSION_STATUSES = ("pending", "approved", "denied", "superseded")


class EmployeePreferenceSubmission(Base):
    __tablename__ = "employee_preference_submissions"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    approved_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'superseded')",
            name="ck_pref_submission_status",
        ),
        Index(
            "uq_pref_submission_one_pending",
            "employee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
