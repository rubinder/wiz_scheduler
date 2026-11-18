"""The voice_numbers table (#259 step 6): one phone number per location for
the AI host's phone channel. The rules that write it live in
backend/reservations/voice_numbers.py; kept apart so backend.models can
register the table without an import cycle (as subscription_models)."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# requested: wanted, not yet claimed (or claimed but not yet routed to the
#   contact flow); the daily step retries it.
# active: claimed and routed to the AI host's contact flow.
# releasing: the subscription ended; held until release_after so a
#   resubscribe keeps the same number.
# released: given back to Amazon Connect.
VOICE_STATUSES = ("requested", "active", "releasing", "released")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class VoiceNumber(Base):
    __tablename__ = "voice_numbers"
    __table_args__ = (
        CheckConstraint(_in("status", VOICE_STATUSES), name="ck_voice_numbers_status"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id", ondelete="CASCADE"),
        nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, unique=True)
    # E.164 ("+12125550123"); NULL until claimed.
    e164: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
    connect_phone_number_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="requested")
    release_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class VoiceTransferCount(Base):
    """Staff transfers the AI host made at a location on a local date
    (reservations.voice_transfers.reserve): the per-day toll-fraud cap.
    Holds no diner data, so diner-data erasure leaves it alone."""

    __tablename__ = "voice_transfer_counts"

    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
