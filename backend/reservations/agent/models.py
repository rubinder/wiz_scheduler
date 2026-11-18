"""AI host agent (#259): one conversation per diner contact, with an
append-only event log. Cost is recorded per event and rolled up on the
conversation."""

import secrets
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        CheckConstraint(_in("channel", CHANNELS), name="ck_agent_conversations_channel"),
        CheckConstraint(_in("status", ("open", "closed")),
                        name="ck_agent_conversations_status"),
        CheckConstraint("outcome IS NULL OR " + _in("outcome", OUTCOMES),
                        name="ck_agent_conversations_outcome"),
        # One conversation per external contact (the Amazon Connect contact
        # id for a call) at a location.
        Index("uq_agent_conversations_location_external_ref", "location_id",
              "external_ref", unique=True,
              postgresql_where=text("external_ref IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    public_token: Mapped[str] = mapped_column(
        String(43), nullable=False, unique=True, default=new_public_token)
    caller_number: Mapped[str | None] = mapped_column(String, nullable=True)
    # The channel's own id for the contact (voice: the Connect contact id).
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The rate-limit viewer key the conversation was started from
    # (rate_limit.trusted_client_keys); keys the lookup lockout.
    source_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="open", server_default=text("'open'"))
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    reservation_id: Mapped[str | None] = _reservation_fk()
    held_reservation_id: Mapped[str | None] = _reservation_fk()
    verified_reservation_id: Mapped[str | None] = _reservation_fk()
    # Set only by the cancel_booking tool (to the verified id); the
    # confirm_cancel action requires it and clears it.
    pending_cancel_id: Mapped[str | None] = _reservation_fk()
    turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    # Set when a turn is claimed (recording.claim_turn), refreshed at each
    # round commit, cleared when it ends, so a concurrent turn on the same
    # conversation is refused. A mark older than recording.TURN_STALE_SECONDS
    # is a turn that died without clearing it.
    turn_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Wrong code/phone lookups; tools.py refuses lookups past the cap.
    failed_lookups: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    # [{"start_iso": ..., "party_size": ...}] offered by find_slots (tools.py).
    offered_starts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    # {"start_iso": str | None, "party_size": int | None} staged by the
    # modify_booking tool; the confirm_modify action applies and clears it.
    pending_modify: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Voice bridge dedupe: the Lambda's sequence number of the last claimed
    # turn (recording.claim_turn) and the reply it got (remember_reply; NULL
    # while that turn runs). A retry of that turn (or an older one) is
    # answered from here without a second run, and the next action is
    # checked against its `expect`.
    # last_reply is a stored schemas.voice.VoiceTurnOut and is served back
    # through that model, possibly after a deploy changed it: a field added
    # to VoiceTurnOut needs a default, and a removed or renamed one needs the
    # old rows (at most one per open call) to still validate.
    last_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_reply: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
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
        CheckConstraint(_in("type", EVENT_TYPES), name="ck_agent_events_type"),
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


class ReservationLookupFailure(Base):
    """One wrong booking lookup (code + phone) by the agent, kept a day.
    Counted per (location, phone) across conversations, so opening new
    conversations cannot reset the lookup lockout (tools._lookup_booking)."""

    __tablename__ = "reservation_lookup_failures"
    __table_args__ = (
        Index("ix_reservation_lookup_failures_loc_phone_created",
              "location_id", "phone", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_short_id)
    company_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("companies.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("locations.id"), nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
