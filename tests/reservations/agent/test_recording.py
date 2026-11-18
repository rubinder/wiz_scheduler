import asyncio
from decimal import Decimal

import pytest

from backend.reservations.agent import recording
from tests.conftest import COMPANY_ID, LOCATION_ID, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _conv(db, seed_location):
    return await recording.start_conversation(db, COMPANY_ID, LOCATION_ID, "web")


async def test_seq_increments(db_session, seed_location):
    conv = await _conv(db_session, seed_location)
    seqs = [
        (await recording.record_event(db_session, conv, "user_turn", {"t": i})).seq
        for i in range(3)
    ]
    assert seqs == [1, 2, 3]


async def test_cost_accumulates_exactly(db_session, seed_location):
    conv = await _conv(db_session, seed_location)
    usage = {"input_tokens": 1000, "output_tokens": 500,
             "cache_read_tokens": 2000, "cache_write_tokens": 100}
    # 1000*0.10 + 500*0.50 + 2000*0.01 + 100*0.125 = 382.5 per 1e6
    expected = Decimal("0.000383")  # 382.5 per 1e6, rounded half up to 6 places
    assert recording.cost_of(usage) == expected
    assert recording.cost_of({}) == Decimal(0)
    e1 = await recording.record_event(db_session, conv, "assistant_turn", {}, usage=usage)
    await recording.record_event(db_session, conv, "assistant_turn", {}, usage=usage)
    assert e1.cost_usd == expected
    assert e1.input_tokens == 1000
    assert conv.cost_usd == expected * 2
    await db_session.refresh(conv)
    assert conv.cost_usd == Decimal("0.000766")


async def test_public_token_unique_and_43_chars(db_session, seed_location):
    a = await _conv(db_session, seed_location)
    b = await _conv(db_session, seed_location)
    assert len(a.public_token) == 43 and len(b.public_token) == 43
    assert a.public_token != b.public_token


async def test_finish(db_session, seed_location):
    conv = await _conv(db_session, seed_location)
    assert conv.status == "open" and conv.ended_at is None
    await recording.finish(db_session, conv, "booked")
    assert (conv.outcome, conv.status) == ("booked", "closed")
    assert conv.ended_at is not None


async def test_invalid_values_rejected(db_session, seed_location):
    with pytest.raises(ValueError):
        await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "sms")
    conv = await _conv(db_session, seed_location)
    with pytest.raises(ValueError):
        await recording.record_event(db_session, conv, "bogus", {})
    with pytest.raises(ValueError):
        await recording.finish(db_session, conv, "bogus")


async def test_cost_is_quantized_per_event(db_session, seed_location):
    conv = await _conv(db_session, seed_location)
    # 1 input token = 0.0000001 USD -> rounds to 0.000000; 5 -> 0.0000005 -> 0.000001
    assert recording.cost_of({"input_tokens": 1}) == Decimal("0.000000")
    e = await recording.record_event(db_session, conv, "assistant_turn", {},
                                     usage={"input_tokens": 5})
    assert e.cost_usd == Decimal("0.000001") == conv.cost_usd


async def test_concurrent_writers_keep_seq_and_cost(db_session, seed_location):
    conv = await _conv(db_session, seed_location)
    await db_session.commit()
    factory = get_test_session_factory()
    usage = {"input_tokens": 1000, "output_tokens": 1000}

    async def writer() -> None:
        async with factory() as db:
            from backend.reservations.agent.models import AgentConversation
            c = await db.get(AgentConversation, conv.id)  # loaded before the others commit
            for _ in range(5):
                await recording.record_event(db, c, "assistant_turn", {}, usage=usage)
                await db.commit()

    await asyncio.gather(writer(), writer())
    async with factory() as db:
        from sqlalchemy import select
        from backend.reservations.agent.models import AgentConversation, AgentEvent
        events = (await db.execute(
            select(AgentEvent).where(AgentEvent.conversation_id == conv.id))).scalars().all()
        c = await db.get(AgentConversation, conv.id)
        assert sorted(e.seq for e in events) == list(range(1, 11))
        assert c.cost_usd == sum((e.cost_usd for e in events), Decimal(0)) == \
            recording.cost_of(usage) * 10
