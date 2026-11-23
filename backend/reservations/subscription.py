"""The AI host subscription (#259 step 5): who may use the agent, and what it
used.

`ReservationSubscription` is the ownership group's separately billed agent
subscription (one per group); Stripe keeps it current through the webhook,
and nothing here calls Stripe. `AgentUsage` is one row per billable
conversation: what it cost us and what it bills (cost x markup, to the cent).
It is metered incrementally (Ruling S, `meter`): every cost-bearing event and
the finish upsert the row with the conversation's running total, so a chat
that is abandoned, never finished, or erased (GDPR) mid-way is still billed
for what it spent. Its period is fixed when the row is first written. It
carries no diner data and outlives the conversation (retention and erasure
delete agent_conversations; `conversation_id` is then set NULL).

The agent answers only while the group's subscription is `active`,
`trialing` or `past_due` (Ruling T: Stripe is still retrying the card), or
`comped` (given free by the operator; Stripe never sees it), see
`active_for_location`, and while the location's spend today is under its
`daily_spend_cap_usd` (`cap_reached`).

Spend cap overshoot (Ruling S). `cap_reached` counts the recorded spend of
the location's local day plus RESERVATION_AGENT_TURN_COST_BOUND_USD for every
turn still in flight, and recording.claim_turn_at_location lets at most
RESERVATION_AGENT_LOCATION_MAX_INFLIGHT turns run at once per location
(claimed under a per-location advisory lock, so the count is exact). A turn
that passes the check can therefore take the day past the cap only by what
the in-flight turns spend beyond their reserved bound: at most
MAX_INFLIGHT x (the worst single-turn cost - the bound), where the worst
turn is RESERVATION_AGENT_MAX_TOOL_ROUNDS model calls of at most
RESERVATION_AGENT_MAX_TOKENS output each. Conversation starts make no model
call and add nothing.
"""

import calendar
import logging
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.company import Company
from backend.models.location import Location
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import RulesSchema, get_agent_rules, get_rules
from backend.reservations.subscription_models import (
    COMPED,
    PRODUCT,
    STATUSES,
    AgentUsage,
    ReservationSubscription,
)
from backend.utils.id_gen import generate_short_id

__all__ = ["COMPED", "PRODUCT", "STATUSES", "AgentUsage", "ReservationSubscription"]

# Ruling T (decision 38): past_due keeps service while Stripe retries the
# card; unpaid, paused, canceled, incomplete and incomplete_expired stop it.
# A comped group (no Stripe subscription, #259 step 11) is answered too.
logger = logging.getLogger(__name__)

ANSWERING_STATUSES = ("active", "trialing", "past_due", COMPED)
# Statuses whose usage overage is reconciled onto the next invoice. Kept
# apart from ANSWERING_STATUSES because who may use the agent and what Stripe
# will still invoice are separate questions: a comped group is answered, and
# its usage is metered as cost, but Stripe never invoices it.
BILLABLE_STATUSES = ("active", "trialing", "past_due")
_CENT = Decimal("0.01")
BUSY_TEXT = "Online booking is busy right now — please call the restaurant."


def billed_amount(cost: Decimal) -> Decimal:
    """What a conversation bills: its cost times the markup, half-up to the cent."""
    markup = Decimal(str(settings.RESERVATIONS_USAGE_MARKUP))
    return (Decimal(cost) * markup).quantize(_CENT, rounding=ROUND_HALF_UP)


def included_billed() -> Decimal:
    """Billed usage a period includes: RESERVATIONS_INCLUDED_COST_USD of our
    cost, at the markup (decision 98)."""
    return billed_amount(Decimal(str(settings.RESERVATIONS_INCLUDED_COST_USD)))


