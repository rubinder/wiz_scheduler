"""AI host agent (#259): one conversation per diner contact, with an
append-only event log. Cost is recorded per event and rolled up on the
conversation."""

import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.id_gen import generate_short_id

CHANNELS = ("web", "voice")
EVENT_TYPES = ("user_turn", "assistant_turn", "tool_call", "tool_result",
               "error", "handoff", "action")
OUTCOMES = ("booked", "modified", "cancelled", "handed_off", "abandoned",
            "failed", "blocked")


def new_public_token() -> str:
    """43 url-safe characters (32 random bytes)."""
    return secrets.token_urlsafe(32)


def _reservation_fk() -> Mapped[str | None]:
    return mapped_column(
        String(8), ForeignKey("reservations.id", ondelete="SET NULL"),
        nullable=True,
    )


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    public_token: Mapped[str] = mapped_column(
        String(43), nullable=False, unique=True, default=new_public_token)
    caller_number: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", server_default=text("'open'"))
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    reservation_id: Mapped[str | None] = _reservation_fk()
    held_reservation_id: Mapped[str | None] = _reservation_fk()
    verified_reservation_id: Mapped[str | None] = _reservation_fk()
    turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    offered_starts: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"),
        server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now())


class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_agent_events_conv_seq"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    latency_ms: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    cache_write_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0"),
        server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
