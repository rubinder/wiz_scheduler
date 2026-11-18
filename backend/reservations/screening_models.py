"""Tables for call screening (#259 step 7). The rules that read them live in
backend/reservations/screening.py; kept apart so backend.models can register
the tables without an import cycle (as voice_number_models).

number_rules and screened_calls are company data (RLS keyed on company).
spam_numbers and spam_ingest_files are global: the FTC's public complaint
list, the same for every tenant. The app role only reads them; the weekly
ingest writes them from its own session, which runs as the table owner
(migration 0063)."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

RULE_ACTIONS = ("allow", "block")
DECISIONS = ("answer", "block")
REASONS = ("allow_list", "block_list", "ftc", "default", "withheld")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class NumberRule(Base):
    """A manager's allow or block entry for a caller number, at one location
    or (location_id NULL) every location of the company. Active while
    expires_at is NULL or in the future. The number is diner data."""

    __tablename__ = "number_rules"
    __table_args__ = (
        CheckConstraint(_in("action", RULE_ACTIONS), name="ck_number_rules_action"),
        Index("ix_number_rules_company_id_e164", "company_id", "e164"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False)
    location_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=True)
    e164: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(5), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class SpamNumber(Base):
    """FTC Do Not Call complaints about a number on one report date."""

    __tablename__ = "spam_numbers"
    __table_args__ = (Index("ix_spam_numbers_e164", "e164"),)

    e164: Mapped[str] = mapped_column(String(20), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    reports: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SpamIngestFile(Base):
    """One FTC source file the ingest has handled: idempotency per date."""

    __tablename__ = "spam_ingest_files"

    file_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class ScreenedCall(Base):
    """One screening decision, for analytics. The caller is kept only as the
    keyed HMAC used for voice conversations (NULL when withheld). Purged
    with agent conversations (RETENTION_RESERVATIONS_DAYS)."""

    __tablename__ = "screened_calls"
    __table_args__ = (
        CheckConstraint(_in("decision", DECISIONS), name="ck_screened_calls_decision"),
        CheckConstraint(_in("reason", REASONS), name="ck_screened_calls_reason"),
        Index("ix_screened_calls_location_id_created_at", "location_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False)
    caller_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(6), nullable=False)
    reason: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