def _utc_month_start(at: datetime) -> datetime:
    return at.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(base: datetime, months: int, day: int) -> datetime:
    """`base` moved by `months` calendar months onto day-of-month `day`,
    clamped to the month's last day, keeping its time of day."""
    total = base.year * 12 + (base.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return base.replace(year=year, month=month, day=min(day, last))


def _month_step(start: datetime, end: datetime) -> tuple[int, int] | None:
    """(months per period, anchor day) when start..end is a calendar-month
    period the way Stripe bills one: the same day of month as the billing
    anchor, clamped to the month's end. A start clamped from a later anchor
    (Feb 28 of a 31st anchor) shows the anchor in its end (Mar 31), so the
    anchor day is the later of the two. None for any other length (a daily
    or weekly price, a test clock): those step by a fixed interval."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if months < 1:
        return None
    day = max(start.day, end.day)
    if _add_months(start, months, day) != end:
        return None
    return months, day


def period_start_for(start: datetime, end: datetime | None, at: datetime) -> datetime:
    """The start of the billing period containing `at`, for a subscription
    whose known period is start..end (end None: open-ended).

    A calendar-month period rolls by whole months from the start, anchored
    on Stripe's day of month (Jan 31 -> Feb 28 -> Mar 31), forward when the
    stored period is stale and backward when `at` is before it. Any other
    period rolls by whole intervals of its length. With no end, the start
    once `at` is past it, else the UTC calendar month of `at`."""
    if end is None or end <= start:
        return start if end is None and at >= start else _utc_month_start(at)
    step = _month_step(start, end)
    if step is None:
        interval = end - start
        return start + ((at - start) // interval) * interval
    months, day = step
    k = ((at.year - start.year) * 12 + (at.month - start.month)) // months
    # The month estimate is off by at most one period either way.
    while _add_months(start, k * months, day) > at:
        k -= 1
    while _add_months(start, (k + 1) * months, day) <= at:
        k += 1
    return _add_months(start, k * months, day)


def usage_period_start(sub: ReservationSubscription | None, at: datetime) -> datetime:
    """The start of the billing period that contains `at`: from the
    subscription's stored period (period_start_for), or, with no stored
    period at all, the UTC calendar month of `at`."""
    if sub is not None and sub.current_period_start is not None:
        return period_start_for(sub.current_period_start, sub.current_period_end, at)
    return _utc_month_start(at)


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
    location's group's, while `active`, `trialing`, `past_due` or `comped`
    (ANSWERING_STATUSES). Anything else (no group,
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
    """Cost (model, and voice call cost) of the location's conversations
    started in its local day (open ones included), from
    agent_conversations.cost_usd.

    Conversations count by start day: a chat that crosses local midnight
    counts all of its cost against the day it started, none against the
    next. Bounded: a conversation runs at most RESERVATION_AGENT_MAX_TURNS
    turns of RESERVATION_AGENT_MAX_TOKENS output, so what a chat carried
    over midnight spends uncounted on the new day is that much at most.

    Spend survives erasure (decision 93): a diner erasure deletes
    conversations, and the public demo login can erase its own chats by
    code, so a sum over conversations alone would let chat, book, erase
    reset the day forever. The metered AgentUsage row (subscription.meter)
    outlives its conversation: the FK sets conversation_id NULL and the row
    keeps location_id, cost_usd and created_at (its first metered turn,
    within seconds of the conversation's start). So today's spend is the
    live conversations by start day plus the orphaned usage rows by
    created_at. Not computed from AgentUsage alone: meter writes no row for
    a cost that bills to 0.00, a `blocked` conversation or a company with no
    ownership group, and a live conversation's row lags its cost by the
    metering savepoint. Applies to every group, not only comped ones."""
    zone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)
    start, end = _local_day_bounds(now, zone)
    live = (await db.execute(
        select(func.coalesce(func.sum(AgentConversation.cost_usd), 0))
        .where(AgentConversation.location_id == location_id,
               AgentConversation.started_at >= start,
               AgentConversation.started_at < end)
    )).scalar_one()
    erased = (await db.execute(
        select(func.coalesce(func.sum(AgentUsage.cost_usd), 0))
        .where(AgentUsage.location_id == location_id,
               AgentUsage.conversation_id.is_(None),
               AgentUsage.created_at >= start,
               AgentUsage.created_at < end)
    )).scalar_one()
    return Decimal(live) + Decimal(erased)


async def is_comped_company(db: AsyncSession, company_id: str) -> bool:
    """The company's group holds a comped AI host subscription."""
    return (await db.execute(
        select(ReservationSubscription.id)
        .join(Company, Company.ownership_group_id
              == ReservationSubscription.ownership_group_id)
        .where(Company.id == company_id, ReservationSubscription.status == COMPED)
    )).scalar_one_or_none() is not None


class CompedCapLimit(Exception):
    """A comped group's rules asked for a daily cap above COMPED_DAILY_CAP_USD."""

    def __init__(self, ceiling: float) -> None:
        self.ceiling = ceiling
        super().__init__(
            f"A complimentary plan's daily spend cap cannot be above ${ceiling:.2f}")


async def check_rules_cap(db: AsyncSession, company_id: str, cap: float) -> None:
    """Refuse a rules write whose daily_spend_cap_usd is above
    COMPED_DAILY_CAP_USD for a comped group (CompedCapLimit). daily_cap
    clamps it anyway; a saved cap the location can never reach would only
    mislead the manager."""
    ceiling = settings.COMPED_DAILY_CAP_USD
    if cap > ceiling and await is_comped_company(db, company_id):
        raise CompedCapLimit(ceiling)


async def rules_as_shown(db: AsyncSession, company_id: str, rules: RulesSchema) -> RulesSchema:
    """The rules as the editor reads them: for a comped group the daily cap is
    the effective one, min(stored, COMPED_DAILY_CAP_USD), so saving them
    unchanged passes check_rules_cap (the $20 default would not)."""
    ceiling = settings.COMPED_DAILY_CAP_USD
    if rules.daily_spend_cap_usd > ceiling and await is_comped_company(db, company_id):
        return rules.model_copy(update={"daily_spend_cap_usd": ceiling})
    return rules


async def daily_cap(db: AsyncSession, company_id: str, location_id: str) -> Decimal:
    """The location's effective daily spend cap: its rules' daily_spend_cap_usd,
    and for a comped group never above COMPED_DAILY_CAP_USD (its usage is
    never invoiced, and a manager can raise the rules cap)."""
    rules, _ = await get_rules(db, company_id, location_id)
    cap = Decimal(str(rules.daily_spend_cap_usd))
    if await is_comped_company(db, company_id):
        cap = min(cap, Decimal(str(settings.COMPED_DAILY_CAP_USD)))
    return cap


async def cap_reached(
    db: AsyncSession, company_id: str, location: Location, now: datetime,
    inflight_turns: int,
) -> bool:
    """True once the location's spend today, plus the cost bound of each of
    `inflight_turns` other turns still running (recording.inflight_turns),
    reaches its effective daily cap (daily_cap). See the module notes on
    overshoot."""
    spent = await spend_today(db, location.id, now, location.timezone)
    reserved = Decimal(str(settings.RESERVATION_AGENT_TURN_COST_BOUND_USD)) * inflight_turns
    return spent + reserved >= await daily_cap(db, company_id, location.id)


async def busy_text(db: AsyncSession, company_id: str, location_id: str) -> str:
    """The closed-chat reply while the cap is reached."""
    rules, _ = await get_agent_rules(db, company_id, location_id)
    phone = rules.staff_transfer_number
    if phone:
        return BUSY_TEXT[:-1] + f" at {phone}."
    return BUSY_TEXT


async def meter(db: AsyncSession, conv: AgentConversation, at: datetime) -> None:
    """Upsert the conversation's AgentUsage row with its running cost (Ruling
    S). Insert when the billed amount first exceeds 0.00, with the period
    containing `at`; later calls update cost and billed amount only (the
    period stays). Never for a `blocked` conversation, nor for a company
    without an ownership group. A DB write only, in a savepoint: metering
    never raises into the call path (it logs instead)."""
    cost = conv.cost_usd or Decimal(0)
    if conv.outcome == "blocked" or cost <= 0:
        return
    billed = billed_amount(cost)
    if billed <= 0:
        return
    try:
        async with db.begin_nested():
            og_id = await ownership_group_of(db, conv.company_id)
            if og_id is None:
                logger.warning("agent usage not metered: company %s has no "
                               "ownership group (conversation %s)",
                               conv.company_id, conv.id)
                return
            sub = await for_group(db, og_id)
            stmt = pg_insert(AgentUsage).values(
                id=generate_short_id(), company_id=conv.company_id,
                ownership_group_id=og_id, location_id=conv.location_id,
                conversation_id=conv.id, cost_usd=cost, billed_usd=billed,
                period_start=usage_period_start(sub, at), product=PRODUCT)
            await db.execute(stmt.on_conflict_do_update(
                index_elements=["conversation_id"],
                set_={"cost_usd": stmt.excluded.cost_usd,
                      "billed_usd": stmt.excluded.billed_usd}))
    except Exception:
        logger.exception("agent usage metering failed (conversation %s)", conv.id)


async def period_usage(db: AsyncSession, og_id: str, period_start: datetime) -> Decimal:
    """Billed amount the group's conversations ran up in one period."""
    total = (await db.execute(
        select(func.coalesce(func.sum(AgentUsage.billed_usd), 0))
        .where(AgentUsage.ownership_group_id == og_id,
               AgentUsage.period_start == period_start)
    )).scalar_one()
    return Decimal(total).quantize(_CENT)
