from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class IntegrationImport(Base):
    """Per-import audit row for 7shifts / Deputy / similar bulk integrations.

    Source of truth for the per-OG import cooldown (#44). The import
    endpoints pull the whole account at once and don't take a location
    parameter today, so the cooldown is effectively per-(OG, integration).
    location_id stays nullable so a future per-location import path can
    populate it without a schema change.
    """
    __tablename__ = "integration_imports"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False
    )
    integration: Mapped[str] = mapped_column(String(64), nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
