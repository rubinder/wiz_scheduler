from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class EmployeeRoleMinutes(Base):
    __tablename__ = "employee_role_minutes"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "employee_id", "role_id", "month_start",
            name="uq_emp_role_month",
        ),
    )

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
    month_start: Mapped[date] = mapped_column(Date, nullable=False)  # first day of month
    total_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
