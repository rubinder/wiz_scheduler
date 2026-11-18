"""Conversation recording: start, append events, finish, and cost."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations.agent.models import AgentConversation, AgentEvent

_MILLION = Decimal(1_000_000)


def cost_of(usage: Mapping[str, int] | None) -> Decimal:
    """USD cost of one model call from the per-million-token price settings."""
    u = usage or {}
    prices = (
        ("input_tokens", settings.RESERVATION_AGENT_INPUT_USD_PER_MTOK),
        ("output_tokens", settings.RESERVATION_AGENT_OUTPUT_USD_PER_MTOK),
        ("cache_read_tokens", settings.RESERVATION_AGENT_CACHE_READ_USD_PER_MTOK),
        ("cache_write_tokens", settings.RESERVATION_AGENT_CACHE_WRITE_USD_PER_MTOK),
    )
    total = Decimal(0)
    for key, price in prices:
        total += Decimal(int(u.get(key, 0) or 0)) * Decimal(str(price)) / _MILLION
    return total


async def start_conversation(
    db: AsyncSession, company_id: str, location_id: str, channel: str,
    caller_number: str | None = None,
) -> AgentConversation:
    conv = AgentConversation(
        company_id=company_id, location_id=location_id, channel=channel,
        caller_number=caller_number, status="open",
        started_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    return conv


async def record_event(
    db: AsyncSession, conv: AgentConversation, type: str,
    payload: dict[str, Any], *, latency_ms: dict[str, Any] | None = None,
    usage: Mapping[str, int] | None = None,
) -> AgentEvent:
    """Append an event. Locks the conversation row so concurrent writers get
    distinct, gapless seq numbers."""
    await db.execute(
        select(AgentConversation.id)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id)
        .with_for_update()
    )
    last = (await db.execute(
        select(AgentEvent.seq)
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == conv.company_id)
        .order_by(AgentEvent.seq.desc()).limit(1)
    )).scalar_one_or_none()
    u = usage or {}
    cost = cost_of(u)
    event = AgentEvent(
        company_id=conv.company_id, conversation_id=conv.id,
        seq=(last or 0) + 1, type=type, payload=payload,
        latency_ms=latency_ms or {},
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_read_tokens=int(u.get("cache_read_tokens", 0) or 0),
        cache_write_tokens=int(u.get("cache_write_tokens", 0) or 0),
        cost_usd=cost,
    )
    db.add(event)
    conv.cost_usd = (conv.cost_usd or Decimal(0)) + cost
    await db.flush()
    return event


async def finish(db: AsyncSession, conv: AgentConversation, outcome: str) -> None:
    conv.outcome = outcome
    conv.status = "closed"
    conv.ended_at = datetime.now(timezone.utc)
    await db.flush()
