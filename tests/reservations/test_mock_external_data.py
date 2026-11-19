"""The mock external provider's data outside the agent (#259 step 9): the
admin-only provider selection, external bookings in the host view, and the
mock's rows in diner data requests, retention and group deletion."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, OwnershipGroup
from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from backend.reservations.mock_external_models import (
    MockExternalBooking,
    MockExternalIdempotency,
)
from backend.reservations.models import BOOKED, SOURCE_STAFF, Reservation
from backend.services.data_retention import run_data_retention
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
BASE = "/api/v1/reservations"
ADA = "+12125550100"
BOB = "+12125550199"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "RESERVATIONS_MOCK_PROVIDER_ALLOWED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _day():
    return datetime.now(timezone.utc).astimezone(NY).date() + timedelta(days=7)


def _at(hh: int, mm: int = 0, day=None) -> datetime:
    return datetime.combine(day or _day(), time(hh, mm), tzinfo=NY)


def _mock(start: datetime, *, status: str = BOOKED, phone: str = ADA, name: str = "Ada",
          location_id: str = LOCATION_ID, code: str | None = "MOCK23",
          held_until: datetime | None = None, minutes: int = 90) -> MockExternalBooking:
    return MockExternalBooking(
        company_id=COMPANY_ID, location_id=location_id, start_at=start,
        end_at=start + timedelta(minutes=minutes), party_size=2, status=status,
        code=code, diner_name=name, diner_phone=phone, held_until=held_until)


# -- rules: provider selection is admin-only -----------------------------------


async def _rules(client, token) -> dict:
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()["rules"]


async def test_rules_carry_the_provider_and_mock_settings(client, manager_token, seeded_floor):
    rules = await _rules(client, manager_token)
    assert rules["provider"] == "wizscheduler"
    assert rules["mock_external"] == {"max_covers_per_slot": 40, "latency_ms": 0,
                                      "failure_rate": 0.0}
    rules["provider"] = "mock_external"
    rules["mock_external"] = {"max_covers_per_slot": 12, "latency_ms": 250,
                              "failure_rate": 0.2}
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules,
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    got = await _rules(client, manager_token)
    assert got["provider"] == "mock_external" and got["mock_external"]["latency_ms"] == 250
    for bad in ({"failure_rate": 0.6}, {"latency_ms": 3001}, {"max_covers_per_slot": 0}):
        rules["mock_external"] = {**got["mock_external"], **bad}
        r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules,
                             headers=_auth(manager_token))
        assert r.status_code == 422, bad
    rules["mock_external"], rules["provider"] = got["mock_external"], "elsewhere"
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules,
                         headers=_auth(manager_token))
    assert r.status_code == 422


async def test_a_regular_manager_cannot_change_the_provider_or_mock_settings(
        client, manager_token, regular_token, seeded_floor):
    rules = await _rules(client, regular_token)
    url = f"{BASE}/locations/{LOCATION_ID}/rules"
    for change in ({"provider": "mock_external"},
                   {"mock_external": {**rules["mock_external"], "failure_rate": 0.3}}):
        r = await client.put(url, json={**rules, **change}, headers=_auth(regular_token))
        assert r.status_code == 403, change
        assert r.json()["detail"]["code"] == "admin_only"
    assert (await _rules(client, manager_token))["provider"] == "wizscheduler"

    # Unchanged values, or the fields left out, are fine for a regular manager,
    # and leaving them out keeps what an admin stored.
    assert (await client.put(url, json=rules, headers=_auth(regular_token))).status_code == 200
    admin_rules = {**rules, "provider": "mock_external",
                   "mock_external": {**rules["mock_external"], "latency_ms": 100}}
    assert (await client.put(url, json=admin_rules,
                             headers=_auth(manager_token))).status_code == 200
    omitted = {k: v for k, v in rules.items() if k not in ("provider", "mock_external")}
    omitted["cutoff_minutes"] = 45
    r = await client.put(url, json=omitted, headers=_auth(regular_token))
    assert r.status_code == 200, r.text
    got = await _rules(client, manager_token)
    assert got["cutoff_minutes"] == 45
    assert got["provider"] == "mock_external" and got["mock_external"]["latency_ms"] == 100
    # Sending the stored (non-default) values back unchanged is not a change.
    assert (await client.put(url, json=got, headers=_auth(regular_token))).status_code == 200


# -- the host view --------------------------------------------------------------


async def test_the_day_listing_merges_live_external_bookings(
        client, manager_token, seeded_floor, db_session: AsyncSession, other_location):
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/reservations", json={
        "party_size": 2, "start": _at(19).isoformat(),
        "diner": {"name": "Wiz", "phone": BOB}}, headers=_auth(manager_token))
    assert r.status_code == 201, r.text
    now = datetime.now(timezone.utc)
    booked = _mock(_at(18))
    held = _mock(_at(20), status="held", code=None, name="Held",
                 held_until=now + timedelta(minutes=5))
    db_session.add_all([
        booked, held,
        _mock(_at(18, 30), status="held", code=None, name="Lapsed",
              held_until=now - timedelta(minutes=1)),
        _mock(_at(21), status="cancelled", code="GONE23", name="Cancelled"),
        _mock(_at(18), code="ELSE23", name="Elsewhere", location_id=OTHER_LOCATION_ID),
        _mock(_at(18, day=_day() + timedelta(days=1)), code="NEXT23", name="Tomorrow"),
    ])
    await db_session.commit()

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/reservations",
                         params={"date": _day().isoformat()}, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    items = r.json()
    assert [(i["diner_name"], i["external"]) for i in items] == [
        ("Ada", True), ("Wiz", False), ("Held", True)]
    ext = items[0]
    assert ext["id"] == booked.id and ext["provider"] == "mock_external"
    assert ext["tables"] == [] and ext["status"] == "booked"
    assert ext["confirmation_code"] == "MOCK23" and ext["source"] == "agent"
    assert items[1]["provider"] == "wizscheduler" and items[1]["tables"]
    assert items[2]["status"] == "held" and items[2]["held_until"] is not None
    # Staff routes act on WizScheduler reservations only: an external id is 404.
    r = await client.post(f"{BASE}/{booked.id}/status", json={"status": "seated"},
                          headers=_auth(manager_token))
    assert r.status_code == 404


# -- diner data -----------------------------------------------------------------


async def _conv(db: AsyncSession, provider: str, **refs) -> AgentConversation:
    conv = await recording.start_conversation(db, COMPANY_ID, LOCATION_ID, "web",
                                              source_key="s" * 64)
    conv.provider = provider
    for k, v in refs.items():
        setattr(conv, k, v)
    await recording.record_event(db, conv, "user_turn",
                                 {"content": [{"type": "text", "text": "hi"}]})
    await db.commit()
    return conv


async def test_diner_data_covers_the_mock_bookings_and_matches_refs_by_provider(
        client, manager_token, seeded_floor, db_session: AsyncSession, other_location):
    now = datetime.now(timezone.utc)
    ada_mock = _mock(_at(18))
    ada_old = _mock(_at(17), status="cancelled", code=None)
    bob_mock = _mock(_at(19), phone=BOB, name="Bob", code="BOBB23")
    ada_elsewhere = _mock(_at(18), location_id=OTHER_LOCATION_ID, code="ELSE23")
    db_session.add_all([ada_mock, ada_old, bob_mock, ada_elsewhere])
    await db_session.flush()
    # Bob's WizScheduler reservation, forced onto Ada's mock booking's id: a
    # WizScheduler conversation pointing at that id is Bob's, not Ada's.
    start = now + timedelta(days=3)
    db_session.add(Reservation(
        id=ada_mock.id, company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=2,
        start_at=start, end_at=start + timedelta(hours=2), status=BOOKED,
        diner_name="Bob", diner_phone=BOB, source=SOURCE_STAFF))
    db_session.add(MockExternalIdempotency(
        company_id=COMPANY_ID, key="c:book:1",
        response={"ref": ada_mock.id, "diner_name": "Ada", "diner_phone": ADA}))
    db_session.add(MockExternalIdempotency(
        company_id=COMPANY_ID, key="c:book:2",
        response={"ref": bob_mock.id, "diner_name": "Bob", "diner_phone": BOB}))
    await db_session.commit()
    mine = await _conv(db_session, "mock_external", reservation_id=ada_mock.id)
    staged = await _conv(db_session, "mock_external", pending_cancel_id=ada_old.id)
    wiz_same_id = await _conv(db_session, "wizscheduler", reservation_id=ada_mock.id)
    bobs = await _conv(db_session, "mock_external", held_reservation_id=bob_mock.id)

    h = _auth(manager_token)
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                          json={"phone": ADA}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(x["id"] for x in body["reservations"]) == sorted([ada_mock.id, ada_old.id])
    assert all(x["external"] and x["provider"] == "mock_external"
               for x in body["reservations"])
    assert sorted(x["id"] for x in body["conversations"]) == sorted([mine.id, staged.id])
    assert "Bob" not in r.text and "ELSE23" not in r.text

    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["external_bookings_deleted"] == 2 and out["reservations_scrubbed"] == 0
    assert out["conversations_deleted"] == 2

    keep = sorted([bob_mock.id, ada_elsewhere.id])
    ids = {"ada": ada_mock.id, "mine": mine.id, "staged": staged.id,
           "wiz": wiz_same_id.id, "bobs": bobs.id}
    db_session.expire_all()
    left = (await db_session.execute(select(MockExternalBooking.id))).scalars().all()
    assert sorted(left) == keep
    keys = (await db_session.execute(select(MockExternalIdempotency.key))).scalars().all()
    assert keys == ["c:book:2"]
    assert await db_session.get(AgentConversation, ids["mine"]) is None
    assert await db_session.get(AgentConversation, ids["staged"]) is None
    assert await db_session.get(AgentConversation, ids["wiz"]) is not None
    assert await db_session.get(AgentConversation, ids["bobs"]) is not None
    assert (await db_session.get(Reservation, ids["ada"])).diner_name == "Bob"


# -- retention --------------------------------------------------------------------


async def test_retention_scrubs_old_mock_bookings_like_reservations(
        db_session: AsyncSession, seed_location):
    now = datetime.now(timezone.utc)
    window = timedelta(days=settings.RETENTION_RESERVATIONS_DAYS)
    old = _mock(now - window - timedelta(days=2), code="OLD234")
    recent = _mock(now - window + timedelta(days=2), code="NEW234")
    db_session.add_all([old, recent])
    await db_session.commit()

    old_id, recent_id = old.id, recent.id
    summary = await run_data_retention(db_session)
    assert summary["old_mock_external_diners_scrubbed"] == 1
    db_session.expire_all()
    row = await db_session.get(MockExternalBooking, old_id)
    assert row is not None and (row.diner_name, row.diner_phone) == (None, None)
    assert row.party_size == 2  # kept for covers, like a reservation
    assert (await db_session.get(MockExternalBooking, recent_id)).diner_phone == ADA
    assert (await run_data_retention(db_session))["old_mock_external_diners_scrubbed"] == 0


# -- group deletion ------------------------------------------------------------------


async def test_group_deletion_deletes_the_mock_rows(db_session: AsyncSession, seed_location):
    from backend.services.billing import delete_og_and_dependents

    og = OwnershipGroup(id="ogmock01", name="Mock Group", ai_credits_usd=0.0)
    db_session.add(og)
    await db_session.flush()
    (await db_session.get(Company, COMPANY_ID)).ownership_group_id = og.id
    db_session.add(_mock(_at(18)))
    db_session.add(MockExternalIdempotency(company_id=COMPANY_ID, key="c:book:1",
                                           response={"ref": "x"}))
    await db_session.commit()

    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    for model in (MockExternalBooking, MockExternalIdempotency):
        count = (await db_session.execute(
            select(func.count()).select_from(model))).scalar_one()
        assert count == 0, model.__tablename__
    assert await db_session.get(Company, COMPANY_ID) is None


async def test_the_mock_provider_is_refused_unless_allowed(
        client, manager_token, seeded_floor, monkeypatch):
    url = f"{BASE}/locations/{LOCATION_ID}/rules"
    h = _auth(manager_token)
    rules = await _rules(client, manager_token)
    monkeypatch.setattr(settings, "RESERVATIONS_MOCK_PROVIDER_ALLOWED", False)
    r = await client.put(url, json={**rules, "provider": "mock_external"}, headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "mock_provider_not_allowed"
    assert (await _rules(client, manager_token))["provider"] == "wizscheduler"

    # Already stored as mock: still accepted, so an existing demo keeps saving.
    monkeypatch.setattr(settings, "RESERVATIONS_MOCK_PROVIDER_ALLOWED", True)
    assert (await client.put(url, json={**rules, "provider": "mock_external"},
                             headers=h)).status_code == 200
    monkeypatch.setattr(settings, "RESERVATIONS_MOCK_PROVIDER_ALLOWED", False)
    stored = await _rules(client, manager_token)
    assert (await client.put(url, json={**stored, "cutoff_minutes": 50},
                             headers=h)).status_code == 200
    # Switching away is always allowed.
    r = await client.put(url, json={**stored, "provider": "wizscheduler"}, headers=h)
    assert r.status_code == 200


async def test_config_exposes_the_mock_provider_flag(client, manager_token, monkeypatch):
    for value in (True, False):
        monkeypatch.setattr(settings, "RESERVATIONS_MOCK_PROVIDER_ALLOWED", value)
        r = await client.get(f"{BASE}/config", headers=_auth(manager_token))
        assert r.json()["mock_provider_allowed"] is value


async def test_the_conversation_list_names_its_provider(
        client, manager_token, db_session, seeded_floor):
    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    await db_session.commit()
    r = await client.get(f"{BASE}/conversations/{conv.id}", headers=_auth(manager_token))
    assert r.status_code == 200 and r.json()["provider"] == "wizscheduler"
