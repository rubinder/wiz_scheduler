"""Public AI host chat endpoints (#259): tenant binding, validation, limits."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.config import settings
from backend.main import app
from backend.models import Company, Location, Region
from backend.reservations.agent import recording
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.models import AgentConversation
from backend.reservations.models import BOOKED, Reservation
from backend.reservations.rules import get_rules, save_rules
from backend.routers.public_reservations import get_agent_core
from backend.tenant import get_tenant_context
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import ScriptedLlm, last_tool_result, reply, text, tool_use
from tests.reservations.agent.test_core import _booked_res, _day, _hold_script, _lookup

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
ROOT = "/api/v1/public/reservations"
BASE = f"{ROOT}/test123/{LOCATION_ID}"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


@pytest_asyncio.fixture
async def llm(client) -> ScriptedLlm:
    """The core behind the routes, scripted. `client` clears the override."""
    scripted = ScriptedLlm()
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(scripted)
    return scripted


async def _start(client, base: str = BASE, headers: dict | None = None) -> str:
    r = await client.post(f"{base}/conversations", headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()["conversation_token"]


async def _turn(client, token: str, body: dict, base: str = BASE,
                headers: dict | None = None):
    return await client.post(f"{base}/conversations/{token}/turns", json=body,
                             headers=headers or {})


async def _other_company(db) -> str:
    db.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db.flush()
    db.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db.flush()
    db.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                    name="Other", timezone="America/New_York"))
    await db.commit()
    return "loc2xxxx"


async def test_flag_off_404s_every_route(client, seeded_floor, llm, monkeypatch):
    token = await _start(client)
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    for method, path, body in (
        ("get", BASE, None),
        ("post", f"{BASE}/conversations", None),
        ("post", f"{BASE}/conversations/{token}/turns", {"text": "hi"}),
    ):
        r = await client.request(method, path, json=body)
        assert r.status_code == 404 and r.json()["detail"] == "Not found"
    assert llm.requests == []


async def test_unknown_slug_and_location_404(client, seeded_floor, llm):
    for path in (f"{ROOT}/nope/{LOCATION_ID}", f"{ROOT}/test123/zzzzzzzz"):
        assert (await client.get(path)).status_code == 404
        assert (await client.post(f"{path}/conversations")).status_code == 404


async def test_archived_location_404(client, db_session, seeded_floor, llm):
    token = await _start(client)
    loc = (await db_session.execute(
        select(Location).where(Location.id == LOCATION_ID))).scalar_one()
    loc.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert (await client.get(BASE)).status_code == 404
    assert (await client.post(f"{BASE}/conversations")).status_code == 404
    assert (await _turn(client, token, {"text": "hi"})).status_code == 404
    assert llm.requests == []


async def test_location_info_and_greeting(client, db_session, seeded_floor, llm):
    r = await client.get(BASE)
    assert r.status_code == 200
    body = r.json()
    assert body["location_name"] == "Downtown Store"
    assert body["timezone"] == "America/New_York"
    assert "Downtown Store" in body["greeting"]

    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.persona.greeting = "Ciao! Welcome to Luigi's."
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    await db_session.commit()
    assert (await client.get(BASE)).json()["greeting"] == "Ciao! Welcome to Luigi's."


async def test_start_conversation_returns_token_and_greeting(client, db_session,
                                                             seeded_floor, llm):
    r = await client.post(f"{BASE}/conversations")
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"conversation_token", "reply_text", "expect"}
    assert len(body["conversation_token"]) == 43
    assert "Downtown Store" in body["reply_text"] and body["expect"] == "text"
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == body["conversation_token"]))).scalar_one()
    assert (conv.company_id, conv.location_id, conv.channel, conv.status) == (
        COMPANY_ID, LOCATION_ID, "web", "open")
    assert llm.requests == []


async def test_text_turn_round_trip(client, seeded_floor, llm):
    token = await _start(client)
    llm.push(reply(text("Hello! What date would you like?")))
    r = await _turn(client, token, {"text": "Hi there"})
    assert r.status_code == 200, r.text
    assert r.json() == {"reply_text": "Hello! What date would you like?",
                        "expect": "text", "hold": None, "ended": False,
                        "handoff": None}
    assert llm.requests[0]["messages"][-1]["content"][-1]["text"] == "Hi there"
    assert get_tenant_context() is None  # the request's tenant did not leak


async def test_hold_and_confirm_round_trip_exposes_no_ids(client, db_session,
                                                          seeded_floor, llm):
    token = await _start(client)
    now = datetime.now(timezone.utc)
    llm.push(*_hold_script(now))
    r = await _turn(client, token, {"text": "Table for two next week"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["expect"] == "confirm_hold" and not out["ended"]
    hold = out["hold"]
    assert set(hold) == {"party_size", "local_date", "local_time", "start_iso",
                         "status", "held_until", "confirmation_code"}
    assert hold["party_size"] == 2 and hold["status"] == "held"
    assert hold["local_date"] == _day(now) and hold["confirmation_code"] is None
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    assert conv.held_reservation_id not in r.text
    calls = len(llm.requests)

    r = await _turn(client, token, {"action": "confirm_hold"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ended"] and out["reply_text"].startswith("You're booked")
    assert len(llm.requests) == calls  # the button never reaches the model
    res = (await db_session.execute(select(Reservation).where(
        Reservation.company_id == COMPANY_ID))).scalar_one()
    assert res.status == BOOKED and res.confirmation_code in out["reply_text"]

    # The conversation is closed: further turns 404.
    assert (await _turn(client, token, {"text": "thanks"})).status_code == 404


async def test_confirm_modify_round_trip(client, db_session, seeded_floor, llm):
    now = datetime.now(timezone.utc)
    res = await _booked_res(db_session, now)
    token = await _start(client)
    find = {"party_size": 2, "date": _day(now), "earliest": "20:00", "latest": "20:00"}

    def modify(request):
        opt = last_tool_result(request)["options"][0]
        return reply(tool_use("tu_m", "modify_booking",
                              {"party_size": None, "start_iso": opt["start_iso"]}),
                     stop="tool_use")

    llm.push(_lookup(res), reply(tool_use("tu_f", "find_slots", find), stop="tool_use"),
             modify, reply(text("Press Confirm to move it to 8pm.")))
    r = await _turn(client, token, {"text": "move my booking to 8pm"})
    assert r.status_code == 200, r.text
    assert r.json()["expect"] == "confirm_modify"

    r = await _turn(client, token, {"action": "confirm_modify"})
    assert r.status_code == 200, r.text
    assert r.json()["ended"] and res.confirmation_code in r.json()["reply_text"]
    moved = (await db_session.execute(
        select(Reservation).where(Reservation.id == res.id)
        .execution_options(populate_existing=True))).scalar_one()
    assert moved.start_at.astimezone(NY).hour == 20
    assert len(llm.requests) == 4


@pytest.mark.parametrize("body", [
    {"text": "hi", "action": "confirm_hold"},
    {},
    {"text": ""},
    {"text": "x" * 1001},
    {"action": "book_it"},
])
async def test_invalid_turn_bodies_422(client, seeded_floor, llm, body):
    token = await _start(client)
    assert (await _turn(client, token, body)).status_code == 422
    assert llm.requests == []


async def test_max_length_text_is_accepted(client, seeded_floor, llm):
    token = await _start(client)
    llm.push(reply(text("OK.")))
    assert (await _turn(client, token, {"text": "x" * 1000})).status_code == 200


async def test_61st_request_from_one_ip_is_429_before_the_model(client, seeded_floor, llm):
    ip = {"X-Forwarded-For": "198.51.100.7, 10.0.0.1"}
    token = await _start(client, headers=ip)  # starts count too
    for _ in range(settings.RESERVATION_AGENT_IP_TURNS_PER_HOUR - 1):
        # A rejected body still spends a token.
        assert (await _turn(client, token, {}, headers=ip)).status_code == 422
    llm.push(reply(text("Hello.")))
    r = await _turn(client, token, {"text": "hi"}, headers=ip)
    assert r.status_code == 429
    assert llm.requests == []
    assert (await client.post(f"{BASE}/conversations", headers=ip)).status_code == 429

    other = {"X-Forwarded-For": "198.51.100.8"}
    assert (await _turn(client, token, {"text": "hi"}, headers=other)).status_code == 200
    assert len(llm.requests) == 1


async def test_other_companys_token_404s(client, db_session, seeded_floor, llm):
    loc2 = await _other_company(db_session)
    theirs = await _start(client, base=f"{ROOT}/other-co/{loc2}")
    # Their token under our slug and location, and under our slug with theirs.
    assert (await _turn(client, theirs, {"text": "hi"})).status_code == 404
    assert (await _turn(client, theirs, {"text": "hi"},
                        base=f"{ROOT}/test123/{loc2}")).status_code == 404
    assert llm.requests == []
    # It still works where it belongs.
    llm.push(reply(text("Hello.")))
    assert (await _turn(client, theirs, {"text": "hi"},
                        base=f"{ROOT}/other-co/{loc2}")).status_code == 200


async def test_token_from_another_location_404s(client, db_session, seeded_floor, llm):
    db_session.add(Location(id="loc3xxxx", company_id=COMPANY_ID, region_id=seeded_floor
                            .location.region_id, name="Uptown",
                            timezone="America/New_York"))
    await db_session.commit()
    token = await _start(client, base=f"{ROOT}/test123/loc3xxxx")
    assert (await _turn(client, token, {"text": "hi"})).status_code == 404


async def test_unknown_and_closed_tokens_404(client, db_session, seeded_floor, llm):
    assert (await _turn(client, "x" * 43, {"text": "hi"})).status_code == 404
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    await recording.finish(db_session, conv, "abandoned")
    await db_session.commit()
    assert (await _turn(client, conv.public_token, {"action": "confirm_hold"})
            ).status_code == 404
    assert llm.requests == []
