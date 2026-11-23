"""AI floor-plan drafts (decision 101): one row per request, holding the
status and the proposed floor, never the uploaded files or the notes. Deleted
after RETENTION_FLOOR_PLAN_DRAFTS_DAYS by the retention sweep; a group or
location deletion cascades to them."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STATUSES = (QUEUED, RUNNING, DONE, FAILED)


class FloorPlanDraft(Base):
    __tablename__ = "floor_plan_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_floor_plan_drafts_status"),
        Index("ix_floor_plan_drafts_company_id", "company_id"),
        Index("ix_floor_plan_drafts_location_id", "location_id"),
        Index("ix_floor_plan_drafts_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=QUEUED)
    # A FloorIn payload once done.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[str | None] = mapped_column(String(6), nullable=True)
    # The credit this draft holds back while in flight: its counted worst
    # case, at least FLOOR_PLAN_AI_MIN_CREDITS_USD.
    reserved_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    # What was debited from AI credits (at cost); NULL when nothing was.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
