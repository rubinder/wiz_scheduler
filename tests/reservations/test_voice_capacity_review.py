"""Review fixes for voice call capacity (decision 103): voice-off gating,
stuck and denied quota requests, atomic admission, and the smaller rules."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from backend.config import settings
from backend.main import app
from backend.reservations import voice_capacity, voice_numbers
from backend.reservations.agent.gate import get_agent_core
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_agent_rules, get_rules, save_rules
from backend.reservations.voice_capacity_models import (
    VoiceCapacityDaily, VoiceCompanySettings, VoiceQuotaRequest,
)
from backend.reservations.voice_number_models import VoiceNumber
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe
from tests.reservations.test_voice_capacity import (
    D0, DAY, NOW, FakeQuotaClient, _call, _t, _voice_company,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CAP = "/api/v1/reservations/voice-capacity"


@pytest.fixture
def mail(monkeypatch):
    sent: dict[str, list] = {"operator": [], "admins": []}
    monkeypatch.setattr(voice_capacity, "_mail_operator",
                        lambda subject, body: sent["operator"].append((subject, body)))

    async def admins(db, company_id, subject, body):
        sent["admins"].append((company_id, subject, body))

    monkeypatch.setattr(voice_capacity, "_mail_admins", admins)
    return sent


@pytest.fixture
def voice_on(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


@pytest.fixture
def voice_off(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", False)
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _number(db, location_id: str = LOCATION_ID, e164: str = "+12125550199") -> None:
    db.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                       location_id=location_id, e164=e164, status="active",
                       connect_phone_number_id=f"pn-{location_id}"))
    await db.commit()


# -- 1. voice off ---------------------------------------------------------------

async def test_config_reports_voice_enabled(client, seed_location, manager_token,
                                            monkeypatch):
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", False)
    r = await client.get("/api/v1/reservations/config", headers=_auth(manager_token))
    assert r.json()["voice_enabled"] is False
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    r = await client.get("/api/v1/reservations/config", headers=_auth(manager_token))
    assert r.json()["voice_enabled"] is True


async def test_capacity_routes_404_while_voice_is_off(client, seed_location, manager_token,
                                                      voice_off):
    h = _auth(manager_token)
    assert (await client.get(CAP, headers=h)).status_code == 404
    body = {"location_ids": [LOCATION_ID], "expected_peak": 5}
    assert (await client.post(f"{CAP}/requests", json=body, headers=h)).status_code == 404
    r = await client.put(f"{CAP}/transfer-number",
                         json={"default_staff_transfer_number": "+12125550122"}, headers=h)
    assert r.status_code == 404


async def test_requested_locations_need_a_phone_line(db_session, seed_location,
                                                     other_location, seed_manager,
                                                     voice_on, mail):
    await subscribe(db_session)
    await _number(db_session)
    with pytest.raises(voice_capacity.UnknownLocation):
        await voice_capacity.request_capacity(
            db_session, COMPANY_ID, seed_manager.id, [LOCATION_ID, OTHER_LOCATION_ID],
            20, None, "", NOW)
    req = await voice_capacity.request_capacity(
        db_session, COMPANY_ID, seed_manager.id, [LOCATION_ID], 20, None, "", NOW)
    assert req.location_ids == [LOCATION_ID]


async def test_company_default_number_is_voice_only(db_session, seed_location, monkeypatch):
    db_session.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=10,
                                        default_staff_transfer_number="+12125550122"))
    await db_session.commit()
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", False)
    rules, _ = await get_agent_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert rules.staff_transfer_number is None
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    rules, _ = await get_agent_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert rules.staff_transfer_number == "+12125550122"


# -- 2. stuck open rows -------------------------------------------------------------

async def test_manual_row_below_the_target_is_superseded(db_session, seed_location,
                                                         voice_on, mail):
    await _voice_company(db_session, "coreva01", "ogreva01", "lcreva01")
    db_session.add(VoiceCompanySettings(company_id="coreva01", voice_call_allocation=300))
    db_session.add(VoiceQuotaRequest(desired=250, status="manual", reason="old"))
    await db_session.commit()
    out = await voice_capacity.ensure_quota(db_session, NOW, FakeQuotaClient(value=10))
    rows = {r.desired: r.status for r in (await db_session.execute(
        select(VoiceQuotaRequest).execution_options(populate_existing=True))).scalars()}
    assert rows == {250: "superseded", 300: "manual"}
    assert out["action"] == "manual"


async def test_open_row_older_than_seven_days_is_failed_and_mailed(db_session, seed_location,
                                                                   voice_on, mail):
    await _voice_company(db_session, "coreva02", "ogreva02", "lcreva02")
    db_session.add(VoiceQuotaRequest(desired=20, status="pending", aws_request_id=None,
                                     reason="old", created_at=NOW - timedelta(days=8)))
    await db_session.commit()
    client = FakeQuotaClient(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    statuses = sorted(r.status for r in (await db_session.execute(
        select(VoiceQuotaRequest).execution_options(populate_existing=True))).scalars())
    assert statuses == ["failed", "pending"]
    assert out["action"] == "requested" and client.requests == [20]
    assert any("closed" in s.lower() for s, _ in mail["operator"])


async def test_pending_row_aws_no_longer_knows_is_failed(db_session, seed_location,
                                                         voice_on, mail):
    await _voice_company(db_session, "coreva03", "ogreva03", "lcreva03")
    db_session.add(VoiceQuotaRequest(desired=20, status="pending", aws_request_id="gone",
                                     reason="old"))
    await db_session.commit()
    client = FakeQuotaClient(value=10, status="missing")
    await voice_capacity.ensure_quota(db_session, NOW, client)
    row = (await db_session.execute(select(VoiceQuotaRequest)
                                    .where(VoiceQuotaRequest.aws_request_id == "gone")
                                    .execution_options(populate_existing=True))).scalar_one()
    assert row.status == "failed"


# -- 3. denied ------------------------------------------------------------------------

async def test_denied_request_is_mailed_and_not_refiled(db_session, seed_location,
                                                        voice_on, mail):
    await _voice_company(db_session, "coreva04", "ogreva04", "lcreva04")
    client = FakeQuotaClient(value=10)
    await voice_capacity.ensure_quota(db_session, NOW, client)
    client.status = "denied"
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "denied_recently" and client.requests == [20]
    subject, body = mail["operator"][-1]
    assert "denied" in subject.lower() and settings.VOICE_QUOTA_NAME in body
    # The next night: not filed again for the same value.
    out = await voice_capacity.ensure_quota(db_session, NOW + timedelta(days=1), client)
    assert client.requests == [20]
    n = len(mail["operator"])
    # A week later it may be filed again.
    out = await voice_capacity.ensure_quota(db_session, NOW + timedelta(days=8), client)
    assert out["action"] == "requested" and client.requests == [20, 20]
    assert len(mail["operator"]) == n + 1


# -- 4. atomic admission -----------------------------------------------------------------

DIALED = "+12125550199"
SECRET = "s3cret-bridge"


@pytest_asyncio.fixture
async def line(db_session, seeded_floor, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", SECRET)
    await subscribe(db_session)
    await _number(db_session, e164=DIALED)


def _first_turn(contact: str) -> dict:
    return {"contact_id": contact, "dialed": DIALED, "caller": None, "seq": 0,
            "text": None, "action": None, "timings": {}}


async def test_two_racing_first_turns_at_allocation_minus_one_admit_one(
        isolated_client, db_session, line):
    from backend.reservations.agent.core import AgentCore
    from tests.reservations.agent.fakes import ScriptedLlm

    app.dependency_overrides[get_agent_core] = lambda: AgentCore(ScriptedLlm())
    db_session.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=2))
    db_session.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                     channel="voice", status="open", external_ref="busy",
                                     started_at=datetime.now(timezone.utc)))
    await db_session.commit()
    headers = {"X-Voice-Bridge-Secret": SECRET}
    a, b = await asyncio.gather(
        isolated_client.post("/api/v1/voice/turn", json=_first_turn("race-a"), headers=headers),
        isolated_client.post("/api/v1/voice/turn", json=_first_turn("race-b"), headers=headers))
    reasons = sorted(str(r.json()["reason"]) for r in (a, b))
    assert reasons == ["None", "capacity"]


async def test_allocation_check_failure_answers_the_call(client, db_session, line,
                                                         monkeypatch):
    from backend.reservations.agent.core import AgentCore
    from tests.reservations.agent.fakes import ScriptedLlm

    app.dependency_overrides[get_agent_core] = lambda: AgentCore(ScriptedLlm())

    async def boom(*a, **kw):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(voice_capacity, "at_allocation", boom)
    r = await client.post("/api/v1/voice/turn", json=_first_turn("c1"),
                          headers={"X-Voice-Bridge-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["reason"] is None and r.json()["end_call"] is False


# -- 5. webhook skip, mail dedupe ------------------------------------------------------------

async def test_webhook_mode_skips_the_quota_step(db_session, seed_location, monkeypatch):
    from tests.reservations.test_voice_capacity import connect  # noqa: F401
    from backend.services import connect_numbers
    from tests.reservations.test_voice_numbers import FakeConnect

    fake = FakeConnect()
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONNECT_INSTANCE_ARN", "arn:aws:connect:x:1:instance/i")
    monkeypatch.setattr(settings, "CONNECT_CONTACT_FLOW_ID", "flow-1")
    monkeypatch.setattr(connect_numbers, "_make_client", lambda: fake)
    connect_numbers.reset_client()
    calls = []

    async def ensure(*a, **kw):
        calls.append(1)
        return {}

    monkeypatch.setattr(voice_capacity, "ensure_quota", ensure)
    try:
        await subscribe(db_session)
        await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW, claim=False)
        assert calls == []
    finally:
        connect_numbers.reset_client()


async def test_the_same_operator_mail_goes_once_a_day(db_session, monkeypatch):
    """Keyed in Postgres since decision 104 (test_voice_capacity_notices has
    the cross-process race); the subject line is the operator's prefix."""
    import backend.services.operator_alerts as alerts

    sent = []
    monkeypatch.setattr(alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append(subject) or True)
    once = voice_capacity._mail_operator_once
    await once(db_session, "quota_manual", "300", "Same", "<p>x</p>", NOW)
    await once(db_session, "quota_manual", "300", "Same", "<p>x</p>", NOW)
    await once(db_session, "quota_not_found", "platform", "Other", "<p>x</p>", NOW)
    assert sent == ["[WizScheduler] Same", "[WizScheduler] Other"]


