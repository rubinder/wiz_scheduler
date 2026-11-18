"""Public AI host chat endpoints (#259): tenant binding, validation, limits."""

import asyncio
from datetime import datetime, timedelta, timezone
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
from backend.services.rate_limit import (
    reservation_agent_hop_limiter,
    reservation_agent_info_limiter,
    reservation_agent_location_starts_limiter,
    source_key_for,
    trusted_client_keys,
)
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import ScriptedLlm, last_tool_result, reply, text, tool_use
from tests.reservations.agent.test_core import _booked_res, _day, _hold_script, _lookup
from tests.reservations.conftest import subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
ROOT = "/api/v1/public/reservations"
BASE = f"{ROOT}/test123/{LOCATION_ID}"
PROXY = "130.176.1.1"  # the CloudFront edge the ALB saw


def _via(client_ip: str, spoof: str | None = None) -> dict[str, str]:
    """X-Forwarded-For as the ALB passes it: [client-sent...], client, edge."""
    parts = ([spoof] if spoof else []) + [client_ip, PROXY]
    return {"X-Forwarded-For": ", ".join(parts)}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


@pytest_asyncio.fixture(autouse=True)
async def _subscribed(db_session, seed_company):
    """Every test here is about a subscribed group (tests/reservations/billing
    covers the gate)."""
    await subscribe(db_session)


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
    """A second company, in its own subscribed ownership group."""
    from backend.models import OwnershipGroup
    from backend.reservations.subscription import ReservationSubscription

    db.add(OwnershipGroup(id="ogother1", name="Other OG"))
    await db.flush()
    db.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co",
                   ownership_group_id="ogother1"))
    db.add(ReservationSubscription(ownership_group_id="ogother1", status="active"))
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
                        "handoff": None, "proposal": None}
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
                         "status", "held_until"}
    assert hold["party_size"] == 2 and hold["status"] == "held"
    assert hold["local_date"] == _day(now)
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
    assert out["hold"]["status"] == "booked"
    assert out["hold"]["confirmation_code"] == res.confirmation_code
    assert res.id not in r.text

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
    assert r.json()["proposal"] == {
        "kind": "modify",
        "current": {"local_date": _day(now), "local_time": "19:00", "party_size": 2},
        "proposed": {"local_date": _day(now), "local_time": "20:00", "party_size": 2},
    }

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
    {"text": "   \n\t "},
    {"text": "hi\u0000there"},
    {"text": "hi\u001bthere"},
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


async def test_61st_request_from_one_ip_is_429_before_the_model(client, seeded_floor, llm,
                                                                caplog):
    ip = _via("198.51.100.7")
    token = await _start(client, headers=ip)  # starts count too
    for _ in range(settings.RESERVATION_AGENT_IP_TURNS_PER_HOUR - 1):
        # A rejected body still spends a token.
        assert (await _turn(client, token, {}, headers=ip)).status_code == 422
    llm.push(reply(text("Hello.")))
    with caplog.at_level("INFO"):
        r = await _turn(client, token, {"text": "hi"}, headers=ip)
    assert r.status_code == 429
    # The log names the keyed hash, never the address (decision 29).
    limited = [rec.getMessage() for rec in caplog.records
               if "reservation_agent.rate_limited" in rec.getMessage()]
    assert limited and not any("198.51.100.7" in m for m in limited)
    assert llm.requests == []
    assert (await client.post(f"{BASE}/conversations", headers=ip)).status_code == 429

    other = _via("198.51.100.8")
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


async def test_forged_leftmost_xff_does_not_escape_the_limit(client, seeded_floor, llm):
    token = await _start(client, headers=_via("198.51.100.7", spoof="1.1.1.1"))
    for i in range(settings.RESERVATION_AGENT_IP_TURNS_PER_HOUR - 1):
        r = await _turn(client, token, {}, headers=_via("198.51.100.7", spoof=f"10.0.0.{i}"))
        assert r.status_code == 422
    r = await _turn(client, token, {"text": "hi"},
                    headers=_via("198.51.100.7", spoof="203.0.113.250"))
    assert r.status_code == 429 and llm.requests == []


