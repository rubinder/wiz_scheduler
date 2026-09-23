"""A payable shift: scheduled hours, gated by an arrival or a manager's word.

Derived from Shift x EmployeeCheckIn, never hand-authored. Paid hours are the
SCHEDULED hours (#78) — the check-in decides WHETHER a shift is payable, never
HOW MANY hours it pays.

checked_in_at and lateness_minutes are denormalised rather than read through
check_in_id because check-ins are swept at RETENTION_CHECKINS_DAYS and a pay
record must still be able to say what it was based on afterwards. role_name is
a snapshot for the same reason: a role renamed in March must not silently
rewrite January's payroll export.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

# The gating scan matched this shift.
TIME_ENTRY_CHECKED_IN = "checked_in"
# No scan; a manager confirmed on the employee's behalf. Phones die.
TIME_ENTRY_MANAGER_ATTESTED = "manager_attested"


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        # One entry per shift, enforced by the database. This is what makes
        # derivation idempotent and what stops an attestation racing a
        # derivation into two payable rows for the same shift.
        UniqueConstraint("shift_id", name="uq_time_entries_shift"),
        CheckConstraint(
            "source IN ('checked_in', 'manager_attested')",
            name="time_entries_source_check",
        ),
        CheckConstraint(
            "source <> 'manager_attested' OR "
            "(attested_by_user_id IS NOT NULL AND attested_at IS NOT NULL)",
            name="time_entries_attested_check",
        ),
        CheckConstraint(
            "paid_minutes > 0", name="time_entries_paid_minutes_check"
        ),
        Index("ix_time_entries_company_pay_date", "company_id", "pay_date"),
        Index("ix_time_entries_company_location_pay_date",
              "company_id", "location_id", "pay_date"),
        Index("ix_time_entries_company_employee_pay_date",
              "company_id", "employee_id", "pay_date"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    shift_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("shifts.id"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("roles.id"), nullable=False
    )
    # Snapshot of Shift.role_name, itself sourced from the roles table.
    role_name: Mapped[str] = mapped_column(String, nullable=False)
    # The location-local calendar date the shift STARTED. Whole and unsplit.
    pay_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    paid_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nulled by the check-in retention sweep; checked_in_at survives it.
    check_in_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("employee_check_ins.id"), nullable=True
    )
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Signed, negative early. NULL for attested entries: inventing a lateness
    # of zero would put a fact in the export that nobody observed.
    lateness_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attested_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
    attested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attestation_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
    # One-way idempotency marker, mirroring Shift.exported_at. Outlives the
    # payroll_exports row it points at, deliberately.
    exported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payroll_export_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("payroll_exports.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
