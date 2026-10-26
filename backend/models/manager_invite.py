from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ManagerInvite(Base):
    __tablename__ = "manager_invites"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False, index=True
    )
    invited_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_company_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=True
    )
    # 'admin' | 'regular' (#149). A regular invite carries the locations to
    # assign on acceptance; ids that no longer exist then are skipped.
    manager_type: Mapped[str] = mapped_column(
        String, server_default="admin", default="admin", nullable=False
    )
    location_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
