"""Conversation recording: start, append events, finish, and cost."""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Mapping

from sqlalchemy import func, literal, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
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
from backend.reservations.rules import get_rules

# last_seq of a conversation whose diner data was erased while a turn was
# in flight (diner_data.erase_diner_data closes it instead of deleting it).
# No turn number matches it (seq >= 0), so that turn's remember_reply writes
# nothing, and record_event keeps the turn's cost but not its words.
ERASED_SEQ = -1

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
    # The provider is fixed here: switching it in the rules later never
    # moves a conversation (or its bookings) to another provider.
    rules, _ = await get_rules(db, company_id, location_id)
    conv = AgentConversation(
        company_id=company_id, location_id=location_id, channel=channel,
        caller_number=caller_number, source_key=source_key, status="open",
        provider=rules.provider,
        started_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    return conv


async def voice_conversation(
    db: AsyncSession, company_id: str, location_id: str, contact_id: str,
    caller_number: str | None, source_key: str,
) -> tuple[AgentConversation, bool]:
    """The call's conversation, by (location, contact id), any status; on
    the call's first turn it is created (`voice`, the caller's number as
    given). Two first turns racing are settled by the unique index: the
    loser loads the winner's row. Returns (conversation, created). Does not
    commit."""
    conv = await find_voice_conversation(db, company_id, location_id, contact_id)
    if conv is not None:
        return conv, False
    try:
        async with db.begin_nested():
            conv = await start_conversation(db, company_id, location_id, "voice",
                                            caller_number=caller_number,
                                            source_key=source_key)
            conv.external_ref = contact_id
            await db.flush()
        return conv, True
    except IntegrityError:
        found = await find_voice_conversation(db, company_id, location_id, contact_id)
        if found is None:  # the winner's row is gone again (erased): give up
            raise
        return found, False


async def find_voice_conversation(db: AsyncSession, company_id: str,
                                  location_id: str,
                                  contact_id: str) -> AgentConversation | None:
    return (await db.execute(
        select(AgentConversation)
        .where(AgentConversation.company_id == company_id,
               AgentConversation.location_id == location_id,
               AgentConversation.external_ref == contact_id)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def remember_reply(db: AsyncSession, conv: AgentConversation, seq: int,
                         reply: dict[str, Any]) -> None:
    """Keep the voice bridge's answer to turn `seq` (claimed with that seq,
    or the call's opening turn), so a retry of it is answered again without
    a second run. Kept on a closing turn too: a retried "yes" that booked
    must hear its confirmation code again, not a goodbye. Only while the
    row still holds `seq`, so a later turn's number is never overwritten,
    and a no-op on a row erased mid-turn (deleted, or closed with
    last_seq = ERASED_SEQ). Does not commit."""
    await db.execute(
        update(AgentConversation)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id,
               AgentConversation.last_seq.is_(None)
               | (AgentConversation.last_seq == seq))
        .values(last_seq=seq, last_reply=reply)
        .execution_options(synchronize_session=False))


async def stored_reply(db: AsyncSession, conv: AgentConversation,
                       seq: int) -> dict[str, Any] | None:
    """The stored answer for turn `seq` or a later one, read fresh."""
    row = (await db.execute(
        select(AgentConversation.last_seq, AgentConversation.last_reply)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id)
    )).first()
    if row is None or row.last_seq is None or row.last_seq < seq:
        return None
    return row.last_reply


async def add_latency(db: AsyncSession, conv: AgentConversation, after_seq: int,
                      stages: Mapping[str, int]) -> None:
    """Merge channel-measured latency stages (voice: `asr`, `tts`) into the
    first event a turn recorded, the one after `after_seq`. Nothing when the
    turn recorded no event, or the conversation was erased mid-turn. Does
    not commit."""
    if not stages:
        return
    erased = (await db.execute(
        select(AgentConversation.last_seq)
        .where(AgentConversation.id == conv.id,
               AgentConversation.company_id == conv.company_id)
    )).scalar_one_or_none() == ERASED_SEQ
    if erased:
        return
    first = (await db.execute(
        select(func.min(AgentEvent.seq))
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == conv.company_id,
               AgentEvent.seq > after_seq)
    )).scalar_one_or_none()
    if first is None:
        return
    await db.execute(
        update(AgentEvent)
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == conv.company_id,
               AgentEvent.seq == first)
        .values(latency_ms=AgentEvent.latency_ms.op("||")(
            literal(dict(stages), type_=JSONB)))
        .execution_options(synchronize_session=False))


async def last_event_seq(db: AsyncSession, conv: AgentConversation) -> int:
    return int((await db.execute(
        select(func.coalesce(func.max(AgentEvent.seq), 0))
        .where(AgentEvent.conversation_id == conv.id,
               AgentEvent.company_id == conv.company_id)
    )).scalar_one())


async def record_event(
    db: AsyncSession, conv: AgentConversation, type: str,
    payload: dict[str, Any], *, latency_ms: dict[str, Any] | None = None,
    usage: Mapping[str, int] | None = None,
) -> AgentEvent:
    """Append an event. Locks the conversation row so concurrent writers get
    distinct, gapless seq numbers. On a conversation erased mid-turn
    (last_seq = ERASED_SEQ) the event is kept for its cost and usage but
    its payload is dropped, so the turn writes none of the diner's words
    back."""
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
    if locked.last_seq == ERASED_SEQ:
        payload = {"erased": True}
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
    if cost > 0:
        # Ruling S: billed as it is spent, not only at finish.
        await subscription.meter(db, locked, datetime.now(timezone.utc))
    return event


