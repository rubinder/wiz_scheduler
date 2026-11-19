"""Tables for the mock external booking provider (#259 step 9). The provider
itself is backend/reservations/providers/mock_external.py; the tables live
here so backend.models can register them without an import cycle (as
screening_models).

This is "someone else's" reservation system, simulated: it keeps its own
inventory and never touches `reservations`, tables or their exclusion
constraint. Both tables are company data (RLS keyed on company, migration
0065)."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

MOCK_STATUSES = ("held", "booked", "cancelled")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class MockExternalBooking(Base):
    """One booking in the mock external system. A hold is a `held` row
    until `held_until`; after that it no longer counts toward capacity and
    reads back as expired. `code` is set when the hold is booked, unique per
    location (the same alphabet as WizScheduler codes)."""

    __tablename__ = "mock_external_bookings"
    __table_args__ = (
        CheckConstraint(_in("status", MOCK_STATUSES), name="ck_mock_external_bookings_status"),
        CheckConstraint("party_size >= 1", name="ck_mock_external_bookings_party_size"),
        Index("ix_mock_external_bookings_company_id", "company_id"),
        Index("ix_mock_external_bookings_location_id_start_at", "location_id", "start_at"),
        Index("uq_mock_external_bookings_location_code", "location_id", "code",
              unique=True, postgresql_where=text("code IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    diner_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    diner_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    held_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class MockExternalIdempotency(Base):
    """The first response to an idempotent book/modify/cancel, keyed by
    `{conversation_id}:{action}:{seq}`. A replay returns `response` (a
    ProviderBooking as JSON) unchanged. Kept for 24 hours (data_retention)."""

    __tablename__ = "mock_external_idempotency"
    __table_args__ = (
        Index("ix_mock_external_idempotency_company_id", "company_id"),
        Index("ix_mock_external_idempotency_created_at", "created_at"),
    )

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
