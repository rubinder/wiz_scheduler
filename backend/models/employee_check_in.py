"""An employee's arrival, matched to the shift they were scheduled for."""

from datetime import date, datetime

from sqlalchemy import (
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

# A scan matched to a shift at the scanned location.
CHECK_IN_MATCHED = "matched"
# Accepted, but this employee has no shift near this time anywhere.
CHECK_IN_NO_SHIFT = "no_shift"
# Accepted, but their shift today is at a different location.
CHECK_IN_WRONG_LOCATION = "wrong_location"
# Accepted, but they already checked in today. The first scan keeps the
# punctuality number.
CHECK_IN_DUPLICATE = "duplicate"


class EmployeeCheckIn(Base):
    __tablename__ = "employee_check_ins"
    __table_args__ = (
        # Single use, enforced by the database rather than by application
        # logic. Two employees scanning the same displayed code both present
        # the same counter; the first insert wins and the second collides.
        # Without this, both requests read COUNT(*) == N, both verify, and
        # both record — the check-in equivalent of the race assert_can_add
        # takes a row lock to avoid.
        UniqueConstraint(
            "location_id", "local_date", "counter",
            name="uq_employee_check_ins_location_date_counter",
        ),
        Index("ix_employee_check_ins_location_date", "company_id",
              "location_id", "local_date"),
        Index("ix_employee_check_ins_employee_date", "company_id",
              "employee_id", "local_date"),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False, index=True
    )
    # Null for no_shift and wrong_location. Also leaves room for a check-out
    # feature later without a second table.
    shift_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("shifts.id"), nullable=True
    )
    checked_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The location-local date. Stored rather than derived because "first scan
    # of the day" and the rotation counter are both wall-clock questions at
    # the location, and a consumer that forgets to convert is wrong only for
    # locations west of UTC late in the day — a bug that survives a test suite
    # running in UTC.
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    counter: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Signed: negative early, positive late. Denormalised because the report
    # reads over six months and the shift behind it can be edited,
    # regenerated, or purged by the retention sweeps in that time — any of
    # which would silently rewrite history if this were recomputed on read.
    minutes_from_start: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
