"""AI host subscription queries (#259 step 5): billed amount, the answering
subscription, spend today and period usage."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.reservations.agent.models import AgentConversation
from backend.reservations.subscription import (
    AgentUsage,
    ReservationSubscription,
    active_for_location,
    billed_amount,
    cap_reached,
    period_usage,
    spend_today,
    usage_period_start,
)
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)  # 22:00 Nov 17 in New York


def test_billed_amount_rounds_half_up_to_the_cent():
    assert billed_amount(Decimal("0.2449")) == Decimal("0.37")  # 0.36735
    assert billed_amount(Decimal("0.0033")) == Decimal("0.00")  # 0.00495
    assert billed_amount(Decimal("0.003334")) == Decimal("0.01")  # 0.005001
    assert billed_amount(Decimal("0")) == Decimal("0.00")


def test_usage_period_start_uses_the_containing_period_else_the_utc_month_without_one():
    sub = ReservationSubscription(
        status="active",
        current_period_start=datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
        current_period_end=datetime(2026, 12, 10, 12, tzinfo=timezone.utc))
    inside = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert usage_period_start(sub, inside) == sub.current_period_start
    assert usage_period_start(None, inside) == datetime(2026, 12, 1, tzinfo=timezone.utc)


def test_usage_period_start_rolls_a_stale_period_by_whole_intervals():
    start = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    sub = ReservationSubscription(status="active", current_period_start=start,
                                  current_period_end=start + timedelta(days=30))
    nxt = start + timedelta(days=30)
    # Just past a stale end: the next period, not the calendar month.
    assert usage_period_start(sub, nxt) == nxt
    assert usage_period_start(sub, nxt + timedelta(seconds=1)) == nxt
    assert usage_period_start(sub, start + timedelta(days=65)) == start + timedelta(days=60)
    # Before the stored start: rolled backward.
    assert usage_period_start(sub, start - timedelta(seconds=1)) == start - timedelta(days=30)
    assert usage_period_start(sub, start - timedelta(days=31)) == start - timedelta(days=60)
    # A start without an end, and no period at all.
    open_ended = ReservationSubscription(status="active", current_period_start=start)
    assert usage_period_start(open_ended, nxt) == start
    bare = ReservationSubscription(status="incomplete")
    assert usage_period_start(bare, nxt) == datetime(2026, 12, 1, tzinfo=timezone.utc)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_active_for_location_needs_an_answering_status(db_session, seed_location):
    assert await active_for_location(db_session, COMPANY_ID, LOCATION_ID, NOW) is None
    sub = await subscribe(db_session, "active")
    assert (await active_for_location(db_session, COMPANY_ID, LOCATION_ID, NOW)).id == sub.id
    # Ruling T: past_due keeps service while Stripe retries the card.
    for status, answers in (("trialing", True), ("past_due", True), ("canceled", False),
                            ("unpaid", False), ("incomplete", False),
                            ("incomplete_expired", False), ("paused", False)):
        sub.status = status
        await db_session.commit()
        got = await active_for_location(db_session, COMPANY_ID, LOCATION_ID, NOW)
        assert (got is not None) == answers, status
    sub.status = "active"
    await db_session.commit()
    # A location id paired with another company does not match.
    assert await active_for_location(db_session, COMPANY2_ID, LOCATION_ID, NOW) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_spend_today_is_the_location_local_day(db_session, seed_location):
    def conv(started: datetime, cost: str, status: str = "closed") -> AgentConversation:
        return AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                 channel="web", status=status, started_at=started,
                                 cost_usd=Decimal(cost))
    db_session.add_all([
        conv(datetime(2026, 11, 17, 4, 59, tzinfo=timezone.utc), "1.0"),   # 23:59 Nov 16
        conv(datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc), "0.25"),   # 00:00 Nov 17
        conv(datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc), "0.5", "open"),
        conv(datetime(2026, 11, 18, 5, 0, tzinfo=timezone.utc), "2.0"),    # 00:00 Nov 18
    ])
    await db_session.commit()
    assert await spend_today(db_session, LOCATION_ID, NOW, "America/New_York") \
        == Decimal("0.75")
    assert await spend_today(db_session, "zzzzzzzz", NOW, "America/New_York") == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_period_usage_sums_one_period_of_one_group(db_session, seed_location):
    await subscribe(db_session)
    p1 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    p2 = datetime(2026, 12, 1, tzinfo=timezone.utc)
    for billed, period in (("0.37", p1), ("1.20", p1), ("9.99", p2)):
        db_session.add(AgentUsage(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                                  location_id=LOCATION_ID, cost_usd=Decimal("0.1"),
                                  billed_usd=Decimal(billed), period_start=period))
    await db_session.commit()
    assert await period_usage(db_session, AGENT_OG_ID, p1) == Decimal("1.57")
    assert await period_usage(db_session, AGENT_OG_ID, p2) == Decimal("9.99")
    assert await period_usage(db_session, "nobodyxx", p1) == Decimal("0.00")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cap_counts_a_bound_per_turn_in_flight(db_session, seed_location, monkeypatch):
    from backend.config import settings
    from backend.reservations.rules import get_rules, save_rules

    monkeypatch.setattr(settings, "RESERVATION_AGENT_TURN_COST_BOUND_USD", 0.05)
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.daily_spend_cap_usd = 1.0
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    now = datetime.now(timezone.utc)
    db_session.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                     channel="web", status="closed", started_at=now,
                                     cost_usd=Decimal("0.9")))
    await db_session.commit()
    assert not await cap_reached(db_session, COMPANY_ID, seed_location, now, 0)
    assert not await cap_reached(db_session, COMPANY_ID, seed_location, now, 1)  # 0.95
    assert await cap_reached(db_session, COMPANY_ID, seed_location, now, 2)      # 1.00
