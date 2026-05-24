from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class GdprExportLog(Base):
    """Per-export audit row for /gdpr/export calls.

    Source of truth for the per-user export cooldown (#45) AND a
    permanent GDPR audit trail (who exported their data, when) which
    /gdpr/export needs to keep regardless of the cooldown feature.
    """
    __tablename__ = "gdpr_export_log"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    user_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("users.id"), nullable=False
    )
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
