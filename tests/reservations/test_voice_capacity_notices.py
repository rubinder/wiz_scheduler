"""Voice capacity follow-ups (decision 104): capacity emails deduplicated in
Postgres across processes, and AWS quota requests that stay pending forever."""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.reservations import voice_capacity
from backend.reservations.voice_capacity_models import VoiceCapacityNotice, VoiceQuotaRequest
from backend.services.voice_quotas import QuotaRequestAlreadyOpen
from tests.conftest import get_test_session_factory
from tests.reservations.test_voice_capacity import NOW, FakeQuotaClient, _voice_company

pytestmark = [pytest.mark.integration]


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str]]:
    """What reached the mail edge (after any dedupe)."""
    out: list[tuple[str, str]] = []
    def operator(subject: str, body: str) -> bool:
        out.append((subject, body))
        return True

    monkeypatch.setattr(voice_capacity, "_mail_operator", operator)

    async def admins(db, company_id, subject, body):
        out.append((subject, body))

    monkeypatch.setattr(voice_capacity, "_mail_admins", admins)
    return out


@pytest.fixture
def provisioning(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)


# -- 3. one email per key and UTC day, across processes -------------------------------

@pytest.mark.asyncio
async def test_two_processes_racing_on_one_key_send_once(db_session, sent):
    factory = get_test_session_factory()
    async with factory() as a, factory() as b:
        await asyncio.gather(*(
            voice_capacity._mail_operator_once(s, "quota_step_failed", "platform",
                                               "Same", "<p>x</p>", NOW)
            for s in (a, b)))
    assert sent == [("Same", "<p>x</p>")]
    rows = (await db_session.execute(select(VoiceCapacityNotice))).scalars().all()
    assert [(r.kind, r.subject_id, r.day) for r in rows] == [
        ("quota_step_failed", "platform", NOW.date())]


@pytest.mark.asyncio
async def test_the_next_utc_day_sends_again_and_other_keys_send(db_session, sent):
    once = voice_capacity._mail_operator_once
    await once(db_session, "quota_denied", "req00001", "Denied", "<p>a</p>", NOW)
    await once(db_session, "quota_denied", "req00001", "Denied", "<p>a</p>", NOW)
    await once(db_session, "quota_denied", "req00002", "Denied", "<p>b</p>", NOW)
    await once(db_session, "quota_denied", "req00001", "Denied", "<p>a</p>",
               NOW + timedelta(days=1))
    assert [b for _, b in sent] == ["<p>a</p>", "<p>b</p>", "<p>a</p>"]


@pytest.mark.asyncio
async def test_admin_mail_is_deduplicated_too(db_session, seed_location, sent):
    once = voice_capacity._mail_admins_once
    for _ in range(2):
        await once(db_session, "admin_down_shift", "co000001", "co000001",
                   "Adjusted", "<p>x</p>", NOW)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_failed_send_frees_the_key_so_a_later_run_sends(db_session, monkeypatch):
    """One-time notices (a denial, a stale request) would otherwise be lost
    for good when the mail provider is down."""
    results = [False, True]
    attempts: list[str] = []

    def flaky(subject: str, body: str) -> bool:
        attempts.append(subject)
        return results.pop(0) if results else True

    monkeypatch.setattr(voice_capacity, "_mail_operator", flaky)
    once = voice_capacity._mail_operator_once
    await once(db_session, "quota_denied", "req00009", "Denied", "<p>x</p>", NOW)
    assert (await db_session.execute(select(VoiceCapacityNotice))).first() is None
    await once(db_session, "quota_denied", "req00009", "Denied", "<p>x</p>", NOW)
    await once(db_session, "quota_denied", "req00009", "Denied", "<p>x</p>", NOW)
    assert attempts == ["Denied", "Denied"]  # failed, sent, then deduplicated


@pytest.mark.asyncio
async def test_the_operator_send_reports_whether_it_went(monkeypatch):
    import backend.services.operator_alerts as alerts

    monkeypatch.setattr(alerts, "_send_operator_email", lambda s, b, label: False)
    assert voice_capacity._mail_operator("S", "<p>x</p>") is False
    monkeypatch.setattr(alerts, "_send_operator_email", lambda s, b, label: True)
    assert voice_capacity._mail_operator("S", "<p>x</p>") is True


@pytest.mark.asyncio
async def test_a_failing_send_never_raises(db_session, monkeypatch):
    def boom(subject, body):
        raise RuntimeError("resend down")

    monkeypatch.setattr(voice_capacity, "_mail_operator", boom)
    await voice_capacity._mail_operator_once(db_session, "quota_step_failed", "platform",
                                             "S", "<p>x</p>", NOW)