# -- 6. --allocation 0 ---------------------------------------------------------------------------

async def test_allocation_zero_is_refused_not_replaced(db_session, seed_location,
                                                       seed_manager, voice_on, mail):
    await subscribe(db_session)
    await _number(db_session)
    req = await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                                [LOCATION_ID], 40, None, "", NOW)
    with pytest.raises(voice_capacity.CapacityRequestError):
        await voice_capacity.approve_request(db_session, req.id, 0, NOW, FakeQuotaClient())


# -- 8. the daily script survives a DB error ------------------------------------------------------

async def test_daily_script_mails_when_the_capacity_step_crashes(monkeypatch):
    from contextlib import asynccontextmanager

    import backend.database
    import backend.services.operator_alerts
    from backend.scripts import run_agent_overage_reconcile as script
    from backend.services import billing_reservations as br

    @asynccontextmanager
    async def session():
        yield object()

    async def ok(db):
        return {"errors": []}

    async def crash(db):
        raise RuntimeError("connection reset")

    sent = []
    monkeypatch.setattr(backend.database, "async_session_factory", session)
    monkeypatch.setattr(br, "reconcile_all_agent_subscriptions", ok)
    monkeypatch.setattr(voice_numbers, "run_daily", ok)
    monkeypatch.setattr(voice_capacity, "run_daily", crash)
    monkeypatch.setattr(backend.services.operator_alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append((subject, body)) or True)
    await script.main()
    assert len(sent) == 1 and "connection reset" in sent[0][1]


