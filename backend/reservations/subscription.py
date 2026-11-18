"""The AI host subscription (#259 step 5): who may use the agent, and what it
used.

`ReservationSubscription` is the ownership group's separately billed agent
subscription (one per group); Stripe keeps it current through the webhook,
and nothing here calls Stripe. `AgentUsage` is one row per billable finished
conversation: what it cost us and what it bills (cost x markup, to the cent),
assigned to the subscription period it ended in. It carries no diner data and
outlives the conversation (retention deletes agent_conversations after
RETENTION_RESERVATIONS_DAYS; `conversation_id` is then set NULL).

The agent answers only while the group's subscription is `active` or
`trialing` (`active_for_location`) and the location's spend today is under its
`daily_spend_cap_usd` (`cap_reached`).
"""

from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.company import Company
from backend.models.location import Location
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_rules
from backend.reservations.subscription_models import (
    PRODUCT,
    STATUSES,
    AgentUsage,
    ReservationSubscription,
)

__all__ = ["PRODUCT", "STATUSES", "AgentUsage", "ReservationSubscription"]

ANSWERING_STATUSES = ("active", "trialing")
_CENT = Decimal("0.01")
BUSY_TEXT = "Online booking is busy right now — please call the restaurant."


def billed_amount(cost: Decimal) -> Decimal:
    """What a conversation bills: its cost times the markup, half-up to the cent."""
    markup = Decimal(str(settings.RESERVATIONS_USAGE_MARKUP))
    return (Decimal(cost) * markup).quantize(_CENT, rounding=ROUND_HALF_UP)


def _utc_month_start(at: datetime) -> datetime:
    return at.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


def usage_period_start(sub: ReservationSubscription | None, ended_at: datetime) -> datetime:
    """The billing period a conversation that ended at `ended_at` belongs to:
    the subscription's current period when it contains `ended_at`, otherwise
    the UTC calendar month of `ended_at`."""
    if sub is not None and sub.current_period_start is not None:
        start, end = sub.current_period_start, sub.current_period_end
        if start <= ended_at and (end is None or ended_at < end):
            return start
    return _utc_month_start(ended_at)


async def ownership_group_of(db: AsyncSession, company_id: str) -> str | None:
    return (await db.execute(
        select(Company.ownership_group_id).where(Company.id == company_id)
    )).scalar_one_or_none()


async def for_group(db: AsyncSession, og_id: str) -> ReservationSubscription | None:
    return (await db.execute(
        select(ReservationSubscription)
        .where(ReservationSubscription.ownership_group_id == og_id)
    )).scalar_one_or_none()


async def active_for_location(
    db: AsyncSession, company_id: str, location_id: str, now: datetime,
) -> ReservationSubscription | None:
    """The subscription that lets the agent answer at this location: the
    location's group's, while `active` or `trialing`. Anything else (no group,
    no subscription, any other status, a location of another company) is None.

    `now` is the caller's clock; the status alone decides today (Stripe moves
    it at period end), so it is accepted for a future grace rule only."""
    del now
    return (await db.execute(
        select(ReservationSubscription)
        .join(Company, Company.ownership_group_id
              == ReservationSubscription.ownership_group_id)
        .join(Location, Location.company_id == Company.id)
        .where(Company.id == company_id,
               Location.id == location_id,
               ReservationSubscription.status.in_(ANSWERING_STATUSES))
    )).scalar_one_or_none()


def _local_day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    day = now.astimezone(tz).date()
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def spend_today(
    db: AsyncSession, location_id: str, now: datetime, tz: ZoneInfo | str,
) -> Decimal:
    """Model cost of the location's conversations started in its local day
    (open ones included), from agent_conversations.cost_usd."""
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    start, end = _local_day_bounds(now, zone)
    total = (await db.execute(
        select(func.coalesce(func.sum(AgentConversation.cost_usd), 0))
        .where(AgentConversation.location_id == location_id,
               AgentConversation.started_at >= start,
               AgentConversation.started_at < end)
    )).scalar_one()
    return Decimal(total)


async def cap_reached(
    db: AsyncSession, company_id: str, location: Location, now: datetime,
) -> bool:
    """True once the location's spend today reaches its daily_spend_cap_usd."""
    rules, _ = await get_rules(db, company_id, location.id)
    spent = await spend_today(db, location.id, now, location.timezone)
    return spent >= Decimal(str(rules.daily_spend_cap_usd))


async def busy_text(db: AsyncSession, company_id: str, location_id: str) -> str:
    """The closed-chat reply while the cap is reached."""
    rules, _ = await get_rules(db, company_id, location_id)
    phone = rules.staff_transfer_number
    if phone:
        return BUSY_TEXT[:-1] + f" at {phone}."
    return BUSY_TEXT


async def period_usage(db: AsyncSession, og_id: str, period_start: datetime) -> Decimal:
    """Billed amount the group's conversations ran up in one period."""
    total = (await db.execute(
        select(func.coalesce(func.sum(AgentUsage.billed_usd), 0))
        .where(AgentUsage.ownership_group_id == og_id,
               AgentUsage.period_start == period_start)
    )).scalar_one()
    return Decimal(total).quantize(_CENT)
