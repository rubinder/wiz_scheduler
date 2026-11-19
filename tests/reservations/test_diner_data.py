"""Diner data requests (#259, Ruling P): one diner's reservations and
conversations at one location, found by phone, and erased on request."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Location, Region
from backend.reservations import booking
from backend.reservations.agent import recording
from backend.reservations.diner_data import erase_diner_data
from backend.reservations.agent.models import (
    AgentConversation, AgentEvent, ReservationLookupFailure,
)
from backend.reservations.models import BOOKED, CANCELLED, SOURCE_WEB_AGENT, Reservation
from tests.conftest import (
    COMPANY2_ID, COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID,
    get_test_session_factory,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BASE = "/api/v1/reservations"
ADA = "+12125550100"
BOB = "+12125550199"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _res(phone: str, name: str, status: str = BOOKED,
         location_id: str = LOCATION_ID, company_id: str = COMPANY_ID) -> Reservation:
    start = datetime.now(timezone.utc) + timedelta(days=3)
    return Reservation(
        company_id=company_id, location_id=location_id, party_size=2,
        start_at=start, end_at=start + timedelta(hours=2), status=status,
        diner_name=name, diner_phone=phone, diner_email=f"{name.lower()}@example.com",
        notes=f"{name} notes", source=SOURCE_WEB_AGENT,
    )


async def _conv(db: AsyncSession, *, location_id: str = LOCATION_ID,
                company_id: str = COMPANY_ID, caller: str | None = None,
                reservation_id: str | None = None,
                held_reservation_id: str | None = None,
                text: str = "hello") -> AgentConversation:
    conv = await recording.start_conversation(
        db, company_id, location_id, "web", caller_number=caller,
        source_key="s" * 64)
    conv.reservation_id = reservation_id
    conv.held_reservation_id = held_reservation_id
    await recording.record_event(
        db, conv, "user_turn",
        {"content": [{"type": "text", "text": text}], "raw": "RAWDUMP"})
    await db.commit()
    return conv


@pytest_asyncio.fixture
async def diners(db_session: AsyncSession, seeded_floor, other_location):
    ada1, ada2 = _res(ADA, "Ada"), _res(ADA, "Ada", CANCELLED)
    bob = _res(BOB, "Bob")
    ada_elsewhere = _res(ADA, "Ada", location_id=OTHER_LOCATION_ID)
    db_session.add_all([ada1, ada2, bob, ada_elsewhere])
    await db_session.commit()
    convs = {
        "booked": await _conv(db_session, reservation_id=ada1.id, text="Ada booked"),
        "held": await _conv(db_session, held_reservation_id=ada2.id, text="Ada held"),
        "caller": await _conv(db_session, caller=ADA, text="Ada called"),
        "bob": await _conv(db_session, reservation_id=bob.id, text="Bob booked"),
        "elsewhere": await _conv(db_session, location_id=OTHER_LOCATION_ID,
                                 caller=ADA, text="Ada elsewhere"),
    }
    db_session.add_all([
        ReservationLookupFailure(company_id=COMPANY_ID, location_id=LOCATION_ID, phone=ADA),
        ReservationLookupFailure(company_id=COMPANY_ID, location_id=LOCATION_ID, phone=BOB),
        ReservationLookupFailure(company_id=COMPANY_ID, location_id=OTHER_LOCATION_ID, phone=ADA),
    ])
    await db_session.commit()
    return {"ada": [ada1.id, ada2.id], "bob": bob.id, "ada_elsewhere": ada_elsewhere.id,
            "convs": {k: v.id for k, v in convs.items()}}


async def test_export_returns_only_that_diner_at_that_location(client, manager_token, diners):
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                         json={"phone": ADA}, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(x["id"] for x in body["reservations"]) == sorted(diners["ada"])
    assert {x["diner_name"] for x in body["reservations"]} == {"Ada"}
    assert body["reservations"][0]["diner_phone"] == ADA
    c = diners["convs"]
    assert sorted(x["id"] for x in body["conversations"]) == sorted(
        [c["booked"], c["held"], c["caller"]])
    assert all(x["events"] for x in body["conversations"])
    assert "Bob" not in r.text and "elsewhere" not in r.text
    assert "source_key" not in r.text and "public_token" not in r.text
    assert "RAWDUMP" not in r.text


async def test_invalid_phone_is_422(client, manager_token, seeded_floor):
    h = _auth(manager_token)
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                         json={"phone": "555-0100"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_phone"
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": "nope"}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_phone"


async def test_erase_scrubs_and_deletes_only_that_diner(
    client, manager_token, diners, db_session: AsyncSession
):
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json() == {"reservations_scrubbed": 2, "external_bookings_deleted": 0,
                        "conversations_deleted": 3,
                        "conversations_closed_in_flight": 0,
                        "conversations_still_in_flight": 0,
                        "lookup_failures_deleted": 1,
                        "number_rules_deleted": 0,
                        "company_rules_remaining": 0}

    db_session.expire_all()
    for rid in diners["ada"]:
        res = await db_session.get(Reservation, rid)
        assert res is not None and res.party_size == 2
        assert (res.diner_name, res.diner_phone, res.diner_email, res.notes) == (None,) * 4
    bob = await db_session.get(Reservation, diners["bob"])
    assert bob.diner_name == "Bob" and bob.diner_phone == BOB
    elsewhere = await db_session.get(Reservation, diners["ada_elsewhere"])
    assert elsewhere.diner_phone == ADA

    c = diners["convs"]
    for key in ("booked", "held", "caller"):
        assert await db_session.get(AgentConversation, c[key]) is None
        assert (await db_session.execute(select(AgentEvent).where(
            AgentEvent.conversation_id == c[key]))).first() is None
    for key in ("bob", "elsewhere"):
        assert await db_session.get(AgentConversation, c[key]) is not None
    phones = (await db_session.execute(select(
        ReservationLookupFailure.location_id, ReservationLookupFailure.phone))).all()
    assert sorted(phones) == sorted([(LOCATION_ID, BOB), (OTHER_LOCATION_ID, ADA)])

    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                         json={"phone": ADA}, headers=_auth(manager_token))
    assert r.json() == {"reservations": [], "conversations": [], "number_rules": []}


async def test_other_company_is_404(client, manager_token, seeded_floor, db_session):
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    await db_session.commit()
    theirs = _res(ADA, "Theirs", location_id="loc2xxxx", company_id=COMPANY2_ID)
    db_session.add(theirs)
    await db_session.commit()
    theirs_id = theirs.id
    h = _auth(manager_token)
    r = await client.post(f"{BASE}/locations/loc2xxxx/diner-data",
                         json={"phone": ADA}, headers=h)
    assert r.status_code == 404
    r = await client.post(f"{BASE}/locations/loc2xxxx/diner-data/erase",
                          json={"phone": ADA}, headers=h)
    assert r.status_code == 404
    db_session.expire_all()
    assert (await db_session.get(Reservation, theirs_id)).diner_name == "Theirs"


async def test_restricted_manager_out_of_scope_is_404(
    client, regular_token, diners, db_session: AsyncSession
):
    h = _auth(regular_token)
    r = await client.post(f"{BASE}/locations/{OTHER_LOCATION_ID}/diner-data",
                         json={"phone": ADA}, headers=h)
    assert r.status_code == 404
    r = await client.post(f"{BASE}/locations/{OTHER_LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=h)
    assert r.status_code == 404
    db_session.expire_all()
    assert (await db_session.get(Reservation, diners["ada_elsewhere"])).diner_phone == ADA
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                         json={"phone": ADA}, headers=h)
    assert r.status_code == 200 and len(r.json()["reservations"]) == 2


async def test_routes_404_when_flag_off(client, manager_token, seeded_floor, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    h = _auth(manager_token)
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                         json={"phone": ADA}, headers=h)
    assert r.status_code == 404
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=h)
    assert r.status_code == 404


async def test_conversation_mid_turn_is_closed_not_deleted(
    client, manager_token, diners, db_session: AsyncSession
):
    c = diners["convs"]
    conv = await db_session.get(AgentConversation, c["caller"])
    conv.turn_started_at = datetime.now(timezone.utc)
    conv.last_reply = {"say": "Your table for two, Ada, is at seven."}
    stale = await db_session.get(AgentConversation, c["held"])
    stale.turn_started_at = datetime.now(timezone.utc) - timedelta(
        seconds=recording.TURN_STALE_SECONDS + 60)
    await db_session.commit()

    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["conversations_deleted"] == 2  # booked + the stale-marked held one
    assert body["conversations_closed_in_flight"] == 1
    db_session.expire_all()
    kept = await db_session.get(AgentConversation, c["caller"])
    assert kept.status == "closed" and kept.ended_at is not None
    assert kept.last_reply is None  # the voice reply replay held the diner's details
    assert await db_session.get(AgentConversation, c["held"]) is None

    # Once the turn has ended, erasing again removes it.
    kept.turn_started_at = None
    await db_session.commit()
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=_auth(manager_token))
    assert r.json()["conversations_deleted"] == 1
    assert r.json()["conversations_closed_in_flight"] == 0


async def test_concurrent_hold_waits_for_the_erase(diners):
    """The erase holds the location lock until it commits, so a hold for the
    same diner cannot land between its snapshot and its scrub."""
    now = datetime.now(timezone.utc)
    ny = ZoneInfo("America/New_York")
    start = datetime.combine(now.astimezone(ny).date() + timedelta(days=9),
                             time(19, 0), tzinfo=ny)
    diner = booking.DinerContact(name="Ada", phone=ADA)
    factory = get_test_session_factory()
    async with factory() as eraser:
        await erase_diner_data(eraser, COMPANY_ID, LOCATION_ID, ADA)
        # Not committed: the location lock is still held.
        async with factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '1s'"))
            with pytest.raises(DBAPIError, match="lock"):
                await booking.hold(other, COMPANY_ID, LOCATION_ID, 2, start,
                                   source=SOURCE_WEB_AGENT, now=now, diner=diner)
            await other.rollback()
        await eraser.commit()
    async with factory() as other:
        await other.execute(text("SET LOCAL lock_timeout = '1s'"))
        held = await booking.hold(other, COMPANY_ID, LOCATION_ID, 2, start,
                                  source=SOURCE_WEB_AGENT, now=now, diner=diner)
        await other.commit()
        assert held.diner_phone == ADA


async def test_erase_at_archived_location_takes_the_lock(
    diners, db_session: AsyncSession
):
    loc = await db_session.get(Location, LOCATION_ID)
    loc.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    factory = get_test_session_factory()
    async with factory() as eraser:
        counts = await erase_diner_data(eraser, COMPANY_ID, LOCATION_ID, ADA)
        assert counts["reservations_scrubbed"] == 2
        async with factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '1s'"))
            with pytest.raises(DBAPIError, match="lock"):
                await booking.lock_location_including_archived(
                    other, COMPANY_ID, LOCATION_ID)
            await other.rollback()
        await eraser.commit()


async def test_a_conversation_linked_only_by_its_reservation_is_found_and_erased(
    client, manager_token, diners, db_session: AsyncSession
):
    """A hold that was declined, expired or replaced is no longer on the
    conversation (held_reservation_id is cleared), but the reservation still
    names the conversation it came from."""
    conv = await _conv(db_session, text="Ada's declined request")
    conv_id = conv.id
    declined = _res(ADA, "Ada", "declined")
    declined.conversation_id = conv_id
    db_session.add(declined)
    await db_session.commit()
    h = _auth(manager_token)
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                          json={"phone": ADA}, headers=h)
    assert conv_id in {x["id"] for x in r.json()["conversations"]}
    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": ADA}, headers=h)
    assert r.json()["conversations_deleted"] == 4
    db_session.expire_all()
    assert await db_session.get(AgentConversation, conv_id) is None


async def test_erase_skips_a_conversation_a_running_turn_has_locked(
    diners, db_session: AsyncSession
):
    """A running turn locks its conversation row (record_event) and may then
    wait for the location lock the erase holds. The erase must skip that row
    rather than wait on it, or the two deadlock."""
    c = diners["convs"]
    conv = await db_session.get(AgentConversation, c["caller"])
    conv.turn_started_at = datetime.now(timezone.utc)
    await db_session.commit()
    factory = get_test_session_factory()
    async with factory() as turn:
        await turn.execute(
            select(AgentConversation).where(AgentConversation.id == c["caller"])
            .with_for_update())
        async with factory() as eraser:
            await eraser.execute(text("SET LOCAL lock_timeout = '2s'"))
            counts = await erase_diner_data(eraser, COMPANY_ID, LOCATION_ID, ADA)
            await eraser.commit()
        await turn.rollback()
    assert counts["conversations_deleted"] == 2
    assert counts["conversations_closed_in_flight"] == 0
    assert counts["conversations_still_in_flight"] == 1
    db_session.expire_all()
    kept = await db_session.get(AgentConversation, c["caller"])
    assert kept is not None and kept.status == "open"
