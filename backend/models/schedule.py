from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ShiftSchedule(Base):
    __tablename__ = "shift_schedules"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False
    )
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    strategy_param: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_param2: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    shift_schedule_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("shift_schedules.id"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("roles.id"), nullable=False
    )
    role_name: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
