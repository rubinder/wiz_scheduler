from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    region_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("regions.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    geo_coord: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
