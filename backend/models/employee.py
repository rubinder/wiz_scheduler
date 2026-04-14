from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, Numeric, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=True
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    location_ids: Mapped[list[str] | None] = mapped_column(
        JSON, nullable=True
    )

    external_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Weekly hour cap. NULL means no cap.
    max_hours_per_week: Mapped[float | None] = mapped_column(Float, nullable=True)

    roles: Mapped[list["EmployeeRole"]] = relationship(
        back_populates="employee", lazy="selectin"
    )


class EmployeeRole(Base):
    __tablename__ = "employee_roles"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    role_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("roles.id"), nullable=False
    )
    skill_level: Mapped[int] = mapped_column(Integer, nullable=False)

    employee: Mapped["Employee"] = relationship(back_populates="roles")


class EmployeeAffinity(Base):
    __tablename__ = "employee_affinities"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    target_employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    level: Mapped[float] = mapped_column(Numeric, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class EmployeeCompany(Base):
    __tablename__ = "employee_companies"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )

    __table_args__ = (
        sa.UniqueConstraint("employee_id", "company_id", name="uq_employee_company"),
    )


class EmployeeInvite(Base):
    __tablename__ = "employee_invites"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmployeeDayBlackout(Base):
    """Recurring per-day-of-week time range during which an employee must not
    be scheduled. Example: employee X must not work 20:00-22:00 on Mondays.

    day_of_week follows Python's datetime.weekday() convention (0 = Monday).
    """

    __tablename__ = "employee_day_blackouts"
    __table_args__ = (
        CheckConstraint(
            "day_of_week BETWEEN 0 AND 6",
            name="ck_employee_day_blackouts_dow",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)    # "HH:MM"


class EmployeeAvailability(Base):
    __tablename__ = "employee_availability"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("employees.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
