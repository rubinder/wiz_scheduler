"""The public AI host chat only answers for a subscribed group, under the
location's daily spend cap (#259 step 5)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.config import settings
from backend.main import app
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_rules, save_rules
from backend.reservations.subscription import AgentUsage
from backend.routers.public_reservations import get_agent_core
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import ScriptedLlm, reply, text
from tests.reservations.conftest import subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BASE = f"/api/v1/public/reservations/test123/{LOCATION_ID}"
BUSY = "Online booking is busy right now — please call the restaurant."


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


@pytest_asyncio.fixture
async def llm(client) -> ScriptedLlm:
    scripted = ScriptedLlm()
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(scripted)
    return scripted


async def _cap(db, cap: float = 1.0, phone: str | None = None) -> None:
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    rules.daily_spend_cap_usd = cap
    rules.staff_transfer_number = phone
    await save_rules(db, COMPANY_ID, LOCATION_ID, rules)
    await db.commit()


async def _spend(db, cost: str) -> None:
    db.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                             channel="web", status="closed", outcome="booked",
                             started_at=datetime.now(timezone.utc),
                             cost_usd=Decimal(cost)))
    await db.commit()


async def _all_404(client, token: str | None = None) -> None:
    assert (await client.get(BASE)).status_code == 404
    r = await client.post(f"{BASE}/conversations")
    assert r.status_code == 404 and r.json()["detail"] == "Not found"
    if token:
        r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "hi"})
        assert r.status_code == 404 and r.json()["detail"] == "Not found"


async def test_no_subscription_404s(client, db_session, seeded_floor, llm):
    await _all_404(client)
    assert (await db_session.execute(select(AgentConversation))).scalars().all() == []


async def test_canceled_subscription_404s_every_route(client, db_session, seeded_floor, llm):
    sub = await subscribe(db_session, "active")
    r = await client.post(f"{BASE}/conversations")
    assert r.status_code == 201
    token = r.json()["conversation_token"]
    sub.status = "canceled"
    await db_session.commit()
    await _all_404(client, token)
    assert llm.requests == []


async def test_trialing_answers(client, db_session, seeded_floor, llm):
    await subscribe(db_session, "trialing")
    assert (await client.get(BASE)).status_code == 200
    assert (await client.post(f"{BASE}/conversations")).status_code == 201


async def test_cap_reached_at_start_closes_the_new_conversation(
        client, db_session, seeded_floor, llm):
    await subscribe(db_session)
    await _cap(db_session, 1.0)
    await _spend(db_session, "1.000000")
    r = await client.post(f"{BASE}/conversations")
    assert r.status_code == 201
    body = r.json()
    assert body["reply_text"] == BUSY and body["expect"] == "text"
    assert body["ended"] is True
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == body["conversation_token"]))).scalar_one()
    assert (conv.status, conv.outcome) == ("closed", "blocked")
    assert (await db_session.execute(select(AgentUsage))).scalars().all() == []
    # Its token has nothing more to say.
    r = await client.post(f"{BASE}/conversations/{body['conversation_token']}/turns",
                          json={"text": "hi"})
    assert r.status_code == 404
    assert llm.requests == []


async def test_cap_reply_names_the_transfer_number(client, db_session, seeded_floor, llm):
    await subscribe(db_session)
    await _cap(db_session, 1.0, phone="+15551234567")
    await _spend(db_session, "2")
    r = await client.post(f"{BASE}/conversations")
    assert r.json()["reply_text"] == (
        "Online booking is busy right now — please call the restaurant at +15551234567.")


async def test_under_the_cap_the_agent_answers(client, db_session, seeded_floor, llm):
    await subscribe(db_session)
    await _cap(db_session, 1.0)
    await _spend(db_session, "0.99")
    r = await client.post(f"{BASE}/conversations")
    assert r.json()["reply_text"] != BUSY and r.json()["ended"] is False
    llm.push(reply(text("Sure, for how many?")))
    r = await client.post(f"{BASE}/conversations/{r.json()['conversation_token']}/turns",
                          json={"text": "a table tonight"})
    assert r.status_code == 200 and r.json()["reply_text"] == "Sure, for how many?"


async def test_cap_reached_mid_conversation_closes_on_that_turn(
        client, db_session, seeded_floor, llm):
    await subscribe(db_session)
    await _cap(db_session, 1.0)
    r = await client.post(f"{BASE}/conversations")
    token = r.json()["conversation_token"]
    await _spend(db_session, "1.5")
    r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply_text"] == BUSY and body["ended"] is True
    assert llm.requests == []
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    await db_session.refresh(conv)
    assert conv.status == "closed" and conv.turn_started_at is None
    r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "hi"})
    assert r.status_code == 404


def test_cap_defaults_and_floor():
    from pydantic import ValidationError

    from backend.reservations.rules import RulesSchema

    assert RulesSchema().daily_spend_cap_usd == 20.0
    with pytest.raises(ValidationError):
        RulesSchema(daily_spend_cap_usd=0.5)


async def _inflight(db, n: int) -> None:
    """n other conversations at the location with a turn running now."""
    now = datetime.now(timezone.utc)
    db.add_all([AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                  channel="web", status="open", started_at=now,
                                  turn_started_at=now) for _ in range(n)])
    await db.commit()


async def test_turns_in_flight_count_toward_the_cap_at_start(
        client, db_session, seeded_floor, llm, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATION_AGENT_TURN_COST_BOUND_USD", 0.05)
    await subscribe(db_session)
    await _cap(db_session, 1.0)
    await _spend(db_session, "0.96")
    r = await client.post(f"{BASE}/conversations")
    assert r.json()["ended"] is False  # 0.96 alone is under the cap
    await _inflight(db_session, 1)     # 0.96 + 0.05
    r = await client.post(f"{BASE}/conversations")
    assert r.json()["reply_text"] == BUSY and r.json()["ended"] is True


async def test_too_many_turns_in_flight_is_409_and_the_chat_stays_open(
        client, db_session, seeded_floor, llm, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATION_AGENT_LOCATION_MAX_INFLIGHT", 10)
    await subscribe(db_session)
    token = (await client.post(f"{BASE}/conversations")).json()["conversation_token"]
    await _inflight(db_session, 10)
    r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "hi"})
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "location_busy",
        "message": "Lots of people are booking right now — please try again in a minute."}
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    await db_session.refresh(conv)
    assert (conv.status, conv.turn_count, conv.turn_started_at) == ("open", 0, None)
    assert llm.requests == []


async def test_cap_close_mid_conversation_meters_what_was_spent(
        client, db_session, seeded_floor, llm):
    await subscribe(db_session)
    await _cap(db_session, 5.0)
    token = (await client.post(f"{BASE}/conversations")).json()["conversation_token"]
    llm.push(reply(text("For how many?"), usage={"input_tokens": 2_000_000}))  # $0.20
    r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "a table"})
    assert r.status_code == 200
    await _spend(db_session, "5")
    r = await client.post(f"{BASE}/conversations/{token}/turns", json={"text": "four"})
    assert r.json()["reply_text"] == BUSY and r.json()["ended"] is True
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    await db_session.refresh(conv)
    assert conv.status == "closed" and conv.outcome == "abandoned"
    (row,) = (await db_session.execute(select(AgentUsage))).scalars().all()
    assert row.conversation_id == conv.id
    assert row.cost_usd == conv.cost_usd > 0
    assert row.billed_usd == Decimal("0.30")
