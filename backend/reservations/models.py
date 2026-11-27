"""Reservations: floor plan, rules and bookings for one location.

ReservationTable.during holds [start_at, end_at + churn): the dining time plus
the reset time after it, so the exclusion constraint also blocks seating a
party before the table is turned over. Later tasks write it; this module only
defines the column and the constraint.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

HELD = "held"
PENDING = "pending_approval"
BOOKED = "booked"
SEATED = "seated"
COMPLETED = "completed"
NO_SHOW = "no_show"
CANCELLED = "cancelled"
EXPIRED = "expired"
DECLINED = "declined"

ALL_STATUSES = (
    HELD, PENDING, BOOKED, SEATED, COMPLETED, NO_SHOW, CANCELLED, EXPIRED,
    DECLINED,
)
# Statuses that occupy a table.
ACTIVE_STATUSES = frozenset({HELD, PENDING, BOOKED, SEATED})

SOURCE_STAFF = "staff"
SOURCE_WEB_AGENT = "web_agent"
SOURCE_VOICE_AGENT = "voice_agent"


def _id() -> Mapped[str]:
    return mapped_column(String(8), primary_key=True, default=generate_short_id)


def _company() -> Mapped[str]:
    return mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )


def _location(**kw: Any) -> Mapped[str]:
    return mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, **kw
    )


class FloorSection(Base):
    __tablename__ = "floor_sections"

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    location_id: Mapped[str] = _location(index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class DiningTable(Base):
    __tablename__ = "dining_tables"
    __table_args__ = (
        Index(
            "uq_dining_tables_location_label", "location_id", "label",
            unique=True, postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    location_id: Mapped[str] = _location(index=True)
    section_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("floor_sections.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    min_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    max_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    combinable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    x: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    y: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    w: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1")
    )
    h: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1")
    )
    rotation: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TableCombination(Base):
    __tablename__ = "table_combinations"

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    location_id: Mapped[str] = _location(index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    table_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    min_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    max_seats: Mapped[int] = mapped_column(Integer, nullable=False)


class ReservationRules(Base):
    __tablename__ = "reservation_rules"

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    location_id: Mapped[str] = _location(unique=True)
    rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # Bumped only when the flows or their editors change: the flow page's
    # stale-save check (decision 104), so a colleague's hours edit is not a
    # flow conflict.
    flows_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("party_size >= 1", name="ck_reservations_party_size"),
        CheckConstraint("end_at > start_at", name="ck_reservations_times"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in ALL_STATUSES) + ")",
            name="ck_reservations_status",
        ),
        Index(
            "uq_reservations_location_code", "location_id",
            "confirmation_code", unique=True,
            postgresql_where=text("confirmation_code IS NOT NULL"),
        ),
        Index("ix_reservations_company_location_start", "company_id",
              "location_id", "start_at"),
    )

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    location_id: Mapped[str] = _location()
    party_size: Mapped[int] = mapped_column(Integer, nullable=False)
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    held_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    diner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    diner_phone: Mapped[str | None] = mapped_column(String, nullable=True)  # E.164
    diner_email: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    confirmation_code: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(
        String, nullable=False, default="wizscheduler",
        server_default=text("'wizscheduler'"),
    )
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ReservationTable(Base):
    __tablename__ = "reservation_tables"
    __table_args__ = (
        ExcludeConstraint(
            ("table_id", "="), ("during", "&&"),
            where=text("active"), using="gist",
            name="ex_reservation_tables_no_overlap",
        ),
        Index("ix_reservation_tables_reservation_id", "reservation_id"),
    )

    id: Mapped[str] = _id()
    company_id: Mapped[str] = _company()
    reservation_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("dining_tables.id"), nullable=False
    )
    # [start_at, end_at + churn): see the module docstring.
    during: Mapped[Any] = mapped_column(TSTZRANGE, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
