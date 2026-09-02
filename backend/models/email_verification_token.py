from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class EmailVerificationToken(Base):
    """Single-use token mailed to a new user to prove they own the address.

    Deliberately the same shape as PasswordResetToken — `used_at` makes it
    single-use, `expires_at` bounds the window (48h rather than that flow's
    30 minutes, because nothing sensitive is behind this link and a signup
    email often sits unread overnight).
    """

    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
