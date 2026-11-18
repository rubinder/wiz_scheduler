"""Usage metering on recording.finish (#259 step 5)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from backend.reservations.subscription import AgentUsage
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _conv(db, cost: str) -> AgentConversation:
    conv = await recording.start_conversation(db, COMPANY_ID, LOCATION_ID, "web")
    conv.cost_usd = Decimal(cost)
    await db.flush()
    return conv


async def _usage(db) -> list[AgentUsage]:
    return list((await db.execute(select(AgentUsage))).scalars().all())


async def test_finish_creates_exactly_one_usage_row(db_session, seed_location):
    now = datetime.now(timezone.utc)
    await subscribe(db_session, period_start=now - timedelta(days=3),
                    period_end=now + timedelta(days=27))
    conv = await _conv(db_session, "0.2449")
    await recording.finish(db_session, conv, "booked")
    await recording.finish(db_session, conv, "booked")  # idempotent
    await db_session.commit()
    rows = await _usage(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert (row.company_id, row.ownership_group_id, row.location_id,
            row.conversation_id, row.product) == (
        COMPANY_ID, AGENT_OG_ID, LOCATION_ID, conv.id, "reservation_agent")
    assert row.cost_usd == Decimal("0.244900") and row.billed_usd == Decimal("0.37")
    assert row.period_start == now - timedelta(days=3)


async def test_blocked_and_unbillable_conversations_make_no_row(db_session, seed_location):
    await subscribe(db_session)
    blocked = await _conv(db_session, "1.0")
    await recording.finish(db_session, blocked, "blocked")
    free = await _conv(db_session, "0")
    await recording.finish(db_session, free, "abandoned")
    tiny = await _conv(db_session, "0.0033")  # bills 0.00
    await recording.finish(db_session, tiny, "abandoned")
    await db_session.commit()
    assert await _usage(db_session) == []


async def test_usage_period_falls_back_to_the_utc_month(db_session, seed_location):
    await subscribe(db_session, "trialing")  # no stored period yet
    conv = await _conv(db_session, "1.0")
    await recording.finish(db_session, conv, "handed_off")
    await db_session.commit()
    (row,) = await _usage(db_session)
    assert row.period_start == conv.ended_at.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    assert row.billed_usd == Decimal("1.50")


async def test_no_group_means_no_row_and_no_error(db_session, seed_location):
    conv = await _conv(db_session, "1.0")
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    assert conv.status == "closed"
    assert await _usage(db_session) == []


async def test_usage_survives_the_conversation(db_session, seed_location):
    await subscribe(db_session)
    conv = await _conv(db_session, "1.0")
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    await db_session.delete(conv)
    await db_session.commit()
    (row,) = await _usage(db_session)
    await db_session.refresh(row)
    assert row.conversation_id is None and row.billed_usd == Decimal("1.50")


async def test_group_deletion_removes_usage_and_subscription(db_session, seed_location):
    from backend.models import OwnershipGroup
    from backend.reservations.subscription import ReservationSubscription
    from backend.services.billing import delete_og_and_dependents

    await subscribe(db_session)
    conv = await _conv(db_session, "1.0")
    await recording.finish(db_session, conv, "booked")
    await db_session.commit()
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert await _usage(db_session) == []
    assert (await db_session.execute(select(ReservationSubscription))).scalars().all() == []



async def test_usage_is_metered_as_events_record_cost(db_session, seed_location):
    """Ruling S: an abandoned chat that never finishes is still billed, and
    the period is fixed at the first meter."""
    now = datetime.now(timezone.utc)
    first = now - timedelta(days=3)
    sub = await subscribe(db_session, period_start=first,
                          period_end=first + timedelta(days=30))
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    tiny = {"input_tokens": 1000}  # 0.0001 -> bills 0.00: no row yet
    await recording.record_event(db_session, conv, "assistant_turn", {}, usage=tiny)
    assert await _usage(db_session) == []
    big = {"input_tokens": 2_000_000}  # 0.20
    await recording.record_event(db_session, conv, "assistant_turn", {}, usage=big)
    await db_session.commit()
    (row,) = await _usage(db_session)
    assert row.cost_usd == Decimal("0.200100") and row.billed_usd == Decimal("0.30")
    assert row.period_start == first
    # The webhook moves the period; the row keeps the one it was written in.
    sub.current_period_start = first + timedelta(days=30)
    sub.current_period_end = first + timedelta(days=60)
    await db_session.commit()
    await recording.record_event(db_session, conv, "assistant_turn", {}, usage=big)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.cost_usd == Decimal("0.400100") and row.billed_usd == Decimal("0.60")
    assert row.period_start == first
    assert conv.status == "open"  # never finished, still billed


async def test_an_erased_open_conversation_keeps_its_usage(db_session, seeded_floor):
    from backend.reservations.diner_data import erase_diner_data

    await subscribe(db_session)
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web",
                                              caller_number="+12125550100")
    await recording.record_event(db_session, conv, "assistant_turn", {},
                                 usage={"input_tokens": 2_000_000})
    await db_session.commit()
    conv_id = conv.id
    result = await erase_diner_data(db_session, COMPANY_ID, LOCATION_ID, "+12125550100")
    await db_session.commit()
    assert result["conversations_deleted"] == 1
    db_session.expire_all()
    assert await db_session.get(AgentConversation, conv_id) is None
    (row,) = await _usage(db_session)
    assert row.conversation_id is None and row.billed_usd == Decimal("0.30")
