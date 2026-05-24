from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class OgEmailSendLog(Base):
    """Per-send audit row for every Resend email tied to an ownership group.

    Source of truth for the per-OG daily email cap (#42). Writing here on
    every successful send (regardless of which sender helper) is simpler
    than reconciling counts across employee_invites, manager_invites,
    password_reset_tokens, and the transactional sends that previously
    had no persistent record.

    Sends made before an OG exists (registration welcome email) skip the
    log — there's no key to attribute them to.
    """
    __tablename__ = "og_email_send_log"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("ownership_groups.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
