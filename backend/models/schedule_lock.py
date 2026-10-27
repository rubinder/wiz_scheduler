from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ScheduleLock(Base):
    """One row per locked location (#180). An operation's whole lock set
    shares a `lock_group`, so it is released in one DELETE."""

    __tablename__ = "schedule_locks"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "location_id", name="uq_schedule_locks_company_location"
        ),
        Index("ix_schedule_locks_lock_group", "lock_group"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    lock_group: Mapped[str] = mapped_column(String(8), nullable=False)
    locked_by_user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
