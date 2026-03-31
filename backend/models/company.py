from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
