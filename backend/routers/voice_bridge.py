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
given, source key the HMAC of the number). Lex may retry a turn: every reply
is stored with its sequence number (`recording.remember_reply`), and a turn
whose seq is not past the last answered one gets that reply again without
running the agent. The Lambda's ASR and TTS timings are recorded as latency
stages on the turn's first event.

No per-address limit (only the Lambda calls this); turns are limited per
caller number (per contact for a withheld number) and per contact.
"""

import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

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
    if _limited(body):
        return voice.close(voice.UNAVAILABLE_TEXT, "rate_limited")
    found = await voice_numbers.location_for_number(db, body.dialed)
    if found is None:
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


async def _turn(db: AsyncSession, core: AgentCore, body: VoiceTurnIn,
                company_id: str, location_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    location = (await db.execute(
        select(Location).where(Location.id == location_id,
                               Location.company_id == company_id,
                               active(Location))
    )).scalar_one_or_none()
    if location is None or not await gate.subscribed(db, company_id, location_id, now):
        return voice.close(voice.UNAVAILABLE_TEXT, "unavailable")

    conv, created = await recording.voice_conversation(
        db, company_id, location_id, body.contact_id, body.caller,
        source_key_for(body.caller or "anonymous"))
    if not created and conv.last_seq is not None and body.seq <= conv.last_seq \
            and conv.last_reply is not None:
        await db.commit()
        return conv.last_reply  # a retried (or stale) turn: answered already
    if created:
        if await gate.cap_reached(db, company_id, location, now):
            await recording.finish(db, conv, "blocked")
            reply = await _busy(db, company_id, location_id)
            recording.remember_reply(conv, body.seq, reply)
            await db.commit()
            return reply
        if body.text is None and body.action is None:
            reply = voice.greeting(await gate.greeting(db, company_id, location))
            recording.remember_reply(conv, body.seq, reply)
            await db.commit()
            return reply
    await db.commit()  # the new conversation is visible to a racing turn
    if conv.status != "open":
        return voice.close(voice.ENDED_TEXT, "ended")

    claim = await recording.claim_turn_at_location(db, conv)
    await db.commit()  # so a concurrent request sees the claim; frees the lock
    if claim in ("busy", "location_busy"):
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
            stages = {name: ms for name, ms in (("asr", body.timings.asr_ms),
                                                 ("tts", body.timings.tts_ms))
                      if ms is not None}
            await recording.add_latency(db, conv, before, stages)
            reply = voice.spoken_reply(out)
    except BaseException:
        # handle_turn never raises an Exception: the call went away mid-turn.
        await gate.clear_mark_after_cancel(db, conv_id, company_id)
        raise
    recording.remember_reply(conv, body.seq, reply)
    await db.commit()
    return reply


async def _busy(db: AsyncSession, company_id: str, location_id: str) -> dict[str, Any]:
    logger.info("reservation_agent.spend_cap_reached location=%s channel=voice",
                location_id)
    rules, _ = await get_rules(db, company_id, location_id)
    return voice.close(voice.busy_text(rules.staff_transfer_number), "spend_cap")