async def test_starts_are_limited_per_location_and_viewer(client, db_session,
                                                          seeded_floor, llm):
    ip = _via("198.51.100.20")
    for _ in range(settings.RESERVATION_AGENT_VIEWER_STARTS_PER_LOCATION_HOUR):
        await _start(client, headers=ip)
    assert (await client.post(f"{BASE}/conversations", headers=ip)).status_code == 429
    # Another diner here, and the same diner at another restaurant, may start.
    await _start(client, headers=_via("198.51.100.21"))
    loc2 = await _other_company(db_session)
    await _start(client, base=f"{ROOT}/other-co/{loc2}", headers=ip)


async def test_a_busy_location_is_logged_not_blocked(client, db_session, seeded_floor,
                                                     llm, caplog):
    for _ in range(settings.RESERVATION_AGENT_LOCATION_STARTS_PER_HOUR):
        assert reservation_agent_location_starts_limiter.check_and_record(LOCATION_ID)
    with caplog.at_level("WARNING"):
        token = await _start(client, headers=_via("198.51.100.22"))
    assert any("location_starts_high" in r.getMessage() and LOCATION_ID in r.getMessage()
               for r in caplog.records)
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    # Stored as an HMAC, never the address itself.
    assert conv.source_key == source_key_for("198.51.100.22")
    assert len(conv.source_key) == 64 and "198.51" not in conv.source_key


async def test_direct_to_alb_forgery_is_limited_by_the_hop_key(client, seeded_floor, llm):
    real = "203.0.113.66"  # the sender's own address, appended by the ALB
    token = await _start(client, headers={"X-Forwarded-For": f"9.9.9.1, {real}"})
    for _ in range(settings.RESERVATION_AGENT_HOP_TURNS_PER_HOUR - 2):
        assert reservation_agent_hop_limiter.check_and_record(real)
    llm.push(reply(text("Hello.")))
    r = await _turn(client, token, {"text": "hi"},
                    headers={"X-Forwarded-For": f"9.9.9.2, {real}"})
    assert r.status_code == 200  # the 600th from this peer
    r = await _turn(client, token, {"text": "hi"},
                    headers={"X-Forwarded-For": f"9.9.9.3, {real}"})
    assert r.status_code == 429 and len(llm.requests) == 1
    assert (await client.get(BASE, headers={"X-Forwarded-For": f"9.9.9.4, {real}"})
            ).status_code == 200  # the info read has its own, looser budget


async def test_trusted_client_keys_shapes():
    from starlette.requests import Request

    def keys(xff: str | None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
        return trusted_client_keys(Request({"type": "http", "headers": headers,
                                            "client": ("10.1.1.1", 5)}))

    assert keys("198.51.100.7, 130.176.1.1") == ("198.51.100.7", "130.176.1.1")
    assert keys("1.2.3.4, 198.51.100.7, 130.176.1.1") == ("198.51.100.7", "130.176.1.1")
    assert keys("203.0.113.66") == ("203.0.113.66", "203.0.113.66")
    assert keys(None) == ("10.1.1.1", "10.1.1.1")


async def test_info_read_has_its_own_looser_ip_limit(client, seeded_floor, llm):
    ip = _via("198.51.100.30")
    for _ in range(settings.RESERVATION_AGENT_IP_INFO_PER_HOUR - 1):
        assert reservation_agent_info_limiter.check_and_record("198.51.100.30")
    assert (await client.get(BASE, headers=ip)).status_code == 200
    assert (await client.get(BASE, headers=ip)).status_code == 429
    # Starting a conversation is a different budget.
    await _start(client, headers=ip)


async def test_token_must_be_43_chars(client, seeded_floor, llm):
    assert (await _turn(client, "short", {"text": "hi"})).status_code == 422
    assert (await _turn(client, "x" * 44, {"text": "hi"})).status_code == 422


async def test_turn_cap_through_the_api(client, db_session, seeded_floor, llm, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATION_AGENT_MAX_TURNS", 1)
    token = await _start(client)
    llm.push(reply(text("Hello.")))
    assert (await _turn(client, token, {"text": "hi"})).json()["ended"] is False
    r = await _turn(client, token, {"text": "again"})
    assert r.status_code == 200 and r.json()["ended"]
    assert "start a new conversation" in r.json()["reply_text"]
    assert len(llm.requests) == 1
    assert (await _turn(client, token, {"text": "more"})).status_code == 404
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token)
        .execution_options(populate_existing=True))).scalar_one()
    assert (conv.turn_count, conv.outcome, conv.turn_started_at) == (2, "abandoned", None)


