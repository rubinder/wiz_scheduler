"""Manager reservations API."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

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


_ROUTES = [
    ("get", f"/locations/{LOCATION_ID}/floor"),
    ("put", f"/locations/{LOCATION_ID}/floor"),
    ("get", f"/locations/{LOCATION_ID}/rules"),
    ("put", f"/locations/{LOCATION_ID}/rules"),
    ("get", f"/locations/{LOCATION_ID}/slots?party_size=2&date=2030-01-01"),
    ("get", f"/locations/{LOCATION_ID}/reservations?date=2030-01-01"),
    ("post", f"/locations/{LOCATION_ID}/reservations"),
    ("post", f"/locations/{LOCATION_ID}/walk-ins"),
    ("get", f"/locations/{LOCATION_ID}/approvals"),
    ("patch", "/abcd1234"),
    ("post", "/abcd1234/status"),
    ("post", "/abcd1234/approve"),
    ("post", "/abcd1234/decline"),
]


@pytest.mark.parametrize("method,path", _ROUTES)
async def test_routes_404_when_flag_off(
    client, manager_token, seed_location, monkeypatch, method, path
):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await client.request(method, BASE + path, json={}, headers=_auth(manager_token))
    assert r.status_code == 404
    assert r.json()["detail"] == "Not found"


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


async def test_voice_knobs_round_trip_through_rules(client, manager_token, seed_location):
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    assert rules["voice"] == {"voice_reply_max_sentences": 0, "voice_read_back": False,
                              "voice_confirm_style": "question"}
    rules["voice"] = {"voice_reply_max_sentences": 2, "voice_read_back": True,
                      "voice_confirm_style": "statement"}
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text
    got = await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)
    assert got.json()["rules"]["voice"] == rules["voice"]
    rules["voice"]["voice_reply_max_sentences"] = 9
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 422



async def test_rules_put_rejects_a_greeting_that_hides_the_automated_host(
        client, manager_token, seed_location):
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    rules["persona"]["greeting"] = "Ciao! Welcome to Luigi's."
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 422
    assert "automated" in r.text
    rules["persona"]["greeting"] = "Ciao! Luigi's reservations Assistant here."
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 422
    rules["persona"]["greeting"] = "Ciao! Luigi's Automated reservations assistant."
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text

async def test_rules_put_without_voice_keeps_stored_voice(client, manager_token, seed_location):
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    rules["voice"] = {"voice_reply_max_sentences": 3, "voice_read_back": True,
                      "voice_confirm_style": "statement"}
    assert (await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules,
                             headers=h)).status_code == 200
    del rules["voice"]
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200 and r.json()["rules"]["voice"]["voice_read_back"] is True
    got = await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)
    assert got.json()["rules"]["voice"]["voice_reply_max_sentences"] == 3


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


async def _list(client, token, day, location_id=LOCATION_ID):
    r = await client.get(f"{BASE}/locations/{location_id}/reservations",
                         params={"date": day.isoformat()}, headers=_auth(token))
    assert r.status_code == 200
    return r.json()


async def test_failed_modify_rolls_back(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    # Party 3 fits only T2; two parties at 18:00 and 19:30 (default turn 90+
    # min) - move the second onto the first's time.
    first = (await _book(client, manager_token, party=3, hh=18)).json()
    second = (await _book(client, manager_token, party=3, hh=20)).json()
    before = {r["id"]: r for r in await _list(client, manager_token, _day())}
    r = await client.patch(f"{BASE}/{second['id']}", json={"start": _at(18)}, headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "slot_unavailable"
    after = {r["id"]: r for r in await _list(client, manager_token, _day())}
    assert after[second["id"]]["start"] == before[second["id"]]["start"]
    assert after[second["id"]]["party_size"] == 3
    assert after[second["id"]]["tables"] == ["T2"]
    assert after[first["id"]] == before[first["id"]]


async def test_failed_modify_keeps_table_row_active(
    client, manager_token, seeded_floor, db_session
):
    from sqlalchemy import select
    from backend.reservations.models import ReservationTable
    await _book(client, manager_token, party=3, hh=18)
    second = (await _book(client, manager_token, party=3, hh=20)).json()
    r = await client.patch(f"{BASE}/{second['id']}", json={"start": _at(18)},
                           headers=_auth(manager_token))
    assert r.status_code == 409
    rows = (await db_session.execute(
        select(ReservationTable).where(
            ReservationTable.reservation_id == second["id"]))).scalars().all()
    assert len(rows) == 1 and rows[0].active is True


async def test_regular_manager_in_scope_can_read_and_book(
    client, regular_token, seeded_floor, other_location
):
    h = _auth(regular_token)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/floor", headers=h)
    assert r.status_code == 200
    r = await _book(client, regular_token)
    assert r.status_code == 201, r.text


async def test_other_company_reservation_id_is_404(
    client, manager_token, db_session
):
    from backend.models import Company, Location, Region
    from backend.reservations.models import Reservation
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    await db_session.flush()
    start = datetime.now(timezone.utc) + timedelta(days=3)
    res = Reservation(
        company_id=COMPANY2_ID, location_id="loc2xxxx", party_size=2,
        start_at=start, end_at=start + timedelta(hours=1), status="booked",
        source="staff")
    db_session.add(res)
    await db_session.commit()
    h = _auth(manager_token)
    rid = res.id
    assert (await client.patch(f"{BASE}/{rid}", json={"notes": "x"}, headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/status", json={"status": "seated"},
                              headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/approve", headers=h)).status_code == 404
    assert (await client.post(f"{BASE}/{rid}/decline", headers=h)).status_code == 404


async def test_listing_uses_the_local_day_on_a_dst_day(
    client, manager_token, seeded_floor, db_session
):
    from datetime import date
    from backend.reservations.models import Reservation
    # 2027-03-14 is the US spring-forward day: the local day is 23 hours.
    day = date(2027, 3, 14)
    def mk(local: datetime) -> Reservation:
        return Reservation(
            company_id=seeded_floor.location.company_id, location_id=LOCATION_ID,
            party_size=2, start_at=local, end_at=local + timedelta(hours=1),
            status="booked", source="staff")
    late = datetime.combine(day, time(23, 30), tzinfo=NY)
    next_day = datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=NY)
    early = datetime.combine(day, time(0, 30), tzinfo=NY)
    rows = [mk(late), mk(next_day), mk(early)]
    db_session.add_all(rows)
    await db_session.commit()
    got = await _list(client, manager_token, day)
    assert {x["id"] for x in got} == {rows[0].id, rows[2].id}


# --- final review fixes -----------------------------------------------------

async def test_naive_start_is_422(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    body = _booking()
    body["start"] = datetime.combine(_day(), time(18)).isoformat()  # no offset
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/reservations", json=body,
                          headers=h)
    assert r.status_code == 422
    rid = (await _book(client, manager_token)).json()["id"]
    r = await client.patch(f"{BASE}/{rid}", json={"start": body["start"]}, headers=h)
    assert r.status_code == 422


async def test_walk_in_is_seated_without_a_phone(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    url = f"{BASE}/locations/{LOCATION_ID}/walk-ins"
    r = await client.post(url, json={"party_size": 3, "name": "Bo"}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "seated" and body["source"] == "staff"
    assert body["tables"] == ["T2"] and body["diner_phone"] is None
    r = await client.post(url, json={"party_size": 3}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slot_unavailable"
    r = await client.post(url, json={"party_size": 2, "phone": "555"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_phone"
    r = await client.post(url, json={"party_size": 0}, headers=h)
    assert r.status_code == 422


async def test_staff_booking_without_phone_is_201(client, manager_token, seeded_floor):
    body = _booking()
    del body["diner"]["phone"]
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/reservations", json=body,
                          headers=_auth(manager_token))
    assert r.status_code == 201, r.text
    assert r.json()["diner_phone"] is None


async def test_slots_can_preview_the_diner_cutoff(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()
    rules = rules["rules"]
    rules["service_periods"] = [{"name": "all day", "weekdays": list(range(7)),
                                 "first_seating": "00:00", "last_seating": "23:45"}]
    rules["cutoff_minutes"] = 1440
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text
    now = datetime.now(timezone.utc)
    tomorrow = (now.astimezone(NY) + timedelta(days=1)).date().isoformat()
    url = f"{BASE}/locations/{LOCATION_ID}/slots"
    staff = (await client.get(url, params={"party_size": 2, "date": tomorrow},
                              headers=h)).json()
    diner = (await client.get(url, params={"party_size": 2, "date": tomorrow,
                                           "apply_cutoff": "true"}, headers=h)).json()
    assert len(diner) < len(staff)
    assert all(datetime.fromisoformat(s["start"]) >= now + timedelta(minutes=1440)
               for s in diner)


async def test_day_listing_reports_a_lapsed_hold_as_expired(
    client, manager_token, seeded_floor, db_session
):
    from backend.reservations import booking
    from backend.reservations.models import HELD, SOURCE_WEB_AGENT, Reservation
    from tests.conftest import COMPANY_ID
    then = datetime.now(timezone.utc) - timedelta(minutes=10)
    res = await booking.hold(db_session, COMPANY_ID, LOCATION_ID, 2,
                             datetime.fromisoformat(_at(19)), source=SOURCE_WEB_AGENT,
                             now=then)
    rid = res.id
    await db_session.commit()
    got = await _list(client, manager_token, _day())
    assert [(x["id"], x["status"]) for x in got] == [(rid, "expired")]
    # Reported, not written.
    status = (await db_session.execute(
        select(Reservation.status).where(Reservation.id == rid))).scalar_one()
    assert status == HELD


async def test_archiving_a_location_with_upcoming_reservations_is_409(
    client, manager_token, seeded_floor
):
    h = _auth(manager_token)
    rid = (await _book(client, manager_token)).json()["id"]
    r = await client.delete(f"/api/v1/locations/{LOCATION_ID}", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "location_has_reservations"
    assert (await client.post(f"{BASE}/{rid}/status", json={"status": "cancelled"},
                              headers=h)).status_code == 200
    r = await client.delete(f"/api/v1/locations/{LOCATION_ID}", headers=h)
    assert r.status_code == 204


async def test_config_reports_flag_without_gating(client, manager_token, monkeypatch):
    h = _auth(manager_token)
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await client.get(f"{BASE}/config", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    r = await client.get(f"{BASE}/config", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is True


async def test_every_reservations_route_but_config_is_gated():
    """A route added without the gate would answer while the flag is off."""
    from fastapi.routing import APIRoute
    from backend.main import app
    from backend.routers.reservations import require_reservations_enabled

    def calls(dependant) -> set:
        out = {d.call for d in dependant.dependencies}
        for d in dependant.dependencies:
            out |= calls(d)
        return out

    def walk(routes, prefix: str = "", inherited: frozenset = frozenset()):
        """APIRoutes with their full path and include-level dependencies.
        FastAPI >= 0.16x keeps included routers nested (`original_router`);
        older versions flatten them into app.routes."""
        for r in routes:
            if isinstance(r, APIRoute):
                yield prefix + r.path, calls(r.dependant) | inherited
            elif hasattr(r, "original_router"):
                ctx = r.include_context
                extra = frozenset(d.dependency for d in (ctx.dependencies or []))
                yield from walk(r.original_router.routes, prefix + (ctx.prefix or ""),
                                inherited | extra)

    routes = [(path, deps) for path, deps in walk(app.routes)
              if path.startswith("/api/v1/reservations")]
    assert len(routes) >= 13
    ungated = sorted(path for path, deps in routes
                     if require_reservations_enabled not in deps)
    assert ungated == ["/api/v1/reservations/config"]


# --- approval queue ---------------------------------------------------------

def _pending(company_id: str, location_id: str, start: datetime,
             held_until: datetime | None, status: str = "pending_approval",
             submitted: bool = True):
    import secrets
    from backend.reservations.models import Reservation
    return Reservation(
        company_id=company_id, location_id=location_id, party_size=2,
        start_at=start, end_at=start + timedelta(hours=1), status=status,
        source="web_agent", held_until=held_until, diner_name="P",
        confirmation_code=secrets.token_hex(4).upper() if submitted else None)


async def test_approvals_lists_live_requests_on_any_day(
    client, manager_token, seeded_floor, other_location, db_session
):
    from tests.conftest import COMPANY_ID
    now = datetime.now(timezone.utc)
    live_later = _pending(COMPANY_ID, LOCATION_ID, now + timedelta(days=5), now + timedelta(hours=20))
    live_soon = _pending(COMPANY_ID, LOCATION_ID, now + timedelta(hours=3), None)
    lapsed = _pending(COMPANY_ID, LOCATION_ID, now + timedelta(days=2), now - timedelta(minutes=1))
    past = _pending(COMPANY_ID, LOCATION_ID, now - timedelta(hours=1), now + timedelta(hours=5))
    booked = _pending(COMPANY_ID, LOCATION_ID, now + timedelta(days=1), None, status="booked")
    elsewhere = _pending(COMPANY_ID, OTHER_LOCATION_ID, now + timedelta(days=1), None)
    # A large-party hold the diner has not submitted (no code) is not a request.
    unsubmitted = _pending(COMPANY_ID, LOCATION_ID, now + timedelta(hours=2),
                           now + timedelta(minutes=10), submitted=False)
    db_session.add_all([live_later, live_soon, lapsed, past, booked, elsewhere, unsubmitted])
    await db_session.commit()
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/approvals",
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    got = r.json()
    assert [x["id"] for x in got] == [live_soon.id, live_later.id]
    assert all(x["status"] == "pending_approval" for x in got)


async def test_approvals_other_company_and_scope_are_404(
    client, manager_token, regular_token, other_location, db_session
):
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    await db_session.commit()
    r = await client.get(f"{BASE}/locations/loc2xxxx/approvals", headers=_auth(manager_token))
    assert r.status_code == 404
    r = await client.get(f"{BASE}/locations/{OTHER_LOCATION_ID}/approvals",
                         headers=_auth(regular_token))
    assert r.status_code == 404


async def test_approvals_404_when_flag_off(client, manager_token, seed_location, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    r = await client.get(f"{BASE}/locations/{LOCATION_ID}/approvals",
                         headers=_auth(manager_token))
    assert r.status_code == 404 and r.json()["detail"] == "Not found"


async def test_staff_transfer_number_is_screened_for_toll_fraud(
        client, manager_token, seed_location, db_session):
    """Outbound transfers are billed per minute: only US/Canada numbers,
    no premium-rate or invalid area codes, never any AI host number."""
    from backend.reservations.voice_number_models import VoiceNumber
    from tests.reservations.conftest import AGENT_OG_ID, subscribe

    await subscribe(db_session)
    own = "+12125550199"
    elsewhere = "+13125550199"  # another tenant's AI host number, on hold
    db_session.add(VoiceNumber(company_id=seed_location.company_id,
                               ownership_group_id=AGENT_OG_ID, location_id=LOCATION_ID,
                               e164=own, status="active", connect_phone_number_id="pn-1"))
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/Chicago"))
    await db_session.flush()
    db_session.add(VoiceNumber(company_id=COMPANY2_ID, ownership_group_id=AGENT_OG_ID,
                               location_id="loc2xxxx", e164=elsewhere, status="releasing",
                               connect_phone_number_id="pn-2"))
    await db_session.commit()
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    for bad in ("+442071234567", "+18765550100", "+18095550100", "+19005550100",
                "+19765550100", "+12129765555", "+14115550100", "+12121234567",
                "+1212555010", own, elsewhere):
        r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules",
                             json={**rules, "staff_transfer_number": bad}, headers=h)
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["detail"]["code"] == "transfer_number_not_allowed", bad
    for good in ("+12125550111", "+14165550100", None):  # US, Toronto
        r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules",
                             json={**rules, "staff_transfer_number": good}, headers=h)
        assert r.status_code == 200, (good, r.text)


async def test_rules_put_without_voice_survives_invalid_stored_rules(
        client, manager_token, seed_location, db_session):
    from sqlalchemy import update

    from backend.reservations.models import ReservationRules
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    assert (await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules,
                             headers=h)).status_code == 200
    bad = dict(rules, voice={"voice_reply_max_sentences": 99})
    await db_session.execute(update(ReservationRules).where(
        ReservationRules.location_id == LOCATION_ID).values(rules=bad))
    await db_session.commit()
    del rules["voice"]
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200
    assert r.json()["rules"]["voice"]["voice_reply_max_sentences"] == 0


async def test_a_comped_group_cannot_save_a_cap_above_the_comped_ceiling(
        client, manager_token, seed_location, db_session, monkeypatch):
    from tests.reservations.conftest import subscribe
    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    await subscribe(db_session, "comped")
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    rules["daily_spend_cap_usd"] = 3.01
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "comped_cap_limit"
    assert detail["ceiling_usd"] == 3.0 and "$3.00" in detail["message"]
    rules["daily_spend_cap_usd"] = 3.0
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text


async def test_a_paying_group_can_save_a_cap_above_the_comped_ceiling(
        client, manager_token, seed_location, db_session, monkeypatch):
    from tests.reservations.conftest import subscribe
    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    await subscribe(db_session, "active")
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    rules["daily_spend_cap_usd"] = 50
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text


async def test_a_comped_group_with_default_rules_round_trips_a_save(
        client, manager_token, seed_location, db_session, monkeypatch):
    """The $20 default cap reads back as the comped ceiling, so saving the
    rules unchanged is not refused."""
    from tests.reservations.conftest import subscribe
    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    await subscribe(db_session, "comped")
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    assert rules["daily_spend_cap_usd"] == 3.0
    r = await client.put(f"{BASE}/locations/{LOCATION_ID}/rules", json=rules, headers=h)
    assert r.status_code == 200, r.text


async def test_a_paying_group_reads_its_stored_cap(
        client, manager_token, seed_location, db_session, monkeypatch):
    from tests.reservations.conftest import subscribe
    monkeypatch.setattr(settings, "COMPED_DAILY_CAP_USD", 3.0)
    await subscribe(db_session, "active")
    h = _auth(manager_token)
    rules = (await client.get(f"{BASE}/locations/{LOCATION_ID}/rules", headers=h)).json()["rules"]
    assert rules["daily_spend_cap_usd"] == 20.0