async def finish(db: AsyncSession, conv: AgentConversation, outcome: str) -> None:
    """Close the conversation and meter it (subscription.meter). Does not
    commit."""
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    conv.outcome = outcome
    conv.status = "closed"
    conv.ended_at = datetime.now(timezone.utc)
    await db.flush()
    await subscription.meter(db, conv, conv.ended_at)


# Longer than the worst live turn (MAX_TOOL_ROUNDS x the model timeout),
# and the mark is refreshed at every round commit besides.
TURN_STALE_SECONDS = 150
Claim = Literal["claimed", "closed", "busy", "seen", "moved"]


async def claim_turn(db: AsyncSession, conv: AgentConversation, *,
                     seq: int | None = None, seen_seq: int | None = None) -> Claim:
    """Atomically count one turn and mark it in flight. Does not commit:
    the caller commits so concurrent requests see the claim.

    One UPDATE checks and claims together, so two concurrent turns cannot
    both pass the turn cap or both run: the second waits on the row lock,
    re-reads it and finds the turn in flight (`busy`). The claim is allowed
    up to MAX_TURNS + 1: that last claim is the core's closing turn (its cap
    path runs on `turn_count > MAX_TURNS` and closes the conversation).
    A turn still marked in flight after TURN_STALE_SECONDS is taken to have
    died. `conv` is refreshed from the row either way.

    Voice (`seq` given): the claim also takes the channel's turn number.
    It is refused with `seen` when the row has already taken a number at or
    past `seq` (a retry: its reply is stored or still coming), and with
    `moved` when the row's number is no longer `seen_seq`, the one the
    caller read and judged the turn against (another turn ran meanwhile).
    It sets `last_seq = seq` and clears `last_reply` until
    `remember_reply` stores this turn's answer, so no retry of `seq` can run
    the core again, even after the in-flight mark clears."""
    where = [AgentConversation.id == conv.id,
             AgentConversation.company_id == conv.company_id,
             AgentConversation.status == "open",
             AgentConversation.turn_count <= settings.RESERVATION_AGENT_MAX_TURNS,
             (AgentConversation.turn_started_at.is_(None))
             | (AgentConversation.turn_started_at
                < func.now() - text(f"interval '{TURN_STALE_SECONDS} seconds'"))]
    values: dict[str, Any] = {"turn_count": AgentConversation.turn_count + 1,
                              "turn_started_at": func.now()}
    if seq is not None:
        where += [(AgentConversation.last_seq.is_(None))
                  | (AgentConversation.last_seq < seq),
                  AgentConversation.last_seq.is_not_distinct_from(seen_seq)]
        values.update(last_seq=seq, last_reply=None)
    claimed = (await db.execute(
        update(AgentConversation).where(*where).values(**values)
        .returning(AgentConversation.id)
        .execution_options(synchronize_session=False)
    )).scalar_one_or_none()
    await db.refresh(conv)
    if claimed is not None:
        return "claimed"
    if seq is not None and conv.last_seq is not None and conv.last_seq >= seq:
        return "seen"
    if conv.status == "open" and conv.turn_started_at is not None:
        return "busy"
    if seq is not None and conv.status == "open" and conv.last_seq != seen_seq:
        return "moved"
    return "closed"


LocationClaim = Literal["claimed", "closed", "busy", "seen", "moved", "location_busy"]


async def inflight_turns(db: AsyncSession, location_id: str,
                         exclude_conversation_id: str | None = None) -> int:
    """Turns running now at the location: open conversations whose in-flight
    mark is younger than TURN_STALE_SECONDS."""
    q = (select(func.count()).select_from(AgentConversation)
         .where(AgentConversation.location_id == location_id,
                AgentConversation.status == "open",
                AgentConversation.turn_started_at
                >= func.now() - text(f"interval '{TURN_STALE_SECONDS} seconds'")))
    if exclude_conversation_id is not None:
        q = q.where(AgentConversation.id != exclude_conversation_id)
    return int((await db.execute(q)).scalar_one())


async def claim_turn_at_location(
    db: AsyncSession, conv: AgentConversation, *, seq: int | None = None,
    seen_seq: int | None = None,
) -> LocationClaim:
    """`claim_turn`, refused with `location_busy` (nothing claimed) while
    RESERVATION_AGENT_LOCATION_MAX_INFLIGHT other turns run at the location.
    Count and claim run under a transaction-scoped advisory lock per
    location, so two requests cannot both take the last place; the caller's
    commit releases it. Does not commit."""
    await db.execute(select(func.pg_advisory_xact_lock(
        func.hashtextextended("reservation_agent_turns:" + conv.location_id, 0))))
    running = await inflight_turns(db, conv.location_id,
                                   exclude_conversation_id=conv.id)
    if running >= settings.RESERVATION_AGENT_LOCATION_MAX_INFLIGHT:
        return "location_busy"
    return await claim_turn(db, conv, seq=seq, seen_seq=seen_seq)


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