async def test_concurrent_turn_on_one_token_is_409(isolated_client, seeded_floor):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(request):
        started.set()
        await release.wait()
        return reply(text("Done."))

    llm = ScriptedLlm(slow)
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(llm)
    token = await _start(isolated_client)
    first = asyncio.create_task(_turn(isolated_client, token, {"text": "one"}))
    await asyncio.wait_for(started.wait(), 10)
    second = await _turn(isolated_client, token, {"text": "two"})
    assert second.status_code == 409
    assert second.json()["detail"] == {"code": "turn_in_progress"}
    release.set()
    r = await asyncio.wait_for(first, 10)
    assert r.status_code == 200 and r.json()["reply_text"] == "Done."
    assert len(llm.requests) == 1
    # The turn ended, so the next one may run.
    llm.push(reply(text("Again.")))
    assert (await _turn(isolated_client, token, {"text": "three"})).status_code == 200


async def test_a_stale_in_flight_mark_does_not_block(client, db_session, seeded_floor, llm):
    token = await _start(client)
    conv = (await db_session.execute(select(AgentConversation).where(
        AgentConversation.public_token == token))).scalar_one()
    conv.turn_started_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    await db_session.commit()
    assert (await _turn(client, token, {"text": "hi"})).status_code == 409
    conv.turn_started_at = datetime.now(timezone.utc) - timedelta(seconds=160)
    await db_session.commit()
    llm.push(reply(text("Hello.")))
    assert (await _turn(client, token, {"text": "hi"})).status_code == 200


async def test_each_round_refreshes_the_in_flight_mark(client, db_session, seeded_floor,
                                                       llm):
    from tests.conftest import get_test_session_factory

    token = await _start(client)
    seen = []

    async def mark() -> datetime:
        async with get_test_session_factory()() as other:
            return (await other.execute(select(AgentConversation.turn_started_at).where(
                AgentConversation.public_token == token))).scalar_one()

    async def first(request):
        seen.append(await mark())
        return reply(tool_use("tu_r", "restaurant_info", {}), stop="tool_use")

    async def second(request):
        seen.append(await mark())
        return reply(text("We're open for dinner."))

    llm.push(first, second)
    assert (await _turn(client, token, {"text": "hours?"})).status_code == 200
    assert seen[0] is not None and seen[1] > seen[0]
    assert await mark() is None


async def test_a_cancelled_turn_clears_its_mark(isolated_client, seeded_floor):
    started = asyncio.Event()

    async def hang(request):
        started.set()
        await asyncio.Event().wait()  # never answers

    llm = ScriptedLlm(hang)
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(llm)
    token = await _start(isolated_client)
    task = asyncio.create_task(_turn(isolated_client, token, {"text": "one"}))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    llm.push(reply(text("Back.")))
    r = await _turn(isolated_client, token, {"text": "two"})
    assert r.status_code == 200 and r.json()["reply_text"] == "Back."


async def test_cancel_while_a_tool_holds_the_conversation_lock(isolated_client,
                                                              seeded_floor, monkeypatch):
    """record_event locks the conversation row until the round commits; a
    cancellation then must not leave the cleanup waiting on that lock."""
    from backend.reservations.agent import tools as agent_tools

    started = asyncio.Event()
    real_execute_tool = agent_tools.execute_tool

    async def hang(*a, **k):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_tools, "execute_tool", hang)
    llm = ScriptedLlm(reply(tool_use("tu_r", "restaurant_info", {}), stop="tool_use"))
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(llm)
    token = await _start(isolated_client)
    task = asyncio.create_task(_turn(isolated_client, token, {"text": "hours?"}))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)  # the cleanup finishes, no hang

    from tests.conftest import get_test_session_factory
    async with get_test_session_factory()() as other:
        conv = (await other.execute(select(AgentConversation).where(
            AgentConversation.public_token == token))).scalar_one()
    assert conv.turn_started_at is None and conv.status == "open"
    monkeypatch.setattr(agent_tools, "execute_tool", real_execute_tool)
    llm.push(reply(text("Back.")))
    r = await asyncio.wait_for(_turn(isolated_client, token, {"text": "two"}), 10)
    assert r.status_code == 200 and r.json()["reply_text"] == "Back."