async def test_retention_failure_is_an_error_not_a_crash(db_session, voice_on, mail,
                                                         monkeypatch):
    real = db_session.execute
    calls = {"n": 0}

    async def flaky(stmt, *a, **kw):
        if "DELETE FROM voice_capacity_daily WHERE voice_capacity_daily.day <" in str(stmt):
            raise RuntimeError("lock timeout")
        return await real(stmt, *a, **kw)

    monkeypatch.setattr(db_session, "execute", flaky)
    out = await voice_capacity.run_daily(db_session, NOW, FakeQuotaClient(value=100))
    assert any(e["step"] == "retention" for e in out["errors"])
    assert "quota" in out


# -- 9. the peak query skips old closed calls ------------------------------------------------------

async def test_old_closed_calls_are_not_read(db_session, seed_location, mail):
    # Started three days before and "ended" inside the day: data that cannot
    # be a real call; the query skips closed calls started before day - 1.
    await _call(db_session, D0 - timedelta(days=3), _t(10, 2))
    await _call(db_session, _t(10), _t(10, 5))
    await voice_capacity.record_peaks(db_session, DAY, None)
    row = (await db_session.execute(select(VoiceCapacityDaily))).scalar_one()
    assert row.peak_concurrent == 1


# -- 13. one pending capacity request per company ----------------------------------------------------

async def test_one_pending_request_per_company_is_a_db_constraint(db_session, seed_location):
    from sqlalchemy.exc import IntegrityError

    from backend.reservations.voice_capacity_models import VoiceCapacityRequest

    for _ in range(2):
        db_session.add(VoiceCapacityRequest(company_id=COMPANY_ID, location_ids=[LOCATION_ID],
                                            expected_peak=5, note="", status="pending"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_racing_pending_request_is_pending_not_a_500(db_session, seed_location,
                                                              seed_manager, voice_on, mail,
                                                              monkeypatch):
    """A competing request committed between the pending check and the
    insert: the unique index refuses it, and that is CapacityRequestPending."""
    from backend.reservations.voice_capacity_models import VoiceCapacityRequest
    from tests.conftest import get_test_session_factory

    await subscribe(db_session)
    await _number(db_session)
    real_pending = voice_capacity._pending

    async def pending_then_race(db, company_id):
        found = await real_pending(db, company_id)
        async with get_test_session_factory()() as other:
            other.add(VoiceCapacityRequest(company_id=company_id, location_ids=[LOCATION_ID],
                                           expected_peak=5, note="", status="pending"))
            await other.commit()
        return found

    monkeypatch.setattr(voice_capacity, "_pending", pending_then_race)
    with pytest.raises(voice_capacity.CapacityRequestPending):
        await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                              [LOCATION_ID], 20, None, "", NOW)
    # The session is still usable.
    n = len((await db_session.execute(select(VoiceCapacityRequest))).scalars().all())
    assert n == 1
    assert mail["operator"] == []
