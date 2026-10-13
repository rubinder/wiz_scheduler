"""Feedback submission tracking for rate limiting (free plan only)."""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class FeedbackSubmission(Base):
    __tablename__ = "feedback_submissions"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    user_id: Mapped[str] = mapped_column(String(8), ForeignKey("users.id"), nullable=False)
    company_id: Mapped[str] = mapped_column(String(8), ForeignKey("companies.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_feedback_user_id_created_at", "user_id", "created_at"),
        Index("ix_feedback_company_id_created_at", "company_id", "created_at"),
    )
