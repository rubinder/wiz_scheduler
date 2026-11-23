"""The call capacity card's API (decision 103): admins only, behind the
reservations flag."""

from datetime import date

import pytest

from backend.config import settings
from backend.reservations import voice_capacity
from backend.reservations.voice_capacity_models import VoiceCapacityRequest
from backend.reservations.voice_number_models import VoiceNumber
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/reservations/voice-capacity"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


@pytest.fixture
def mail(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(voice_capacity, "_mail_operator",
                        lambda subject, body: sent.append((subject, body)))
    return sent


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _request(**over) -> dict:
    return {"location_ids": [LOCATION_ID], "expected_peak": 40,
            "needed_by": "2026-12-15", "note": "Holiday rush", **over}


async def test_summary_for_an_admin(client, db_session, seed_location, manager_token):
    await subscribe(db_session)
    db_session.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                               location_id=LOCATION_ID, e164="+12125550199",
                               status="active", connect_phone_number_id="pn-1"))
    await db_session.commit()
    r = await client.get(URL, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allocation"] == settings.VOICE_DEFAULT_COMPANY_ALLOCATION
    assert body["voice_locations"] == [{"location_id": LOCATION_ID,
                                        "name": "Downtown Store"}]
    assert body["busiest"] is None and body["pending_request"] is None


async def test_regular_manager_is_refused(client, seed_location, regular_token):
    assert (await client.get(URL, headers=_auth(regular_token))).status_code == 403
    r = await client.post(f"{URL}/requests", json=_request(), headers=_auth(regular_token))
    assert r.status_code == 403
    r = await client.put(f"{URL}/transfer-number",
                         json={"default_staff_transfer_number": "+12125550122"},
                         headers=_auth(regular_token))
    assert r.status_code == 403


async def test_flag_off_is_404(client, seed_location, manager_token, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    assert (await client.get(URL, headers=_auth(manager_token))).status_code == 404


async def test_request_is_recorded_and_mailed(client, db_session, seed_location,
                                              manager_token, mail):
    r = await client.post(f"{URL}/requests", json=_request(), headers=_auth(manager_token))
    assert r.status_code == 201, r.text
    assert r.json()["pending_request"]["expected_peak"] == 40
    row = (await db_session.execute(
        VoiceCapacityRequest.__table__.select())).one()
    assert (row.expected_peak, row.needed_by, row.note) == (40, date(2026, 12, 15),
                                                            "Holiday rush")
    assert len(mail) == 1
    # A second while the first waits: 409.
    r = await client.post(f"{URL}/requests", json=_request(), headers=_auth(manager_token))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "capacity_request_pending"


@pytest.mark.parametrize("over", [
    {"expected_peak": 0}, {"expected_peak": 501}, {"note": "x" * 1001},
    {"location_ids": []},
])
async def test_request_validation(client, seed_location, manager_token, mail, over):
    r = await client.post(f"{URL}/requests", json=_request(**over),
                          headers=_auth(manager_token))
    assert r.status_code == 422
    assert mail == []


async def test_request_refuses_a_location_of_another_company(client, seed_location,
                                                             manager_token, mail):
    r = await client.post(f"{URL}/requests", json=_request(location_ids=["zzzzzzzz"]),
                          headers=_auth(manager_token))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_location"


async def test_request_may_cover_several_locations(client, seed_location, other_location,
                                                   manager_token, mail):
    r = await client.post(f"{URL}/requests",
                          json=_request(location_ids=[LOCATION_ID, OTHER_LOCATION_ID]),
                          headers=_auth(manager_token))
    assert r.status_code == 201, r.text


async def test_default_transfer_number_is_validated_and_saved(client, seed_location,
                                                              manager_token):
    put = f"{URL}/transfer-number"
    r = await client.put(put, json={"default_staff_transfer_number": "+12125550122"},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["default_staff_transfer_number"] == "+12125550122"
    # Not E.164: 422 from the schema; premium rate: 422 from the transfer rules.
    r = await client.put(put, json={"default_staff_transfer_number": "2125550122"},
                         headers=_auth(manager_token))
    assert r.status_code == 422
    r = await client.put(put, json={"default_staff_transfer_number": "+19005550122"},
                         headers=_auth(manager_token))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "transfer_number_not_allowed"
    # Cleared.
    r = await client.put(put, json={"default_staff_transfer_number": None},
                         headers=_auth(manager_token))
    assert r.status_code == 200 and r.json()["default_staff_transfer_number"] is None
