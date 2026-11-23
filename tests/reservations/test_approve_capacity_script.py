"""backend/scripts/approve_capacity_request.py: the operator's approval."""

import pytest

from backend.reservations import voice_capacity
from backend.reservations.voice_capacity_models import VoiceCapacityRequest
from backend.scripts import approve_capacity_request as script
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.test_voice_capacity import NOW, FakeQuotaClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(voice_capacity, "_mail_operator", lambda s, b: None)

    async def admins(db, company_id, subject, body):
        return None

    monkeypatch.setattr(voice_capacity, "_mail_admins", admins)


async def _req(db, manager) -> str:
    req = await voice_capacity.request_capacity(db, COMPANY_ID, manager.id, [LOCATION_ID],
                                                45, None, "", NOW)
    return req.id


async def test_approve_with_the_asked_number(db_session, seed_location, seed_manager):
    rid = await _req(db_session, seed_manager)
    out = await script.handle(db_session, [rid], client=FakeQuotaClient())
    assert out["allocation"] == 45
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == 45


async def test_approve_with_another_number(db_session, seed_location, seed_manager):
    rid = await _req(db_session, seed_manager)
    out = await script.handle(db_session, [rid, "--allocation", "30"],
                              client=FakeQuotaClient())
    assert out["allocation"] == 30


async def test_decline(db_session, seed_location, seed_manager):
    rid = await _req(db_session, seed_manager)
    out = await script.handle(db_session, [rid, "--decline"], client=FakeQuotaClient())
    assert out["declined"] is True
    assert (await db_session.get(VoiceCapacityRequest, rid)).status == "declined"


async def test_unknown_request_is_an_error(db_session, seed_location):
    with pytest.raises(voice_capacity.CapacityRequestError):
        await script.handle(db_session, ["nope0000"], client=FakeQuotaClient())
