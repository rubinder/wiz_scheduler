"""Voice bridge (#259 step 6): the endpoint the Amazon Connect Lambda calls
for each phone turn.

Never public: without the exact X-Voice-Bridge-Secret (and always while
VOICE_BRIDGE_SECRET is empty) or with reservations off, it 404s, before the
body is validated. The Lambda has no database; everything about the call is
here.

A turn (`reservations.agent.voice_turn.handle`, which the eval suite also
calls in process): the dialled number names the location (`voice_numbers.
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

Screening (#259 step 7): before answering, the Lambda asks `/voice/screen`
whether to take the call (`reservations.screening.decide`). It has the same
secret, flag and number mapping; it makes no model call, creates no
conversation and is not rate limited by caller. Each decision is recorded
as a ScreenedCall; an unknown dialled number is answered (the turn then
closes politely) and records nothing, having no company.
"""

import hmac
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import bind_rls_context, get_db
from backend.reservations import screening, voice_numbers
from backend.reservations.agent import voice_turn as turn_service
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.gate import get_agent_core
from backend.schemas.voice import VoiceScreenIn, VoiceScreenOut, VoiceTurnIn, VoiceTurnOut
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


@router.post("/screen", response_model=VoiceScreenOut)
async def voice_screen(
    body: VoiceScreenIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    found = await voice_numbers.location_for_number(db, body.dialed)
    if found is None:
        return {"action": screening.ANSWER, "reason": "default"}
    company_id, location_id = found
    token = tenant_context.set([company_id])
    try:
        await bind_rls_context(db, [company_id])
        decision, reason = await screening.screen(
            db, company_id, location_id, body.caller, datetime.now(timezone.utc))
        await db.commit()
        return {"action": decision, "reason": reason}
    finally:
        try:
            tenant_context.reset(token)
        except ValueError:  # teardown ran in another context
            tenant_context.set(None)


@router.post("/turn", response_model=VoiceTurnOut)
async def voice_turn(
    body: VoiceTurnIn,
    db: AsyncSession = Depends(get_db),
    core: AgentCore = Depends(get_agent_core),
) -> dict[str, Any]:
    return await turn_service.handle(db, core, body)
