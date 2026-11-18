"""Manager reservations API."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.config import settings
from backend.models import Company, Location, Region
from tests.conftest import COMPANY2_ID, LOCATION_ID, OTHER_LOCATION_ID

NY = ZoneInfo("America/New_York")
BASE = "/api/v1/reservations"
PHONE = "+12125550100"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _day():
    return datetime.now(timezone.utc).astimezone(NY).date() + timedelta(days=7)


def _at(hh: int, mm: int = 0) -> str:
    return datetime.combine(_day(), time(hh, mm), tzinfo=NY).isoformat()


def _booking(party: int = 3, hh: int = 18) -> dict:
    return {"party_size": party, "start": _at(hh),
            "diner": {"name": "Ada", "phone": PHONE}, "notes": "window"}


async def _book(client, token, party: int = 3, hh: int = 18):
    return await client.post(
        f"{BASE}/locations/{LOCATION_ID}/reservations",
        json=_booking(party, hh), headers=_auth(token))


async def test_routes_404_when_flag_off(client, manager_token, seed_location, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/floor", headers=_auth(manager_token))
    assert r.status_code == 404
    r = await client.post(f"{BASE}/x/approve", headers=_auth(manager_token))
    assert r.status_code == 404


async def test_floor_and_rules_round_trip(client, manager_token, seed_location):
    h = _auth(manager_token)
    floor = {
        "sections": [{"key": "main", "name": "Main"}],
        "tables": [{"label": "T1", "section_key": "main", "min_seats": 2, "max_seats": 4}],
    }
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/floor", json=floor, headers=h)
    assert r.status_code == 200, r.text
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/floor", headers=h)
    assert [t["label"] for t in r.json()["tables"]] == ["T1"]

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)
    assert r.json()["version"] == 0
    rules = r.json()["rules"]
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 1
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)
    assert r.json()["version"] == 1


async def test_invalid_floor_is_422(client, manager_token, seed_location):
    floor = {"tables": [
        {"label": "T1", "min_seats": 2, "max_seats": 4},
        {"label": "T1", "min_seats": 2, "max_seats": 4}]}
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/floor", json=floor,
                         headers=_auth(manager_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "duplicate_label"


async def test_staff_booking_then_slot_disappears(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    q = {"party_size": 3, "date": _day().isoformat()}
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/slots", params=q, headers=h)
    assert r.status_code == 200
    starts = [s["start"] for s in r.json()]
    assert starts
    r = await _book(client, manager_token)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "booked" and body["tables"] == ["T2"]
    assert body["confirmation_code"] and body["diner_name"] == "Ada"
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/slots", params=q, headers=h)
    wanted = datetime.fromisoformat(_at(18))
    still = [s for s in r.json() if datetime.fromisoformat(s["start"]) == wanted]
    assert still == []

    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/reservations",
                         params={"date": _day().isoformat()}, headers=h)
    assert [x["id"] for x in r.json()] == [body["id"]]
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/reservations",
                         params={"date": (_day() + timedelta(days=1)).isoformat()}, headers=h)
    assert r.json() == []


async def test_double_booking_answers_409(client, manager_token, seeded_floor):
    assert (await _book(client, manager_token)).status_code == 201
    r = await _book(client, manager_token)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "slot_unavailable"


async def test_party_size_zero_is_422(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/slots",
                         params={"party_size": 0, "date": _day().isoformat()}, headers=h)
    assert r.status_code == 422
    r = await _book(client, manager_token, party=0)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_party_size"


async def test_bad_phone_is_422(client, manager_token, seeded_floor):
    body = _booking()
    body["diner"]["phone"] = "555"
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/reservations", json=body,
                          headers=_auth(manager_token))
    assert r.status_code == 422


async def test_archiving_table_with_booking_is_409(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    assert (await _book(client, manager_token)).status_code == 201
    floor = (await client.get(f"{BASE}/locations/{LOCATION_ID}/floor", headers=h)).json()
    floor["tables"] = [t for t in floor["tables"] if t["label"] != "T2"]
    floor["combinations"] = []
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/floor", json=floor, headers=h)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "table_has_bookings" and d["table_ids"] == [seeded_floor.t2]


async def test_regular_manager_outside_scope_gets_404(
    client, regular_token, other_location
):
    h = _auth(regular_token)
    for method, path in [
        ("get", f"{BASE}/locations/{OTHER_LOCATION_ID}/floor"),
        ("get", f"{BASE}/locations/{OTHER_LOCATION_ID}/rules"),
        ("get", f"{BASE}/locations/{OTHER_LOCATION_ID}/reservations?date=2030-01-01"),
    ]:
        assert (await getattr(client, method)(path, headers=h)).status_code == 404
    r = await client.post(f"{BASE}/locations/{OTHER_LOCATION_ID}/reservations",
                          json=_booking(), headers=h)
    assert r.status_code == 404


async def test_reservation_in_other_scope_is_404(
    client, manager_token, regular_token, seeded_floor, other_location, db_session
):
    from backend.reservations.models import Reservation
    r = await _book(client, manager_token)
    rid = r.json()["id"]
    # Move it to the location the regular manager cannot see.
    res = await db_session.get(Reservation, rid)
    res.location_id = OTHER_LOCATION_ID
    await db_session.commit()
    h = _auth(regular_token)
    assert (await client.patch(f"{BASE}/{rid}", json={"notes": "x"}, headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/status", json={"status": "cancelled"},
                              headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/approve", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/decline", headers=h)).status_code == 404


async def test_other_company_location_is_404(client, manager_token, db_session):
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    await db_session.commit()
    h = _auth(manager_token)
    assert (await client.get(f"{BASE}/locations/loc2xxxx/floor", headers=h)).status_code == 404
    r = await client.post(f"{BASE}/locations/loc2xxxx/reservations", json=_booking(), headers=h)
    assert r.status_code == 404


async def test_modify_and_status_flow(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    rid = (await _book(client, manager_token)).json()["id"]
    r = await client.patch(f"{BASE}/{rid}", json={"notes": "birthday", "party_size": 2},
                           headers=h)
    assert r.status_code == 200 and r.json()["notes"] == "birthday"
    assert r.json()["party_size"] == 2
    r = await client.patch(f"{BASE}/{rid}", json={"party_size": 0}, headers=h)
    assert r.status_code == 422
    r = await client.post(f"{BASE}/{rid}/status", json={"status": "completed"}, headers=h)
    assert r.status_code == 409
    for s in ("seated", "completed"):
        r = await client.post(f"{BASE}/{rid}/status", json={"status": s}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == s
    r = await client.post(f"{BASE}/{rid}/status", json={"status": "bogus"}, headers=h)
    assert r.status_code == 422
    r = await client.post(f"{BASE}/nope1234/status", json={"status": "seated"}, headers=h)
    assert r.status_code == 404


async def test_cancel_and_approve_decline(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    rid = (await _book(client, manager_token)).json()["id"]
    r = await client.post(f"{BASE}/{rid}/status", json={"status": "cancelled"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # A booked (not pending) row cannot be approved or declined.
    rid2 = (await _book(client, manager_token)).json()["id"]
    assert (await client.post(f"{BASE}/{rid2}/approve", headers=h)).status_code == 409
    assert (await client.post(f"{BASE}/{rid2}/decline", headers=h)).status_code == 409
