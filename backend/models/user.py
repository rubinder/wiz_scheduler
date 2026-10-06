from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(String(8), ForeignKey("companies.id"), nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    user_role: Mapped[str] = mapped_column(String, nullable=False)  # 'manager' | 'employee'
    # Manager type: 'admin' (full access) | 'regular' (location-scoped access)
    # Only applies if user_role == 'manager'. NULL for employees.
    manager_type: Mapped[str | None] = mapped_column(String, nullable=True)  # 'admin' | 'regular'
    google_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Set once the address is proven: the user clicked a link we mailed to it,
    # or Google asserted email_verified on an ID token. NULL means unproven,
    # which blocks schedule generation (services.email_verification) but
    # nothing else.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Canonical form of `email` (see utils.email_normalize). Deliberately NOT
    # unique — one address legitimately owns several ownership groups. Stored
    # so serial signups from one mailbox cluster when we look.
    email_normalized: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )

    # Relationship to manager locations (only populated for regular managers)
    manager_locations: Mapped[list["ManagerLocation"]] = relationship(
        "ManagerLocation", back_populates="manager", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        sa.UniqueConstraint("email", "company_id", name="uq_users_email_company"),
    )
