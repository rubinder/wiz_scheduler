from datetime import datetime

from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class OwnershipGroup(Base):
    __tablename__ = "ownership_groups"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ai_credits_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    autoreload_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    autoreload_threshold_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("2.0")
    )
    autoreload_amount_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("10.0")
    )
    autoreload_failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    default_payment_method_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_subscription_ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_deletion_reminder_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notified_data_deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Which external integration feeds data into this group (e.g. "7shifts").
    # NULL means the group manages data manually without an integration.
    api_integration: Mapped[str | None] = mapped_column(String, nullable=True)
    # ------------------------------------------------------------------
    # Signup signals. Recorded at /auth/register, OBSERVE-ONLY: nothing
    # reads these to allow or deny anything, and no code path should start
    # doing so without first looking at what the distribution actually is.
    # They exist so the question "is anyone actually farming free tiers?"
    # has an answer other than a guess.
    #
    # Nulled out by the retention sweep after RETENTION_SIGNUP_SIGNALS_DAYS.
    # The ownership group itself is untouched — only the signals age out.
    # ------------------------------------------------------------------

    # Masked to a /16 by utils.privacy.mask_ip, so this corroborates a
    # cluster rather than identifying one on its own. Two signups sharing a
    # /16 means little; two sharing a /16 AND a device id means a lot.
    signup_ip_masked: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # utils.email_normalize form of the registering address. Duplicated from
    # users.email_normalized on purpose: the point is to compare across
    # ownership groups, and the user row can be renamed or deleted.
    signup_email_normalized: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    # Opaque id the frontend keeps in localStorage. The strongest of the
    # three for the case we care about — same person, same browser, fresh
    # email — and the easiest to defeat (incognito, cleared storage). Fine:
    # a signal that catches the lazy majority is worth more than none.
    signup_device_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    signup_user_agent_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
