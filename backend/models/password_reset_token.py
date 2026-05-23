from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class PasswordResetToken(Base):
    """Single-use token mailed to the user when they request a password reset.

    `used_at` makes the token single-use; `expires_at` is a 30-minute window.
    The token field is the value sent in the email URL and SELECTed by the
    /auth/reset-password endpoint — must be unique + indexed.
    """

    __tablename__ = "password_reset_tokens"

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
