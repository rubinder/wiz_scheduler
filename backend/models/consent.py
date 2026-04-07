from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class UserConsent(Base):
    __tablename__ = "user_consents"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    user_id: Mapped[str] = mapped_column(String(8), ForeignKey("users.id"), nullable=False)
    company_id: Mapped[str] = mapped_column(String(8), ForeignKey("companies.id"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String, nullable=False)  # "privacy_policy", "terms_of_service", "data_processing"
    version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