@pytest.mark.asyncio
async def test_a_repeated_quota_failure_mails_once_a_day(db_session, seed_location,
                                                         provisioning, sent):
    await _voice_company(db_session, "cootha01", "ogotha01", "lcotha01")
    for _ in range(2):
        await voice_capacity.ensure_quota(db_session, NOW, FakeQuotaClient(fail=True))
    assert len(sent) == 1
    await voice_capacity.ensure_quota(db_session, NOW + timedelta(days=1),
                                      FakeQuotaClient(fail=True))
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_the_daily_run_prunes_old_notices(db_session, seed_location, provisioning,
                                                sent):
    today = NOW.date()
    for age in (31, 29):
        db_session.add(VoiceCapacityNotice(kind="k", subject_id=f"s{age}",
                                           day=today - timedelta(days=age)))
    await db_session.commit()
    await voice_capacity.run_daily(db_session, NOW, FakeQuotaClient(value=1000))
    left = (await db_session.execute(select(VoiceCapacityNotice.subject_id)
                                     .where(VoiceCapacityNotice.kind == "k"))).scalars().all()
    assert left == ["s29"]


# -- 4. an AWS request left pending ------------------------------------------------------

async def _pending_row(db, age_days: int) -> VoiceQuotaRequest:
    row = VoiceQuotaRequest(desired=20, status="pending", aws_request_id="aws-req-9",
                            reason="nightly check", created_at=NOW - timedelta(days=age_days),
                            updated_at=NOW - timedelta(days=age_days))
    db.add(row)
    await db.commit()
    return row


async def _status(db, row_id: str) -> str:
    return (await db.execute(select(VoiceQuotaRequest.status)
                             .where(VoiceQuotaRequest.id == row_id))).scalar_one()


@pytest.mark.asyncio
async def test_a_young_pending_request_is_still_polled(db_session, seed_location,
                                                       provisioning, sent):
    await _voice_company(db_session, "cootha02", "ogotha02", "lcotha02")
    row = await _pending_row(db_session, settings.VOICE_QUOTA_REQUEST_STALE_DAYS - 1)
    client = FakeQuotaClient(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "pending"
    assert client.polled == ["aws-req-9"] and client.requests == []
    assert await _status(db_session, row.id) == "pending"
    assert sent == []


@pytest.mark.asyncio
async def test_an_old_pending_request_goes_stale_mails_once_then_refiles(
        db_session, seed_location, provisioning, sent):
    await _voice_company(db_session, "cootha03", "ogotha03", "lcotha03")
    row = await _pending_row(db_session, settings.VOICE_QUOTA_REQUEST_STALE_DAYS + 1)
    client = FakeQuotaClient(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "stale"
    assert client.polled == ["aws-req-9"] and client.requests == []
    assert await _status(db_session, row.id) == "stale"
    assert len(sent) == 1
    subject, body = sent[0]
    assert "aws-req-9" in body and "20" in body and "Service Quotas console" in body
    # The next run files a new request for the target.
    out = await voice_capacity.ensure_quota(db_session, NOW + timedelta(hours=1), client)
    assert out["action"] == "requested" and client.requests == [20]
    assert len([s for s, _ in sent if "stale" in s.lower()]) == 1


@pytest.mark.asyncio
async def test_aws_already_holding_an_open_request_is_pending_upstream(
        db_session, seed_location, provisioning, sent):
    await _voice_company(db_session, "cootha04", "ogotha04", "lcotha04")

    class AlreadyOpen(FakeQuotaClient):
        async def request_increase(self, quota, desired):
            self.requests.append(desired)
            raise QuotaRequestAlreadyOpen()

    client = AlreadyOpen(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "pending_upstream" and client.requests == [20]
    row = (await db_session.execute(select(VoiceQuotaRequest))).scalar_one()
    assert (row.status, row.aws_request_id) == ("pending_upstream", None)
    assert sent == []  # not a failure: nothing to tell the operator yet
    # It is not filed again the next night: the row holds it open.
    out = await voice_capacity.ensure_quota(db_session, NOW + timedelta(days=1), client)
    assert out["action"] == "pending" and client.requests == [20]

    # After 7 days it stops blocking a new filing, and the operator is told
    # the request is still open at AWS, not that it "could no longer complete".
    await voice_capacity.ensure_quota(db_session, NOW + timedelta(days=8), client)
    assert client.requests == [20, 20]
    assert len(sent) == 1
    subject, body = sent[0]
    assert "still open at AWS" in body and "Service Quotas console" in body
    assert "could no longer complete" not in body
    assert "closed" not in subject.lower()


def test_the_stale_threshold_is_two_weeks():
    assert settings.VOICE_QUOTA_REQUEST_STALE_DAYS == 14


def test_notice_keys_hold_no_diner_or_phone_field():
    cols = set(VoiceCapacityNotice.__table__.columns.keys())
    assert cols == {"kind", "subject_id", "day", "created_at"}


def test_notice_day_is_the_utc_day():
    assert voice_capacity._notice_day(NOW) == date(2026, 11, 22)
