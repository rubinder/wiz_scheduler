from decimal import Decimal

import pytest

from backend.reservations.agent import recording
from tests.conftest import COMPANY_ID, LOCATION_ID

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
    expected = Decimal("382.5") / Decimal(1_000_000)
    assert recording.cost_of(usage) == expected
    assert recording.cost_of({}) == Decimal(0)
    e1 = await recording.record_event(db_session, conv, "assistant_turn", {}, usage=usage)
    await recording.record_event(db_session, conv, "assistant_turn", {}, usage=usage)
    assert e1.cost_usd == expected
    assert e1.input_tokens == 1000
    assert conv.cost_usd == expected * 2
    await db_session.refresh(conv)
    assert conv.cost_usd == Decimal("0.000765")


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
