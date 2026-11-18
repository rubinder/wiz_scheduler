"""Public AI host chat (#259): unauthenticated, diner-facing.

A diner starts a conversation at `/{slug}/{location_id}` and posts turns
with the returned token. There is no login, so the tenant comes from the
URL: `public_company_scope` resolves the company by slug and then binds the
request to it (ORM tenant filter and Postgres RLS) before any tenant table
is read. Every route 404s when reservations are off or the company or
location is unknown or archived, and, once PUBLIC_ORIGIN_SECRET is set
(Ruling O), when the X-Origin-Verify header CloudFront adds is missing or
wrong; that check runs first.

Abuse limits, keyed by `trusted_client_keys` (Rulings N, O): every request
passes a per-viewer limit (the diner as CloudFront saw them) and, while the
origin secret is unset, a 50x looser per-hop limit (the peer the ALB saw),
so a forged X-Forwarded-For sent straight to the ALB is still limited by
its real address. With the secret set only CloudFront gets in, and the
hop limit is skipped.
- `consume_ip_limit` counts conversation starts and turns in one window
  (both routes declare it ahead of the scope lookup, so it runs before
  anything is loaded, any model is called or the body is validated);
  `consume_info_limit` is the info read's looser window;
- starts are also limited per (location, viewer); a location past
  RESERVATION_AGENT_LOCATION_STARTS_PER_HOUR is logged, not blocked;
- viewer keys are stored only as `source_key_for` HMACs;
- each turn is claimed atomically (`recording.claim_turn`), which enforces
  the turn cap and refuses a second concurrent turn with 409.

Billing (#259 step 5), after the limits and the scope: every route 404s
unless the location's ownership group has an AI host subscription that
answers (`subscription.active_for_location`). At most
RESERVATION_AGENT_LOCATION_MAX_INFLIGHT turns run at once per location; one
more is refused with 409 `location_busy` and the chat stays open. Once the
location's spend today plus a cost bound per turn in flight reaches its
`daily_spend_cap_usd` (`subscription.cap_reached`), a new conversation is
created closed (`blocked`, so it bills nothing; `ended: true`) and a turn
closes its conversation, both answering `subscription.busy_text`.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

import hmac

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import bind_rls_context, get_db
from backend.models import Company, Location
from backend.reservations.agent import gate, recording
from backend.reservations.agent.core import AgentCore, TurnInput, TurnOutput
from backend.reservations.agent.gate import LOCATION_BUSY_TEXT, get_agent_core
from backend.reservations.agent.models import AgentConversation
from backend.schemas.reservation_agent import (
    ConversationOut, LocationInfoOut, TurnIn, TurnOut,
)
from backend.services.archive import active
from backend.services.rate_limit import (
    SlidingWindowLimiter,
    reservation_agent_hop_info_limiter,
    reservation_agent_hop_limiter,
    reservation_agent_info_limiter,
    reservation_agent_ip_limiter,
    reservation_agent_location_starts_limiter,
    reservation_agent_viewer_starts_limiter,
    source_key_for,
    trusted_client_keys,
)
from backend.tenant import tenant_context

logger = logging.getLogger(__name__)

NOT_FOUND = "Not found"


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)


def require_origin(request: Request) -> None:
    """Ruling O: once PUBLIC_ORIGIN_SECRET is set, only requests carrying it
    (added by CloudFront) exist; anything else is a 404."""
    secret = settings.PUBLIC_ORIGIN_SECRET
    if secret and not hmac.compare_digest(
            request.headers.get("x-origin-verify", "").encode(), secret.encode()):
        raise _not_found()


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise _not_found()


def _limit(limiter: SlidingWindowLimiter, key: str, what: str) -> None:
    if not limiter.check_and_record(key):
        # Never log a raw address (decision 29): the keyed hash only.
        logger.info("reservation_agent.rate_limited %s=%s", what, source_key_for(key))
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many requests. Try again later.")


def consume_ip_limit(request: Request) -> str:
    """The starts-and-turns window. Declared ahead of the scope dependency
    on both routes, and FastAPI raises body errors only after dependencies,
    so a malformed request still spends a token. Returns the viewer key."""
    viewer, hop = trusted_client_keys(request)
    if hop != viewer:
        _limit(reservation_agent_hop_limiter, hop, "hop")
    _limit(reservation_agent_ip_limiter, viewer, "ip")
    return viewer


def consume_info_limit(request: Request) -> None:
    viewer, hop = trusted_client_keys(request)
    if hop != viewer:
        _limit(reservation_agent_hop_info_limiter, hop, "hop")
    _limit(reservation_agent_info_limiter, viewer, "ip")


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


async def require_subscription(
    scope: PublicScope = Depends(public_company_scope),
    db: AsyncSession = Depends(get_db),
) -> PublicScope:
    """The scope, once its group's AI host subscription answers; else 404."""
    if not await gate.subscribed(db, scope.company_id, scope.location.id,
                                 datetime.now(timezone.utc)):
        raise _not_found()
    return scope


