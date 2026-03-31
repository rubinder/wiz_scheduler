from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    geo_bounds: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
