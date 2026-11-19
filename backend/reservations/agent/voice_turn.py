"""One voice bridge turn (#259 step 6), as a service: the body of
POST /voice/turn (routers/voice_bridge.py, which documents the behaviour),
callable in process so the eval suite drives the same seq dedupe, claim,
action matching and transfer screening as production.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import bind_rls_context
from backend.models import Location
from backend.reservations import voice_numbers, voice_transfers
from backend.reservations.agent import gate, recording, voice
from backend.reservations.agent.core import AgentCore, TurnInput
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_rules
from backend.schemas.voice import VoiceTurnIn
from backend.services.archive import active
from backend.services.rate_limit import (
    SlidingWindowLimiter,
    source_key_for,
    voice_caller_limiter,
)
from backend.tenant import tenant_context

logger = logging.getLogger(__name__)

# A retry of a turn still running waits this long for its stored reply.
REPLY_POLL_SECONDS = 0.25
REPLY_WAIT_SECONDS = 3.0
_sleep = asyncio.sleep  # replaced in tests


def _limited(body: VoiceTurnIn, limiter: SlidingWindowLimiter) -> bool:
    keys = [f"contact:{body.contact_id}",
            f"caller:{body.caller}" if body.caller else None]
    for key in filter(None, keys):
        if not limiter.check_and_record(key):
            # Never log a raw number: the keyed hash only.
            logger.info("voice_bridge.rate_limited key=%s", source_key_for(key))
            return True
    return False


async def handle(db: AsyncSession, core: AgentCore, body: VoiceTurnIn, *,
                 now: datetime | None = None,
                 limiter: SlidingWindowLimiter | None = None) -> dict[str, Any]:
    """One phone turn, the whole of POST /voice/turn after its secret and
    flag checks. `now` (default: the wall clock) is the gate's clock and,
    when given, the core's; `limiter` defaults to the process-wide
    `voice_caller_limiter`. The eval suite passes both."""
    if limiter is None:
        limiter = voice_caller_limiter
    found = await voice_numbers.location_for_number(db, body.dialed)
    if found is None:
        if _limited(body, limiter):
            return voice.close(voice.RATE_LIMITED_TEXT, "rate_limited")
        return voice.close(voice.UNAVAILABLE_TEXT, "unknown_number")
    company_id, location_id = found
    token = tenant_context.set([company_id])
    try:
        await bind_rls_context(db, [company_id])
        return await _turn(db, core, body, company_id, location_id, now, limiter)
    finally:
        try:
            tenant_context.reset(token)
        except ValueError:  # teardown ran in another context
            tenant_context.set(None)


async def _replay(db: AsyncSession, conv: AgentConversation,
                  seq: int) -> dict[str, Any]:
    """A turn whose seq the call has already taken. Its stored reply, or,
    while that turn still runs, the reply once it is stored (polled with
    fresh reads); past the wait, or when the turn died, the caller repeats
    and the next seq runs."""
    waited = 0.0
    while True:
        reply = await recording.stored_reply(db, conv, seq)
        await db.commit()  # no transaction held across the wait
        if reply is not None:
            return reply
        if waited >= REPLY_WAIT_SECONDS:
            return voice.inflight()
        await _sleep(REPLY_POLL_SECONDS)
        waited += REPLY_POLL_SECONDS


async def _turn(db: AsyncSession, core: AgentCore, body: VoiceTurnIn,
                company_id: str, location_id: str, at: datetime | None,
                limiter: SlidingWindowLimiter) -> dict[str, Any]:
    now = at or datetime.now(timezone.utc)
    # Replays first: a retried turn is answered before any limit.
    conv = await recording.find_voice_conversation(db, company_id, location_id,
                                                   body.contact_id)
    if conv is not None and conv.last_seq is not None and body.seq <= conv.last_seq:
        return await _replay(db, conv, body.seq)
    if _limited(body, limiter):
        await db.commit()
        return voice.close(voice.RATE_LIMITED_TEXT, "rate_limited")

    location = (await db.execute(
        select(Location).where(Location.id == location_id,
                               Location.company_id == company_id,
                               active(Location))
    )).scalar_one_or_none()
    if location is None or not await gate.subscribed(db, company_id, location_id, now):
        await db.commit()
        return voice.close(voice.UNAVAILABLE_TEXT, "unavailable")

    created = False
    if conv is None:
        conv, created = await recording.voice_conversation(
            db, company_id, location_id, body.contact_id, body.caller,
            source_key_for(body.caller or "anonymous"))
    if created:
        if await gate.cap_reached(db, company_id, location, now):
            await recording.finish(db, conv, "blocked")
            reply = await _busy(db, company_id, location_id)
            await recording.remember_reply(db, conv, body.seq, reply)
            await db.commit()
            return reply
        if body.text is None and body.action is None:
            reply = voice.greeting(await gate.greeting(db, company_id, location))
            await recording.remember_reply(db, conv, body.seq, reply)
            await db.commit()
            return reply
    await db.commit()  # the new conversation is visible to a racing turn
    if conv.status != "open":
        return voice.close(voice.ENDED_TEXT, "ended")
    seen_seq, last_reply = conv.last_seq, conv.last_reply
    if body.action is not None and not voice.action_matches(body.action, last_reply):
        # A confirmation only answers the question the caller was asked.
        return voice.not_caught(last_reply)

    claim = await recording.claim_turn_at_location(db, conv, seq=body.seq,
                                                   seen_seq=seen_seq)
    await db.commit()  # so a concurrent request sees the claim; frees the lock
    if claim == "seen":
        return await _replay(db, conv, body.seq)
    if claim in ("busy", "location_busy", "moved"):
        logger.info("voice_bridge.%s location=%s", claim, location_id)
        return voice.inflight()
    if claim == "closed":
        return voice.close(voice.ENDED_TEXT, "ended")

    conv_id = conv.id
    try:
        if await gate.cap_reached(db, company_id, location, now,
                                  exclude_conversation_id=conv_id):
            reply = await _busy(db, company_id, location_id)
            await core.close_conversation(db, conv, now=now)
        else:
            before = await recording.last_event_seq(db, conv)
            out = await core.handle_turn(
                db, conv, TurnInput(text=body.text, action=body.action), now=at)
            timings = body.timings
            stages = {name: ms for name, ms in (("asr", timings.asr_ms),
                                                 ("tts", timings.tts_ms),
                                                 ("transport", timings.transport_ms))
                      if ms is not None}
            await recording.add_latency(db, conv, before, stages)
            rules, _ = await get_rules(db, company_id, location_id)
            reply = await _screen_transfer(
                db, location,
                voice.spoken_reply(out, rules.voice.voice_confirm_style), now)
    except BaseException:
        # handle_turn never raises an Exception: the call went away mid-turn.
        await gate.clear_mark_after_cancel(db, conv_id, company_id)
        raise
    await recording.remember_reply(db, conv, body.seq, reply)
    await db.commit()
    return reply


async def _screen_transfer(db: AsyncSession, location: Location,
                           reply: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A transfer is an outbound call billed per minute (toll fraud): one to
    a number voice_transfers.number_problem refuses, or past
    VOICE_TRANSFERS_PER_DAY at the location today (an atomic reservation,
    committed with the turn's reply), ends the call with the no-transfer
    line instead."""
    number = reply.get("transfer_to")
    if not number:
        return reply
    problem = await voice_transfers.number_problem(db, number)
    if problem:
        logger.warning("voice_bridge.transfer_refused location=%s: %s", location.id, problem)
        return voice.close(voice.NO_TRANSFER_TEXT, "transfer_refused")
    local_date = now.astimezone(ZoneInfo(location.timezone)).date()
    if not await voice_transfers.reserve(db, location.company_id, location.id, local_date,
                                         settings.VOICE_TRANSFERS_PER_DAY):
        logger.warning("voice_bridge.transfer_cap location=%s", location.id)
        return voice.close(voice.NO_TRANSFER_TEXT, "transfer_cap")
    return reply


async def _busy(db: AsyncSession, company_id: str, location_id: str) -> dict[str, Any]:
    logger.info("reservation_agent.spend_cap_reached location=%s channel=voice",
                location_id)
    rules, _ = await get_rules(db, company_id, location_id)
    return voice.close(voice.busy_text(rules.staff_transfer_number), "spend_cap")
