"""Manager conversations view (#259): list by local day and outcome, detail
with ordered events. Same auth and scope rules as the other manager routes."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Location, Region
from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
BASE = "/api/v1/reservations"
DAY = date(2030, 1, 15)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _local(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=NY).astimezone(timezone.utc)


async def _conv(
    db: AsyncSession, *, started_at: datetime, outcome: str | None = None,
    location_id: str = LOCATION_ID, company_id: str = COMPANY_ID,
) -> AgentConversation:
    conv = await recording.start_conversation(
        db, company_id, location_id, "web", source_key="k" * 64)
    conv.started_at = started_at
    await recording.record_event(
        db, conv, "user_turn",
        {"content": [{"type": "text", "text": "[Now: x]"},
                     {"type": "text", "text": "Table for two?"}]})
    await recording.record_event(
        db, conv, "assistant_turn",
        {"content": [{"type": "text", "text": "Sure, what time?"}],
         "stop_reason": "end_turn"},
        latency_ms={"llm": 420},
        usage={"input_tokens": 1000, "output_tokens": 100})
    await recording.record_event(
        db, conv, "tool_call", {"tool_use_id": "t1", "name": "find_slots",
                                "input": {"party_size": 2}})
    if outcome:
        await recording.finish(db, conv, outcome)
    await db.commit()
    return conv


async def test_list_by_local_day_newest_first(client, manager_token, seeded_floor, db_session):
    early = await _conv(db_session, started_at=_local(DAY, 0, 30), outcome="booked")
    late = await _conv(db_session, started_at=_local(DAY, 23, 30), outcome="abandoned")
    await _conv(db_session, started_at=_local(DAY + timedelta(days=1), 0, 30))
    await _conv(db_session, started_at=_local(DAY, 0, 0) - timedelta(minutes=1))

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations",
                         params={"date": DAY.isoformat()}, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [c["id"] for c in rows] == [late.id, early.id]
    row = rows[1]
    assert set(row) == {"id", "channel", "started_at", "ended_at", "outcome",
                        "turn_count", "cost_usd", "reservation_id"}
    assert row["channel"] == "web" and row["outcome"] == "booked"
    assert isinstance(row["cost_usd"], str) and row["cost_usd"] == "0.000150"
    assert row["ended_at"] is not None

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations",
                         params={"date": DAY.isoformat(), "outcome": "booked"},
                         headers=_auth(manager_token))
    assert [c["id"] for c in r.json()] == [early.id]


async def test_unknown_outcome_is_422(client, manager_token, seeded_floor):
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations",
                         params={"date": DAY.isoformat(), "outcome": "nope"},
                         headers=_auth(manager_token))
    assert r.status_code == 422


async def test_detail_has_ordered_events_and_no_secrets(
    client, manager_token, seeded_floor, db_session
):
    conv = await _conv(db_session, started_at=_local(DAY, 19), outcome="handed_off")
    r = await client.get(f"{BASE}/conversations/{conv.id}", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "source_key" not in r.text and "public_token" not in r.text
    assert conv.public_token not in r.text
    assert body["id"] == conv.id and body["location_id"] == LOCATION_ID
    assert body["outcome"] == "handed_off"
    events = body["events"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert [e["type"] for e in events] == ["user_turn", "assistant_turn", "tool_call"]
    a = events[1]
    assert a["latency_ms"] == {"llm": 420}
    assert a["input_tokens"] == 1000 and a["output_tokens"] == 100
    assert a["cost_usd"] == "0.000150"
    assert a["payload"]["content"][0]["text"] == "Sure, what time?"
    assert events[2]["payload"]["name"] == "find_slots"
    assert events[0]["created_at"]


async def test_routes_404_when_flag_off(client, manager_token, seeded_floor, db_session, monkeypatch):
    conv = await _conv(db_session, started_at=_local(DAY, 19))
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    h = _auth(manager_token)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations?date=2030-01-15", headers=h)
    assert r.status_code == 404
    assert (await client.get(f"{BASE}/conversations/{conv.id}", headers=h)).status_code == 404


async def test_employee_is_forbidden(client, employee_token, seeded_floor, db_session):
    conv = await _conv(db_session, started_at=_local(DAY, 19))
    h = _auth(employee_token)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations?date=2030-01-15", headers=h)
    assert r.status_code == 403
    assert (await client.get(f"{BASE}/conversations/{conv.id}", headers=h)).status_code == 403


async def test_other_company_is_404(client, manager_token, seeded_floor, db_session):
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    await db_session.commit()
    theirs = await _conv(db_session, started_at=_local(DAY, 19),
                         location_id="loc2xxxx", company_id=COMPANY2_ID)
    h = _auth(manager_token)
    r = await client.get(f"{BASE}/locations/loc2xxxx/conversations?date=2030-01-15", headers=h)
    assert r.status_code == 404
    assert (await client.get(f"{BASE}/conversations/{theirs.id}", headers=h)).status_code == 404
    assert (await client.get(f"{BASE}/conversations/nope1234", headers=h)).status_code == 404


async def test_regular_manager_out_of_scope_is_404(
    client, regular_token, seeded_floor, other_location, db_session
):
    mine = await _conv(db_session, started_at=_local(DAY, 19))
    out = await _conv(db_session, started_at=_local(DAY, 19), location_id=OTHER_LOCATION_ID)
    h = _auth(regular_token)
    r = await client.get(
        f"{BASE}/locations/{OTHER_LOCATION_ID}/conversations?date=2030-01-15", headers=h)
    assert r.status_code == 404
    assert (await client.get(f"{BASE}/conversations/{out.id}", headers=h)).status_code == 404
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/conversations?date=2030-01-15", headers=h)
    assert [c["id"] for c in r.json()] == [mine.id]
    assert (await client.get(f"{BASE}/conversations/{mine.id}", headers=h)).status_code == 200


async def test_detail_drops_raw_and_thinking(client, manager_token, seeded_floor, db_session):
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    conv.started_at = _local(DAY, 19)
    content = [
        {"type": "thinking", "thinking": "secret plan", "signature": "sig"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "Sure, what time?"},
    ]
    await recording.record_event(
        db_session, conv, "assistant_turn",
        {"content": content, "raw": "RAWDUMP", "stop_reason": "end_turn"})
    await db_session.commit()
    r = await client.get(f"{BASE}/conversations/{conv.id}", headers=_auth(manager_token))
    assert r.status_code == 200
    [event] = r.json()["events"]
    assert "raw" not in event["payload"]
    assert event["payload"]["content"] == [{"type": "text", "text": "Sure, what time?"}]
    assert event["payload"]["stop_reason"] == "end_turn"
    assert "secret plan" not in r.text and "opaque" not in r.text and "RAWDUMP" not in r.text


async def test_config_lists_conversation_outcomes(client, manager_token, seed_location):
    """The conversations filter reads outcomes from here, so the frontend
    cannot drift from agent/models.py."""
    from backend.reservations.agent.models import OUTCOMES
    r = await client.get(f"{BASE}/config", headers=_auth(manager_token))
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "conversation_outcomes": list(OUTCOMES)}
