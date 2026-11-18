"""Voice bridge (#259 step 6): the endpoint the Amazon Connect Lambda calls
for each phone turn.

Never public: without the exact X-Voice-Bridge-Secret (and always while
VOICE_BRIDGE_SECRET is empty) or with reservations off, it 404s, before the
body is validated. The Lambda has no database; everything about the call is
here.

A turn: the dialled number names the location (`voice_numbers.
location_for_number`, read across tenants), the request is then bound to its
company (ORM tenant filter and RLS) and runs the web chat's guarantees
(`reservations.agent.gate`): an archived location, a number in its release
hold or a group without an answering subscription gets a polite close with
no model call; the spend cap closes the call; the turn is claimed atomically
(`recording.claim_turn_at_location`), and a turn already running for the
call or a full location answers "say that again" with the call kept up.

One conversation per call: the first turn for a Connect contact id creates
it (`recording.voice_conversation`, channel `voice`, the caller's number as
given, source key the HMAC of the number).

Lex may retry a turn. The atomic claim also takes the turn's seq
(`last_seq`, compare-and-set against the seq this request read) and clears
`last_reply` until `remember_reply` stores the answer, so a seq runs the
core at most once. A turn whose seq is not past `last_seq` is a replay: its
stored reply, or, while that turn still runs, the reply once stored
(polled for up to REPLY_WAIT_SECONDS), else "say that again". Replays are
answered before the rate limit.

A confirmation action is checked against the question last asked (the
stored reply's `expect`); one that answers no open question is not run.
The Lambda's ASR, TTS and transport timings are recorded as latency stages
on the turn's first event.

A handoff to staff is an outbound call billed per minute: it is screened
(`_screen_transfer`: voice_transfers.number_problem, and at most
VOICE_TRANSFERS_PER_DAY per location per local day, reserved atomically) and otherwise ends the
call with the no-transfer line.

No per-address limit (only the Lambda calls this); turns are limited per
caller number (per contact for a withheld number) and per contact.
"""

import asyncio
import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import bind_rls_context, get_db
from backend.models import Location
from backend.reservations import voice_numbers
from backend.reservations.agent import gate, recording, voice
from backend.reservations.agent.core import AgentCore, TurnInput
from backend.reservations.agent.gate import get_agent_core
from backend.reservations.agent.models import AgentConversation
from backend.reservations import voice_transfers
from backend.reservations.rules import get_rules
from backend.schemas.voice import VoiceTurnIn, VoiceTurnOut
from backend.services.archive import active
from backend.services.rate_limit import source_key_for, voice_caller_limiter
from backend.tenant import tenant_context

logger = logging.getLogger(__name__)

# Compared against when the header is missing or the secret unset, so every
# refusal takes the same constant-time path.
_DUMMY_SECRET = secrets.token_bytes(32)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def require_bridge_secret(
    x_voice_bridge_secret: str | None = Header(default=None),
) -> None:
    secret = settings.VOICE_BRIDGE_SECRET.encode()
    given = (x_voice_bridge_secret or "").encode()
    ok = hmac.compare_digest(given, secret or _DUMMY_SECRET)
    if not (secret and x_voice_bridge_secret is not None and ok):
        raise _not_found()


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise _not_found()


router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(require_bridge_secret), Depends(require_reservations_enabled)],
)


# A retry of a turn still running waits this long for its stored reply.
REPLY_POLL_SECONDS = 0.25
REPLY_WAIT_SECONDS = 3.0
_sleep = asyncio.sleep  # replaced in tests


def _limited(body: VoiceTurnIn) -> bool:
    keys = [f"contact:{body.contact_id}",
            f"caller:{body.caller}" if body.caller else None]
    for key in filter(None, keys):
        if not voice_caller_limiter.check_and_record(key):
            # Never log a raw number: the keyed hash only.
            logger.info("voice_bridge.rate_limited key=%s", source_key_for(key))
            return True
    return False


@router.post("/turn", response_model=VoiceTurnOut)
async def voice_turn(
    body: VoiceTurnIn,
    db: AsyncSession = Depends(get_db),
    core: AgentCore = Depends(get_agent_core),
) -> dict[str, Any]:
    found = await voice_numbers.location_for_number(db, body.dialed)
    if found is None:
        if _limited(body):
            return voice.close(voice.RATE_LIMITED_TEXT, "rate_limited")
        return voice.close(voice.UNAVAILABLE_TEXT, "unknown_number")
    company_id, location_id = found
    token = tenant_context.set([company_id])
    try:
        await bind_rls_context(db, [company_id])
        return await _turn(db, core, body, company_id, location_id)
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
                company_id: str, location_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    # Replays first: a retried turn is answered before any limit.
    conv = await recording.find_voice_conversation(db, company_id, location_id,
                                                   body.contact_id)
    if conv is not None and conv.last_seq is not None and body.seq <= conv.last_seq:
        return await _replay(db, conv, body.seq)
    if _limited(body):
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
                db, conv, TurnInput(text=body.text, action=body.action))
            timings = body.timings
            stages = {name: ms for name, ms in (("asr", timings.asr_ms),
                                                 ("tts", timings.tts_ms),
                                                 ("transport", timings.transport_ms))
                      if ms is not None}
            await recording.add_latency(db, conv, before, stages)
            reply = await _screen_transfer(db, location, voice.spoken_reply(out), now)
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
