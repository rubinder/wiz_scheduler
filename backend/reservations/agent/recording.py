"""Conversation recording: start, append events, finish, and cost."""

import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Mapping

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations import subscription
from backend.reservations.agent.models import (
    CHANNELS,
    EVENT_TYPES,
    OUTCOMES,
    AgentConversation,
    AgentEvent,
)
from backend.utils.id_gen import generate_short_id

logger = logging.getLogger(__name__)

_MILLION = Decimal(1_000_000)
_CENT_MILLIONTH = Decimal("0.000001")


def cost_of(usage: Mapping[str, int] | None) -> Decimal:
    """USD cost of one model call, rounded to 6 places (the column scale), from the per-million-token price settings."""
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
    return total.quantize(_CENT_MILLIONTH, rounding=ROUND_HALF_UP)


async def start_conversation(
    db: AsyncSession, company_id: str, location_id: str, channel: str,
    caller_number: str | None = None, source_key: str | None = None,
) -> AgentConversation:
    if channel not in CHANNELS:
        raise ValueError(f"invalid channel: {channel!r}")
    conv = AgentConversation(
        company_id=company_id, location_id=location_id, channel=channel,
        caller_number=caller_number, source_key=source_key, status="open",
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
    if type not in EVENT_TYPES:
        raise ValueError(f"invalid event type: {type!r}")
    # Lock the whole row and refresh it, so cost_usd below is the committed
    # value and not a stale in-memory one.
    locked = (await db.execute(
        select(AgentConversation)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
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
    locked.cost_usd = (locked.cost_usd or Decimal(0)) + cost
    await db.flush()
    return event


async def finish(db: AsyncSession, conv: AgentConversation, outcome: str) -> None:
    """Close the conversation and meter it (`_meter`). Does not commit."""
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    conv.outcome = outcome
    conv.status = "closed"
    conv.ended_at = datetime.now(timezone.utc)
    await db.flush()
    await _meter(db, conv)


async def _meter(db: AsyncSession, conv: AgentConversation) -> None:
    """Record the conversation's AgentUsage row: one per conversation
    (ON CONFLICT DO NOTHING on conversation_id), none for a `blocked`
    outcome or a billed amount of 0.00, none when the company has no
    ownership group. A DB write only, in a savepoint: metering never raises
    into the call path (it logs and the conversation stays finished)."""
    cost = conv.cost_usd or Decimal(0)
    if conv.outcome == "blocked" or cost <= 0:
        return
    billed = subscription.billed_amount(cost)
    if billed <= 0:
        return
    try:
        async with db.begin_nested():
            og_id = await subscription.ownership_group_of(db, conv.company_id)
            if og_id is None:
                logger.warning("agent usage not metered: company %s has no "
                               "ownership group (conversation %s)",
                               conv.company_id, conv.id)
                return
            sub = await subscription.for_group(db, og_id)
            ended_at = conv.ended_at or datetime.now(timezone.utc)
            await db.execute(
                pg_insert(subscription.AgentUsage)
                .values(id=generate_short_id(), company_id=conv.company_id,
                        ownership_group_id=og_id, location_id=conv.location_id,
                        conversation_id=conv.id, cost_usd=cost, billed_usd=billed,
                        period_start=subscription.usage_period_start(sub, ended_at),
                        product=subscription.PRODUCT)
                .on_conflict_do_nothing(index_elements=["conversation_id"]))
    except Exception:
        logger.exception("agent usage metering failed (conversation %s)", conv.id)


# Longer than the worst live turn (MAX_TOOL_ROUNDS x the model timeout),
# and the mark is refreshed at every round commit besides.
TURN_STALE_SECONDS = 150
Claim = Literal["claimed", "closed", "busy"]


async def claim_turn(db: AsyncSession, conv: AgentConversation) -> Claim:
    """Atomically count one turn and mark it in flight. Does not commit:
    the caller commits so concurrent requests see the claim.

    One UPDATE checks and claims together, so two concurrent turns cannot
    both pass the turn cap or both run: the second waits on the row lock,
    re-reads it and finds the turn in flight (`busy`). The claim is allowed
    up to MAX_TURNS + 1: that last claim is the core's closing turn (its cap
    path runs on `turn_count > MAX_TURNS` and closes the conversation).
    A turn still marked in flight after TURN_STALE_SECONDS is taken to have
    died. `conv` is refreshed from the row either way."""
    claimed = (await db.execute(
        update(AgentConversation)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id,
               AgentConversation.status == "open",
               AgentConversation.turn_count <= settings.RESERVATION_AGENT_MAX_TURNS,
               (AgentConversation.turn_started_at.is_(None))
               | (AgentConversation.turn_started_at
                  < func.now() - text(f"interval '{TURN_STALE_SECONDS} seconds'")))
        .values(turn_count=AgentConversation.turn_count + 1,
                turn_started_at=func.now())
        .returning(AgentConversation.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    await db.refresh(conv)
    if claimed is not None:
        return "claimed"
    if conv.status == "open" and conv.turn_started_at is not None:
        return "busy"
    return "closed"


def end_turn(conv: AgentConversation) -> None:
    """Clear the in-flight mark; flushed with the turn's last commit."""
    conv.turn_started_at = None


def touch_turn(conv: AgentConversation) -> None:
    """Refresh the in-flight mark; flushed with the round's commit."""
    conv.turn_started_at = datetime.now(timezone.utc)


async def clear_turn_mark(db: AsyncSession, conv_id: str, company_id: str) -> None:
    """Clear the in-flight mark by id and commit. For a turn that was
    cancelled mid-way, on a session of the caller's choosing."""
    await db.execute(
        update(AgentConversation)
        .where(AgentConversation.id == conv_id,
               AgentConversation.company_id == company_id)
        .values(turn_started_at=None)
        .execution_options(synchronize_session=False))
    await db.commit()