async def _busy(db: AsyncSession, scope: PublicScope) -> str:
    return await gate.busy_text(db, scope.company_id, scope.location.id)


async def _greeting(db: AsyncSession, scope: PublicScope) -> str:
    return await gate.greeting(db, scope.company_id, scope.location)


router = APIRouter(
    prefix="/public/reservations",
    tags=["public-reservations"],
    dependencies=[Depends(require_origin), Depends(require_reservations_enabled)],
)


@router.get("/{slug}/{location_id}", response_model=LocationInfoOut,
            dependencies=[Depends(consume_info_limit)])
async def location_info(
    scope: PublicScope = Depends(require_subscription),
    db: AsyncSession = Depends(get_db),
) -> LocationInfoOut:
    return LocationInfoOut(location_name=scope.location.name,
                           timezone=scope.location.timezone,
                           greeting=await _greeting(db, scope))


@router.post("/{slug}/{location_id}/conversations", response_model=ConversationOut,
             status_code=status.HTTP_201_CREATED)
async def start_conversation(
    viewer: str = Depends(consume_ip_limit),
    scope: PublicScope = Depends(public_company_scope),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """The greeting turn: no model call, and nothing for the model's history
    (its system prompt already carries the greeting)."""
    location_id = scope.location.id
    _limit(reservation_agent_viewer_starts_limiter, f"{location_id}|{viewer}",
           "location_viewer")
    if not reservation_agent_location_starts_limiter.check_and_record(location_id):
        # Operator signal only: many diners may share a busy restaurant.
        logger.warning("reservation_agent.location_starts_high location=%s", location_id)
    scope = await require_subscription(scope, db)
    now = datetime.now(timezone.utc)
    conv = await recording.start_conversation(db, scope.company_id, location_id,
                                              "web", source_key=source_key_for(viewer))
    token = conv.public_token
    if await gate.cap_reached(db, scope.company_id, scope.location, now):
        await recording.finish(db, conv, "blocked")
        reply = await _busy(db, scope)
        await db.commit()
        return ConversationOut(conversation_token=token, reply_text=reply,
                               expect="text", ended=True)
    greeting = await _greeting(db, scope)
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
    scope = await require_subscription(scope, db)
    conv = (await db.execute(
        select(AgentConversation).where(
            AgentConversation.public_token == token,
            AgentConversation.company_id == scope.company_id,
            AgentConversation.location_id == scope.location.id,
            AgentConversation.status == "open")
    )).scalar_one_or_none()
    if conv is None:
        raise _not_found()
    claim = await recording.claim_turn_at_location(db, conv)
    await db.commit()  # so a concurrent request sees the claim; frees the lock
    if claim == "location_busy":
        logger.info("reservation_agent.location_busy location=%s", scope.location.id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "location_busy",
                                    "message": LOCATION_BUSY_TEXT})
    if claim == "busy":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "turn_in_progress"})
    if claim == "closed":
        raise _not_found()
    conv_id, company_id = conv.id, conv.company_id
    try:
        now = datetime.now(timezone.utc)
        if await gate.cap_reached(db, scope.company_id, scope.location, now,
                                  exclude_conversation_id=conv_id):
            # Closes as the turn cap does (a live hold released, outcome by
            # what the conversation achieved), clearing the turn mark.
            reply = await _busy(db, scope)
            await core.close_conversation(db, conv, now=now)
            return TurnOut.from_turn(TurnOutput(reply, "text", None, True, None))
        out = await core.handle_turn(db, conv,
                                     TurnInput(text=body.text, action=body.action))
    except BaseException:
        # handle_turn never raises an Exception, so this is a cancellation
        # (the diner went away mid-turn): clear the in-flight mark so the
        # next turn is not refused until it goes stale.
        await gate.clear_mark_after_cancel(db, conv_id, company_id)
        raise
    return TurnOut.from_turn(out)

