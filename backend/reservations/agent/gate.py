"""What every AI host channel shares around a turn (#259): the agent core,
the greeting, the subscription and spend-cap answers, and the cleanup after
a cancelled turn. The web chat (routers/public_reservations.py) and the
voice bridge (routers/voice_bridge.py) both use these, so the two channels
cannot drift apart."""

import logging
from datetime import datetime

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import RLS_COMPANY_IDS
from backend.models import Location
from backend.reservations import subscription
from backend.reservations.agent import recording
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.llm import AnthropicLlm
from backend.reservations.rules import get_rules

logger = logging.getLogger(__name__)

LOCATION_BUSY_TEXT = "Lots of people are booking right now — please try again in a minute."

CLEAR_LOCK_TIMEOUT = "2s"

_core: AgentCore | None = None


def get_agent_core() -> AgentCore:
    """The process-wide core (a FastAPI dependency, overridden in tests)."""
    global _core
    if _core is None:
        _core = AgentCore(AnthropicLlm())
    return _core


async def greeting(db: AsyncSession, company_id: str, location: Location) -> str:
    rules, _ = await get_rules(db, company_id, location.id)
    return rules.persona.rendered_greeting() or default_greeting(location.name)


def default_greeting(location_name: str) -> str:
    """Says the host is automated (the disclosure a custom greeting must
    keep too, rules.Persona). Shared by the web chat and the voice line."""
    return (f"Hi, you've reached {location_name}'s automated reservations assistant. "
            "How can I help with your reservation?")


async def subscribed(db: AsyncSession, company_id: str, location_id: str,
                     now: datetime) -> bool:
    """The location's group has an AI host subscription that answers."""
    return await subscription.active_for_location(
        db, company_id, location_id, now) is not None


async def busy_text(db: AsyncSession, company_id: str, location_id: str) -> str:
    """The spend cap's reply, logged for the operator."""
    logger.info("reservation_agent.spend_cap_reached location=%s", location_id)
    return await subscription.busy_text(db, company_id, location_id)


async def cap_reached(db: AsyncSession, company_id: str, location: Location,
                      now: datetime, exclude_conversation_id: str | None = None) -> bool:
    """The location's spend today plus a cost bound per turn in flight (other
    than `exclude_conversation_id`'s) has reached its daily cap."""
    inflight = await recording.inflight_turns(
        db, location.id, exclude_conversation_id=exclude_conversation_id)
    return await subscription.cap_reached(db, company_id, location, now, inflight)


async def clear_mark_after_cancel(db: AsyncSession, conv_id: str,
                                  company_id: str) -> None:
    """Best effort, shielded: the cancellation that brought us here would
    otherwise cancel every await in it too.

    The request's session is rolled back first: its uncommitted round may
    hold the conversation row (record_event locks it), and the clear would
    wait on that lock forever. The clear then runs on a fresh session (the
    request's may be mid-statement) with a short lock timeout, so it can
    never hang; on a timeout the mark simply goes stale."""
    with anyio.CancelScope(shield=True):
        try:
            await db.rollback()
        except Exception:
            logger.warning("rollback after a cancelled turn failed (conversation %s)",
                           conv_id, exc_info=True)
        try:
            async with AsyncSession(db.bind, expire_on_commit=False) as fresh:
                fresh.info[RLS_COMPANY_IDS] = [company_id]
                await fresh.execute(text(
                    f"SET LOCAL lock_timeout = '{CLEAR_LOCK_TIMEOUT}'"))
                await recording.clear_turn_mark(fresh, conv_id, company_id)
        except Exception:
            logger.exception("could not clear the turn mark (conversation %s)", conv_id)
