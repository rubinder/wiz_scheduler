from datetime import date
from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class ShiftTemplate(Base):
    __tablename__ = "shift_templates"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    weekly_schedule: Mapped[dict] = mapped_column(JSON, nullable=False)
    specific_date: Mapped[date | None] = mapped_column(Date, nullable=True)
