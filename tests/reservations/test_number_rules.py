"""Number lists for managers (#259 step 7): CRUD with LocationScope, mark as
spam, the screening counts, and diner data export/erase of entries."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Location, Region
from backend.reservations.agent import recording
from backend.reservations.screening_models import NumberRule, ScreenedCall
from tests.conftest import (
    COMPANY2_ID, COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BASE = "/api/v1/reservations"
NUM = "+12125550142"
OTHER = "+12125550143"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _utc_today():
    return datetime.now(timezone.utc).date()


async def _add(db: AsyncSession, *, location_id: str | None = LOCATION_ID,
               e164: str = NUM, action: str = "block",
               expires_at: datetime | None = None) -> str:
    rule = NumberRule(company_id=COMPANY_ID, location_id=location_id, e164=e164,
                      action=action, expires_at=expires_at)
    db.add(rule)
    await db.commit()
    return rule.id


async def _post(client, token: str, **body):
    payload = {"e164": NUM, "action": "block", "location_id": LOCATION_ID,
               "expires_on": None, "note": None} | body
    return await client.post(f"{BASE}/number-rules", json=payload, headers=_auth(token))


# -- create, read, edit, delete -----------------------------------------------

async def test_admin_creates_location_rule_with_local_end_of_day_expiry(
    client, manager_token, seed_manager, seed_location, db_session
):
    day = _utc_today() + timedelta(days=10)
    r = await _post(client, manager_token, expires_on=day.isoformat(), note="Robocaller")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["e164"] == NUM and body["action"] == "block"
    assert body["location_id"] == LOCATION_ID
    assert body["expires_on"] == day.isoformat()
    assert body["note"] == "Robocaller" and body["active"] is True
    assert body["created_by_name"] == "Test Manager"
    rule = await db_session.get(NumberRule, body["id"])
    local = rule.expires_at.astimezone(ZoneInfo("America/New_York"))
    assert (local.date(), local.hour, local.minute, local.second) == (day, 23, 59, 59)
    assert local.microsecond == 999000


async def test_company_wide_expiry_uses_first_active_location_zone(
    client, manager_token, seed_manager, seed_location, db_session
):
    db_session.add(Location(id="aaaazone", company_id=COMPANY_ID,
                            region_id=seed_location.region_id, name="Aardvark Cafe",
                            timezone="Asia/Tokyo"))
    await db_session.commit()
    day = _utc_today() + timedelta(days=10)
    r = await _post(client, manager_token, location_id=None, expires_on=day.isoformat())
    assert r.status_code == 201, r.text
    assert r.json()["expires_on"] == day.isoformat()
    rule = await db_session.get(NumberRule, r.json()["id"])
    local = rule.expires_at.astimezone(ZoneInfo("Asia/Tokyo"))
    assert (local.date(), local.hour) == (day, 23)


async def test_invalid_number_and_past_expiry_are_422(
    client, manager_token, seed_location
):
    for bad in ("212-555-0142", "+12345"):  # booking.E164: at least 7 digits
        r = await _post(client, manager_token, e164=bad)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_phone"
    r = await _post(client, manager_token,
                    expires_on=(_utc_today() - timedelta(days=2)).isoformat())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "expiry_in_past"
    r = await _post(client, manager_token, note="x" * 201)
    assert r.status_code == 422


async def test_duplicate_active_rule_is_409_but_expired_one_is_not(
    client, manager_token, seed_location, db_session
):
    assert (await _post(client, manager_token)).status_code == 201
    r = await _post(client, manager_token, action="allow")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_rule"
    # Company-wide is another scope.
    assert (await _post(client, manager_token, location_id=None)).status_code == 201
    r = await _post(client, manager_token, location_id=None)
    assert r.status_code == 409
    # An expired entry does not count.
    await _add(db_session, e164=OTHER,
               expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (await _post(client, manager_token, e164=OTHER)).status_code == 201


async def test_list_filters_by_location_and_adds_company_wide(
    client, manager_token, regular_token, other_location, db_session
):
    here = await _add(db_session)
    there = await _add(db_session, location_id=OTHER_LOCATION_ID)
    everywhere = await _add(db_session, location_id=None, e164=OTHER, action="allow")
    expired = await _add(db_session, e164=OTHER,
                         expires_at=datetime.now(timezone.utc) - timedelta(days=1))

    r = await client.get(f"{BASE}/number-rules", params={"location_id": LOCATION_ID},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    got = {x["id"]: x for x in r.json()}
    assert set(got) == {here, everywhere, expired}
    assert got[expired]["active"] is False and got[here]["active"] is True
    assert got[expired]["expires_on"] is not None and got[here]["expires_on"] is None

    r = await client.get(f"{BASE}/number-rules", headers=_auth(manager_token))
    assert {x["id"] for x in r.json()} == {here, there, everywhere, expired}

    # A regular manager on LOCATION_ID sees its entries and company-wide ones.
    r = await client.get(f"{BASE}/number-rules", headers=_auth(regular_token))
    assert {x["id"] for x in r.json()} == {here, everywhere, expired}
    r = await client.get(f"{BASE}/number-rules",
                         params={"location_id": OTHER_LOCATION_ID},
                         headers=_auth(regular_token))
    assert r.status_code == 404


async def test_regular_manager_cannot_touch_other_location_or_company_wide(
    client, regular_token, other_location, db_session
):
    h = _auth(regular_token)
    there = await _add(db_session, location_id=OTHER_LOCATION_ID)
    everywhere = await _add(db_session, location_id=None, e164=OTHER)

    r = await _post(client, regular_token, location_id=OTHER_LOCATION_ID)
    assert r.status_code == 404
    r = await _post(client, regular_token, location_id=None)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"

    r = await client.patch(f"{BASE}/number-rules/{there}", json={"note": "x"}, headers=h)
    assert r.status_code == 404
    r = await client.delete(f"{BASE}/number-rules/{there}", headers=h)
    assert r.status_code == 404
    r = await client.patch(f"{BASE}/number-rules/{everywhere}", json={"note": "x"},
                           headers=h)
    assert r.status_code == 403
    r = await client.delete(f"{BASE}/number-rules/{everywhere}", headers=h)
    assert r.status_code == 403
    db_session.expire_all()
    assert await db_session.get(NumberRule, there) is not None
    assert (await db_session.get(NumberRule, everywhere)).note is None

    # Its own location: allowed.
    r = await _post(client, regular_token)
    assert r.status_code == 201, r.text
    mine = r.json()["id"]
    r = await client.patch(f"{BASE}/number-rules/{mine}", json={"action": "allow"},
                           headers=h)
    assert r.status_code == 200 and r.json()["action"] == "allow"
    r = await client.delete(f"{BASE}/number-rules/{mine}", headers=h)
    assert r.status_code == 204


async def test_patch_changes_only_action_expiry_and_note(
    client, manager_token, seed_location, db_session
):
    rid = await _add(db_session)
    h = _auth(manager_token)
    day = _utc_today() + timedelta(days=3)
    r = await client.patch(f"{BASE}/number-rules/{rid}",
                           json={"action": "allow", "expires_on": day.isoformat(),
                                 "note": "Regular"}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["action"], r.json()["expires_on"], r.json()["note"]) == (
        "allow", day.isoformat(), "Regular")
    # Omitted fields stay; null expiry means never.
    r = await client.patch(f"{BASE}/number-rules/{rid}", json={"expires_on": None},
                           headers=h)
    assert r.json()["expires_on"] is None and r.json()["note"] == "Regular"
    for bad in ({"e164": OTHER}, {"location_id": None}):
        r = await client.patch(f"{BASE}/number-rules/{rid}", json=bad, headers=h)
        assert r.status_code == 422


async def test_patch_note_on_an_expired_rule_keeps_its_date(
    client, manager_token, seed_location, db_session
):
    past = datetime.now(timezone.utc) - timedelta(days=3)
    rid = await _add(db_session, expires_at=past)
    h = _auth(manager_token)
    listed = (await client.get(f"{BASE}/number-rules", headers=h)).json()
    expires_on = next(x for x in listed if x["id"] == rid)["expires_on"]
    r = await client.patch(f"{BASE}/number-rules/{rid}", json={"note": "old"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["note"] == "old" and r.json()["active"] is False
    # Sending the unchanged date is not a new expiry either.
    r = await client.patch(f"{BASE}/number-rules/{rid}",
                           json={"expires_on": expires_on, "action": "allow"}, headers=h)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (await db_session.get(NumberRule, rid)).expires_at == past
    # A different past date still is.
    r = await client.patch(f"{BASE}/number-rules/{rid}",
                           json={"expires_on": (_utc_today() - timedelta(days=10)).isoformat()},
                           headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "expiry_in_past"


async def test_patch_null_action_is_422(client, manager_token, seed_location, db_session):
    rid = await _add(db_session)
    r = await client.patch(f"{BASE}/number-rules/{rid}", json={"action": None},
                           headers=_auth(manager_token))
    assert r.status_code == 422


async def test_permanent_rules_have_a_unique_index(seed_location, db_session):
    from sqlalchemy.exc import IntegrityError

    await _add(db_session, location_id=None)
    db_session.add(NumberRule(company_id=COMPANY_ID, location_id=None, e164=NUM,
                              action="allow"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
    # Expiring entries and other scopes are not held by it.
    await _add(db_session, location_id=LOCATION_ID)
    await _add(db_session, location_id=None,
               expires_at=datetime.now(timezone.utc) + timedelta(days=1))


async def test_patch_reviving_an_expired_duplicate_is_409(
    client, manager_token, seed_location, db_session
):
    await _add(db_session)
    old = await _add(db_session, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    r = await client.patch(f"{BASE}/number-rules/{old}", json={"expires_on": None},
                           headers=_auth(manager_token))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_rule"


async def test_delete_is_hard_and_other_company_is_404(
    client, manager_token, seed_location, db_session
):
    rid = await _add(db_session)
    r = await client.delete(f"{BASE}/number-rules/{rid}", headers=_auth(manager_token))
    assert r.status_code == 204
    db_session.expire_all()
    assert await db_session.get(NumberRule, rid) is None

    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Region(id="rgn2xxxx", company_id=COMPANY2_ID, name="R"))
    await db_session.flush()
    db_session.add(Location(id="loc2xxxx", company_id=COMPANY2_ID, region_id="rgn2xxxx",
                            name="Elsewhere", timezone="America/New_York"))
    theirs = NumberRule(company_id=COMPANY2_ID, location_id="loc2xxxx", e164=NUM,
                        action="block")
    db_session.add(theirs)
    await db_session.commit()
    h = _auth(manager_token)
    assert (await client.delete(f"{BASE}/number-rules/{theirs.id}", headers=h)).status_code == 404
    assert (await _post(client, manager_token, location_id="loc2xxxx")).status_code == 404
    r = await client.get(f"{BASE}/number-rules", headers=h)
    assert all(x["id"] != theirs.id for x in r.json())


async def test_routes_404_when_flag_off(client, manager_token, seed_location, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    h = _auth(manager_token)
    assert (await client.get(f"{BASE}/number-rules", headers=h)).status_code == 404
    assert (await client.get(f"{BASE}/screening-stats", headers=h)).status_code == 404


# -- mark as spam -------------------------------------------------------------

async def _conv(db: AsyncSession, channel: str = "voice", caller: str | None = NUM,
                location_id: str = LOCATION_ID):
    conv = await recording.start_conversation(
        db, COMPANY_ID, location_id, channel, caller_number=caller)
    await db.commit()
    return conv


async def test_mark_spam_creates_a_permanent_location_block_once(
    client, manager_token, seed_location, db_session
):
    conv = await _conv(db_session)
    h = _auth(manager_token)
    r = await client.get(f"{BASE}/conversations/{conv.id}", headers=h)
    assert r.json()["spam_markable"] is True and NUM not in r.text

    r = await client.post(f"{BASE}/conversations/{conv.id}/mark-spam", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["e164"], body["action"], body["location_id"], body["expires_at"]) == (
        NUM, "block", LOCATION_ID, None)
    assert body["note"] == f"Marked as spam from conversation {conv.id}"
    assert body["created"] is True

    r = await client.post(f"{BASE}/conversations/{conv.id}/mark-spam", headers=h)
    assert r.status_code == 200 and r.json()["id"] == body["id"]
    assert r.json()["created"] is False
    rows = (await db_session.execute(select(NumberRule))).scalars().all()
    assert len(rows) == 1


async def test_mark_spam_needs_a_voice_caller_number(
    client, manager_token, seed_location, db_session
):
    h = _auth(manager_token)
    ids = [(await _conv(db_session, channel="web", caller=None)).id,
           (await _conv(db_session, caller=None)).id,
           (await _conv(db_session, caller="anonymous")).id]
    for cid in ids:
        r = await client.get(f"{BASE}/conversations/{cid}", headers=h)
        assert r.json()["spam_markable"] is False
        r = await client.post(f"{BASE}/conversations/{cid}/mark-spam", headers=h)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "no_caller_number"
    assert (await db_session.execute(select(NumberRule))).first() is None


@pytest.mark.parametrize("allow_at, scope", [(LOCATION_ID, "here"), (None, "all_locations")])
async def test_mark_spam_under_an_allow_is_409_number_allowed(
    client, manager_token, seed_location, db_session, allow_at, scope
):
    await _add(db_session, action="allow", location_id=allow_at)
    conv = await _conv(db_session)
    r = await client.post(f"{BASE}/conversations/{conv.id}/mark-spam",
                          headers=_auth(manager_token))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "number_allowed"
    assert r.json()["detail"]["scope"] == scope


async def test_location_block_under_an_allow_is_409_number_allowed(
    client, manager_token, other_location, db_session
):
    await _add(db_session, action="allow", location_id=None)
    await _add(db_session, action="allow", location_id=OTHER_LOCATION_ID, e164=OTHER)
    r = await _post(client, manager_token)
    assert r.status_code == 409 and r.json()["detail"] == {
        "code": "number_allowed", "scope": "all_locations",
        "message": r.json()["detail"]["message"]}
    r = await _post(client, manager_token, e164=OTHER, location_id=OTHER_LOCATION_ID)
    assert r.json()["detail"]["code"] == "number_allowed"
    assert r.json()["detail"]["scope"] == "here"
    # An allow elsewhere does not stop a block here, and an allow is never refused.
    assert (await _post(client, manager_token, e164=OTHER)).status_code == 201
    assert (await _post(client, manager_token, action="allow")).status_code == 201


async def test_mark_spam_out_of_scope_is_404(
    client, regular_token, other_location, db_session
):
    conv = await _conv(db_session, location_id=OTHER_LOCATION_ID)
    r = await client.post(f"{BASE}/conversations/{conv.id}/mark-spam",
                          headers=_auth(regular_token))
    assert r.status_code == 404
    assert (await db_session.execute(select(NumberRule))).first() is None


# -- screening stats ----------------------------------------------------------

async def test_screening_stats_last_seven_days_in_scope(
    client, manager_token, regular_token, other_location, db_session
):
    now = datetime.now(timezone.utc)

    def call(decision: str, reason: str, *, loc: str = LOCATION_ID, days: float = 1):
        return ScreenedCall(company_id=COMPANY_ID, location_id=loc, caller_key=None,
                            decision=decision, reason=reason,
                            created_at=now - timedelta(days=days))

    db_session.add_all([
        call("block", "block_list"), call("block", "block_list"),
        call("block", "ftc"), call("answer", "default"), call("answer", "allow_list"),
        call("answer", "withheld"),
        call("block", "ftc", days=8),  # too old
        call("block", "ftc", loc=OTHER_LOCATION_ID),
    ])
    await db_session.commit()
    r = await client.get(f"{BASE}/screening-stats", params={"location_id": LOCATION_ID},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json() == {"days": 7, "blocked_list": 2, "blocked_ftc": 1, "answered": 3}
    r = await client.get(f"{BASE}/screening-stats", headers=_auth(manager_token))
    assert r.json()["blocked_ftc"] == 2
    r = await client.get(f"{BASE}/screening-stats", headers=_auth(regular_token))
    assert r.json()["blocked_ftc"] == 1
    r = await client.get(f"{BASE}/screening-stats",
                         params={"location_id": OTHER_LOCATION_ID},
                         headers=_auth(regular_token))
    assert r.status_code == 404


# -- diner data ---------------------------------------------------------------

async def test_diner_export_and_erase_cover_only_this_location(
    client, manager_token, regular_token, seed_manager, other_location, db_session
):
    here = await _add(db_session)
    there = await _add(db_session, location_id=OTHER_LOCATION_ID, action="allow")
    everywhere = await _add(db_session, location_id=None)
    someone_else = await _add(db_session, e164=OTHER)
    h = _auth(regular_token)  # scoped to LOCATION_ID only

    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data",
                          json={"phone": NUM}, headers=h)
    assert r.status_code == 200, r.text
    got = {x["id"]: x for x in r.json()["number_rules"]}
    assert set(got) == {here, everywhere}
    assert got[everywhere]["location_id"] is None

    r = await client.post(f"{BASE}/locations/{LOCATION_ID}/diner-data/erase",
                          json={"phone": NUM}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["number_rules_deleted"] == 1
    assert r.json()["company_rules_remaining"] == 1
    db_session.expire_all()
    left = set((await db_session.execute(select(NumberRule.id))).scalars().all())
    assert left == {there, everywhere, someone_else}
