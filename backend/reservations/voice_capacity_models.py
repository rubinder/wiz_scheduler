"""Voice call capacity tables (decision 103). The rules that write them live
in backend/reservations/voice_capacity.py; kept apart so backend.models can
register the tables without an import cycle (as voice_number_models).

Company-keyed tables cascade from companies, so a group's deletion
(billing.delete_og_and_dependents deletes its companies) takes them along.
The platform tables (voice_quota_requests, voice_platform_daily) hold no
tenant data and are written only by the daily job and the operator script.
None of them holds a diner identifier."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# pending: filed with Service Quotas, waiting; manual: above
# VOICE_QUOTA_AUTO_REQUEST_MAX, the operator was mailed a support-case text;
# approved / denied: the outcome; failed: the request call itself failed, AWS
# no longer knows it, or it stayed open past 7 days; superseded: a manual or
# id-less row whose value fell below the current target; stale: AWS kept it
# pending past VOICE_QUOTA_REQUEST_STALE_DAYS, no longer polled (decision 104).
QUOTA_REQUEST_STATUSES = ("pending", "manual", "approved", "denied", "failed", "superseded",
                          "stale")
OPEN_QUOTA_REQUEST_STATUSES = ("pending", "manual")
CAPACITY_REQUEST_STATUSES = ("pending", "approved", "declined")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class VoiceCompanySettings(Base):
    """A company's AI host phone settings. No row: the defaults (allocation
    VOICE_DEFAULT_COMPANY_ALLOCATION, no default staff number)."""

    __tablename__ = "voice_company_settings"
    __table_args__ = (
        CheckConstraint("voice_call_allocation >= 1",
                        name="ck_voice_company_settings_allocation"),
    )

    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    # Simultaneous calls the company may hold; changed only by an operator
    # approval or the nightly down-shift.
    voice_call_allocation: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Used by every location without its own staff_transfer_number.
    default_staff_transfer_number: Mapped[str | None] = mapped_column(
        String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class VoiceCapacityDaily(Base):
    """A company's busiest moment on a UTC day: the most voice conversations
    open at once, and how many started that day."""

    __tablename__ = "voice_capacity_daily"
    __table_args__ = (
        UniqueConstraint("company_id", "day", name="uq_voice_capacity_daily_company_day"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    peak_concurrent: Mapped[int] = mapped_column(Integer, nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class VoicePlatformDaily(Base):
    """The whole platform's busiest moment on a UTC day, from our own
    conversations and (when the instance exists) CloudWatch's
    ConcurrentCalls maximum."""

    __tablename__ = "voice_platform_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    peak_concurrent: Mapped[int] = mapped_column(Integer, nullable=False)
    cloudwatch_peak: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class VoiceQuotaRequest(Base):
    """One request to raise the Connect concurrent-calls quota."""

    __tablename__ = "voice_quota_requests"
    __table_args__ = (
        CheckConstraint(_in("status", QUOTA_REQUEST_STATUSES),
                        name="ck_voice_quota_requests_status"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    desired: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    aws_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class VoiceCapacityNotice(Base):
    """One capacity email sent (operator or admins): at most one per kind,
    subject and UTC day across every process, claimed with INSERT ... ON
    CONFLICT DO NOTHING before the send (decision 104). The subject is an
    internal id (a quota request, a company, "platform"), never a diner
    identifier or phone number. Platform data, kept 30 days."""

    __tablename__ = "voice_capacity_notices"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class VoiceCapacityRequest(Base):
    """A company admin's request for more simultaneous calls."""

    __tablename__ = "voice_capacity_requests"
    __table_args__ = (
        CheckConstraint(_in("status", CAPACITY_REQUEST_STATUSES),
                        name="ck_voice_capacity_requests_status"),
        CheckConstraint("expected_peak BETWEEN 1 AND 500",
                        name="ck_voice_capacity_requests_expected_peak"),
        # One pending request per company.
        Index("uq_voice_capacity_requests_company_pending", "company_id", unique=True,
              postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        index=True)
    requested_by: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Location ids the capacity should cover (no FK: archived locations stay).
    location_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    expected_peak: Mapped[int] = mapped_column(Integer, nullable=False)
    needed_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    granted_allocation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
