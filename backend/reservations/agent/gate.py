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

from backend.config import settings
from backend.database import RLS_COMPANY_IDS
from backend.models import Location
from backend.reservations import subscription
from backend.reservations.agent import recording
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.llm import AnthropicLlm
from backend.reservations.flows import opening_text
from backend.reservations.rules import RulesSchema, get_rules

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


async def greeting(db: AsyncSession, company_id: str, location: Location,
                   channel: str) -> str:
    """Custom greeting, else the channel's custom flow opening, else the
    default. `channel` is "web" or "voice"."""
    rules, _ = await get_rules(db, company_id, location.id)
    return (rules.custom_greeting()
            or flow_greeting(location.name, rules, channel)
            or default_greeting(location.name, rules.disclose_automated))


def flow_greeting(location_name: str, rules: RulesSchema, channel: str) -> str | None:
    """With no custom greeting, a custom flow (decision 102) opens with its
    start step's line after the usual introduction; None otherwise (also for
    a start step that says nothing, such as a book step)."""
    flow = rules.flows.for_channel(channel)
    if flow is None or rules.custom_greeting():
        return None
    start = flow.step(flow.start)
    line = opening_text(start) if start is not None else None
    if not line:
        return None
    return f"{_introduction(location_name, rules.disclose_automated)} {line}"


def _introduction(location_name: str, disclose_automated: bool) -> str:
    if not disclose_automated:
        return f"Hi, you've reached {location_name}'s reservations line."
    return f"Hi, you've reached {location_name}'s automated reservations assistant."


def is_demo_location(company_id: str, location_id: str) -> bool:
    """The public demo restaurant, whose manager login every visitor sees
    (settings.RESERVATIONS_DEMO_COMPANY_ID / RESERVATIONS_DEMO_LOCATION_ID)."""
    return (company_id == settings.RESERVATIONS_DEMO_COMPANY_ID
            and location_id == settings.RESERVATIONS_DEMO_LOCATION_ID)


def default_greeting(location_name: str, disclose_automated: bool = True) -> str:
    """Says the host is automated unless the location turned the disclosure
    off (rules.RulesSchema.disclose_automated). Shared by the web chat and
    the voice line. It asks the first question itself (a regular table or an
    event, decision 100), so the diner's first answer is already useful."""
    question = "Are you booking a table, or is this about an event or private party?"
    return f"{_introduction(location_name, disclose_automated)} {question}"


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