SECRET = "origin-s3cret"


async def test_origin_secret_404s_every_route_without_the_header(
        client, seeded_floor, llm, monkeypatch):
    token = await _start(client)
    monkeypatch.setattr(settings, "PUBLIC_ORIGIN_SECRET", SECRET)
    for headers in ({}, {"X-Origin-Verify": "wrong"}, {"X-Origin-Verify": SECRET + "x"}):
        for method, path, body in (
            ("get", BASE, None),
            ("post", f"{BASE}/conversations", None),
            ("post", f"{BASE}/conversations/{token}/turns", {"text": "hi"}),
        ):
            r = await client.request(method, path, json=body, headers=headers)
            assert r.status_code == 404 and r.json()["detail"] == "Not found"
    assert llm.requests == []
    # Checked first: none of those spent a rate-limit token.
    ok = {"X-Origin-Verify": SECRET}
    for _ in range(settings.RESERVATION_AGENT_IP_TURNS_PER_HOUR - 2):  # start was 1
        assert (await _turn(client, token, {}, headers=ok)).status_code == 422
    llm.push(reply(text("Hello.")))
    r = await _turn(client, token, {"text": "hi"}, headers=ok)
    assert r.status_code == 200 and r.json()["reply_text"] == "Hello."
    assert (await client.get(BASE, headers=ok)).status_code == 200


async def test_origin_secret_trusts_the_viewer_and_skips_the_hop_limit(
        client, seeded_floor, llm, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_ORIGIN_SECRET", SECRET)
    edge = PROXY
    for _ in range(settings.RESERVATION_AGENT_HOP_TURNS_PER_HOUR):
        assert reservation_agent_hop_limiter.check_and_record(edge)
    # The edge's hop budget is spent, but behind the secret it is not used.
    headers = {**_via("198.51.100.40"), "X-Origin-Verify": SECRET}
    token = await _start(client, headers=headers)
    llm.push(reply(text("Hello.")))
    assert (await _turn(client, token, {"text": "hi"}, headers=headers)).status_code == 200
    # The viewer limit still applies.
    for _ in range(settings.RESERVATION_AGENT_IP_TURNS_PER_HOUR - 2):
        assert (await _turn(client, token, {}, headers=headers)).status_code == 422
    assert (await _turn(client, token, {"text": "hi"}, headers=headers)).status_code == 429


async def test_trusted_client_keys_with_the_origin_secret(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setattr(settings, "PUBLIC_ORIGIN_SECRET", SECRET)
    req = Request({"type": "http", "client": ("10.1.1.1", 5),
                   "headers": [(b"x-forwarded-for", b"1.2.3.4, 198.51.100.7, 130.176.1.1")]})
    assert trusted_client_keys(req) == ("198.51.100.7", "198.51.100.7")


async def test_confirm_on_the_closing_turn_books_through_the_api(
        client, db_session, seeded_floor, llm, monkeypatch):
    """The four button actions pass the turn cap: a Confirm on the closing
    turn still books."""
    monkeypatch.setattr(settings, "RESERVATION_AGENT_MAX_TURNS", 1)
    now = datetime.now(timezone.utc)
    token = await _start(client)
    find = {"party_size": 2, "date": _day(now), "earliest": None, "latest": None}

    def hold(request):
        opt = last_tool_result(request)["options"][0]
        return reply(tool_use("tu_h", "hold_slot", {
            "start_iso": opt["start_iso"], "party_size": 2, "name": "Ada",
            "phone": "+12125550100"}), stop="tool_use")

    llm.push(reply(tool_use("tu_f", "find_slots", find), stop="tool_use"), hold,
             reply(text("Press Confirm.")))
    r = await _turn(client, token, {"text": "table for 2"})
    assert r.json()["expect"] == "confirm_hold"
    r = await _turn(client, token, {"action": "confirm_hold"})
    assert r.status_code == 200, r.text
    assert r.json()["ended"] and r.json()["reply_text"].startswith("You're booked")
