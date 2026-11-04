"""Paid payroll archives (#218) and the single-use links that retrieve them.

A cancelled group inside its grace period may pay once to keep its pay
records for seven years after the account is deleted. The archive row is the
durable record of that purchase and of the file written for it, so it is
deliberately NOT a foreign key to ownership_groups: the group, its users and
its billing charges are hard-deleted at day 90 and this row has to outlive
them. `delete_og_and_dependents` never touches either table.

Recipient emails (the group's admins at purchase time, lowercase) and company
names are copied onto the row for the same reason: nothing else that names
them survives the deletion.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    BigInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id


class PayrollArchive(Base):
    __tablename__ = "payroll_archives"
    __table_args__ = (
        CheckConstraint(
            "status IN ('paid', 'written')", name="payroll_archives_status_check"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    # Not a foreign key: see the module docstring. Unique: one archive per
    # group, ever. A second paid Checkout session is refunded, not stored.
    ownership_group_id: Mapped[str] = mapped_column(
        String(8), nullable=False, unique=True
    )
    company_names: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    stripe_checkout_session_id: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # JSONB so retrieval can match an address with a containment query.
    recipient_emails: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'paid'")
    )
    s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    written_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PayrollArchiveToken(Base):
    """A single-use download link mailed to an archive recipient.

    Only the sha256 of the token is stored: the table outlives the account
    and leaks nothing usable if read. `used_at` makes it single use (set by a
    conditional UPDATE, so two clicks cannot both redeem it), `expires_at`
    bounds it to PAYROLL_ARCHIVE_TOKEN_TTL_HOURS.
    """

    __tablename__ = "payroll_archive_tokens"

    id: Mapped[str] = mapped_column(
        String(8), primary_key=True, default=generate_short_id
    )
    archive_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("payroll_archives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
