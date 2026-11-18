"""Public AI host chat (#259): unauthenticated, diner-facing.

A diner starts a conversation at `/{slug}/{location_id}` and posts turns
with the returned token. There is no login, so the tenant comes from the
URL: `public_company_scope` resolves the company by slug and then binds the
request to it (ORM tenant filter and Postgres RLS) before any tenant table
is read. Every route 404s when reservations are off or the company or
location is unknown or archived.

Abuse limits, all keyed on `trusted_client_ip` (the X-Forwarded-For entry
the proxies wrote, not one a client can forge):
- one per-IP window counts conversation starts and turns, checked before
  anything is loaded or any model is called; a looser one covers the info
  read; conversation starts are also capped per location;
- each turn is claimed atomically (`recording.claim_turn`), which enforces
  the turn cap and refuses a second concurrent turn with 409.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import bind_rls_context, get_db
from backend.models import Company, Location
from backend.reservations.agent import recording
from backend.reservations.agent.core import AgentCore, TurnInput
from backend.reservations.agent.llm import AnthropicLlm
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_rules
from backend.schemas.reservation_agent import (
    ConversationOut, LocationInfoOut, TurnIn, TurnOut,
)
from backend.services.archive import active
from backend.services.rate_limit import (
    SlidingWindowLimiter,
    reservation_agent_info_limiter,
    reservation_agent_ip_limiter,
    reservation_agent_location_starts_limiter,
    trusted_client_ip,
)
from backend.tenant import tenant_context

logger = logging.getLogger(__name__)

NOT_FOUND = "Not found"


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise _not_found()


def _limit(limiter: SlidingWindowLimiter, key: str, what: str) -> None:
    if not limiter.check_and_record(key):
        logger.info("reservation_agent.rate_limited %s=%s", what, key)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many requests. Try again later.")


def consume_ip_limit(request: Request) -> None:
    """Runs before the scope lookup and before the body is validated, so a
    malformed request still spends a token."""
    _limit(reservation_agent_ip_limiter, trusted_client_ip(request), "ip")


def consume_info_limit(request: Request) -> None:
    _limit(reservation_agent_info_limiter, trusted_client_ip(request), "ip")


@dataclass(frozen=True)
class PublicScope:
    company_id: str
    location: Location


async def public_company_scope(
    slug: str, location_id: str, db: AsyncSession = Depends(get_db),
) -> AsyncIterator[PublicScope]:
    """The URL's company, bound as this request's tenant, and its active
    location. The tenant context is reset when the request ends, so it
    never outlives it."""
    company_id = (await db.execute(
        select(Company.id).where(Company.slug == slug)
    )).scalar_one_or_none()
    if company_id is None:
        raise _not_found()
    token = tenant_context.set([company_id])
    try:
        await bind_rls_context(db, [company_id])
        location = (await db.execute(
            select(Location).where(Location.id == location_id,
                                   Location.company_id == company_id,
                                   active(Location))
        )).scalar_one_or_none()
        if location is None:
            raise _not_found()
        yield PublicScope(company_id, location)
    finally:
        try:
            tenant_context.reset(token)
        except ValueError:  # teardown ran in another context
            tenant_context.set(None)


_core: AgentCore | None = None


def get_agent_core() -> AgentCore:
    global _core
    if _core is None:
        _core = AgentCore(AnthropicLlm())
    return _core


async def _greeting(db: AsyncSession, scope: PublicScope) -> str:
    rules, _ = await get_rules(db, scope.company_id, scope.location.id)
    return rules.persona.greeting or (
        f"Hi, thanks for contacting {scope.location.name}. "
        "How can I help with your reservation?")


router = APIRouter(
    prefix="/public/reservations",
    tags=["public-reservations"],
    dependencies=[Depends(require_reservations_enabled)],
)


@router.get("/{slug}/{location_id}", response_model=LocationInfoOut,
            dependencies=[Depends(consume_info_limit)])
async def location_info(
    scope: PublicScope = Depends(public_company_scope),
    db: AsyncSession = Depends(get_db),
) -> LocationInfoOut:
    return LocationInfoOut(location_name=scope.location.name,
                           timezone=scope.location.timezone,
                           greeting=await _greeting(db, scope))


@router.post("/{slug}/{location_id}/conversations", response_model=ConversationOut,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(consume_ip_limit)])
async def start_conversation(
    scope: PublicScope = Depends(public_company_scope),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """The greeting turn: no model call, and nothing for the model's history
    (its system prompt already carries the greeting)."""
    _limit(reservation_agent_location_starts_limiter, scope.location.id, "location")
    greeting = await _greeting(db, scope)
    conv = await recording.start_conversation(db, scope.company_id, scope.location.id,
                                              "web")
    token = conv.public_token
    await db.commit()
    return ConversationOut(conversation_token=token, reply_text=greeting, expect="text")


@router.post("/{slug}/{location_id}/conversations/{token}/turns",
             response_model=TurnOut, dependencies=[Depends(consume_ip_limit)])
async def post_turn(
    body: TurnIn,
    token: str = Path(min_length=43, max_length=43),
    scope: PublicScope = Depends(public_company_scope),
    db: AsyncSession = Depends(get_db),
    core: AgentCore = Depends(get_agent_core),
) -> TurnOut:
    conv = (await db.execute(
        select(AgentConversation).where(
            AgentConversation.public_token == token,
            AgentConversation.company_id == scope.company_id,
            AgentConversation.location_id == scope.location.id,
            AgentConversation.status == "open")
    )).scalar_one_or_none()
    if conv is None:
        raise _not_found()
    claim = await recording.claim_turn(db, conv)
    await db.commit()  # so a concurrent request sees the claim
    if claim == "busy":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "turn_in_progress"})
    if claim == "closed":
        raise _not_found()
    out = await core.handle_turn(db, conv, TurnInput(text=body.text, action=body.action))
    return TurnOut.from_turn(out)
