"""Voice call capacity (decision 103): the quota target and its requests,
daily peaks, the per-company allocation and its down-shift, capacity
requests and their approval."""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.config import settings
from backend.models import Company, Location, OwnershipGroup, Region
from backend.reservations import voice_capacity
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.subscription_models import ReservationSubscription
from backend.reservations.voice_capacity_models import (
    VoiceCapacityDaily, VoiceCapacityRequest, VoiceCompanySettings, VoicePlatformDaily,
    VoiceQuotaRequest,
)
from backend.reservations.voice_number_models import VoiceNumber
from backend.services.voice_quotas import AppliedQuota
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)
DAY = date(2026, 11, 21)
D0 = datetime(2026, 11, 21, tzinfo=timezone.utc)


class FakeQuotaClient:
    def __init__(self, value: int = 10, status: str = "pending", fail: bool = False,
                 found: bool = True, cloudwatch: int | None = None):
        self.value = value
        self.status = status
        self.fail = fail
        self.found = found
        self.cloudwatch = cloudwatch
        self.requests: list[int] = []
        self.polled: list[str] = []

    async def applied_quota(self):
        if self.fail:
            raise RuntimeError("AccessDenied")
        return AppliedQuota("L-CC", self.value) if self.found else None

    async def request_increase(self, quota, desired):
        self.requests.append(desired)
        return f"req-{len(self.requests)}"

    async def request_status(self, quota, request_id):
        self.polled.append(request_id)
        return self.status

    async def peak_concurrent_calls(self, day):
        return self.cloudwatch


@pytest.fixture
def mail(monkeypatch):
    sent: dict[str, list] = {"operator": [], "admins": []}

    def operator(subject: str, body: str) -> None:
        sent["operator"].append((subject, body))

    async def admins(db, company_id: str, subject: str, body: str) -> None:
        sent["admins"].append((company_id, subject, body))

    monkeypatch.setattr(voice_capacity, "_mail_operator", operator)
    monkeypatch.setattr(voice_capacity, "_mail_admins", admins)
    return sent


@pytest.fixture
def provisioning(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)


async def _voice_company(db, company_id: str, og_id: str, location_id: str,
                         status: str = "active", number_status: str = "active") -> None:
    """A company in its own group with an AI host subscription and a number."""
    if await db.get(OwnershipGroup, og_id) is None:
        db.add(OwnershipGroup(id=og_id, name=og_id))
        await db.flush()
    if await db.get(Company, company_id) is None:
        db.add(Company(id=company_id, name=company_id, slug=f"s-{company_id}",
                       ownership_group_id=og_id))
        await db.flush()
        region = Region(id=f"rg{company_id[2:]}", company_id=company_id, name="R")
        db.add(region)
        await db.flush()
        db.add(Location(id=location_id, company_id=company_id, region_id=region.id,
                        name=location_id, timezone="America/New_York"))
        await db.flush()
    db.add(ReservationSubscription(ownership_group_id=og_id, status=status))
    db.add(VoiceNumber(company_id=company_id, ownership_group_id=og_id,
                       location_id=location_id, e164=f"+1212555{company_id[-4:]}",
                       status=number_status, connect_phone_number_id=f"pn-{company_id}"))
    await db.commit()


async def _call(db, start: datetime, end: datetime | None, *, company_id: str = COMPANY_ID,
                location_id: str = LOCATION_ID, last_event: datetime | None = None,
                channel: str = "voice", updated: datetime | None = None) -> AgentConversation:
    conv = AgentConversation(company_id=company_id, location_id=location_id,
                             channel=channel, started_at=start, ended_at=end,
                             status="closed" if end else "open")
    db.add(conv)
    await db.flush()
    if last_event is not None:
        db.add(AgentEvent(company_id=company_id, conversation_id=conv.id, seq=1,
                          type="user_turn", created_at=last_event))
    if updated is not None:
        conv.updated_at = updated
    await db.commit()
    return conv


async def _phone_line(db) -> None:
    """The test location gets a number: capacity requests need one."""
    db.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                       location_id=LOCATION_ID, e164="+12125550199",
                       status="active", connect_phone_number_id="pn-1"))
    await db.commit()


@pytest_asyncio.fixture
async def phone_line(db_session, seed_location):
    await subscribe(db_session)
    await _phone_line(db_session)


def _t(h: int, m: int = 0) -> datetime:
    return D0 + timedelta(hours=h, minutes=m)


# -- peaks ------------------------------------------------------------------------

async def test_day_peak_counts_overlaps_and_touching_calls():
    d1 = D0 + timedelta(days=1)
    calls = [(_t(10), _t(11)), (_t(10, 30), _t(12)), (_t(10, 45), _t(10, 50)),
             (_t(13), _t(13))]
    assert voice_capacity.day_peak(calls, D0, d1) == 3
    # Touching ends count as overlapping (capacity errs high).
    assert voice_capacity.day_peak([(_t(1), _t(2)), (_t(2), _t(3))], D0, d1) == 2
    # Outside the day, or clipped into it.
    assert voice_capacity.day_peak([(D0 - timedelta(hours=2), D0 - timedelta(hours=1))],
                                   D0, d1) == 0
    assert voice_capacity.day_peak([(D0 - timedelta(hours=1), _t(1))], D0, d1) == 1
    assert voice_capacity.day_peak([], D0, d1) == 0


async def test_peaks_per_company_and_platform(db_session, seed_location, mail):
    other_co, other_loc = "coother1", "lcother1"
    await _voice_company(db_session, other_co, "ogother1", other_loc)
    # Ours: two overlapping calls 10:00-10:30, then one at 15:00.
    await _call(db_session, _t(10), _t(10, 30))
    await _call(db_session, _t(10, 10), _t(10, 20))
    await _call(db_session, _t(15), _t(15, 5))
    # A web chat never counts.
    await _call(db_session, _t(10), _t(10, 30), channel="web")
    # Theirs: one at 10:15 (overlaps ours) and two at 15:30.
    await _call(db_session, _t(10, 15), _t(10, 25), company_id=other_co,
                location_id=other_loc)
    await _call(db_session, _t(15, 30), _t(15, 40), company_id=other_co,
                location_id=other_loc)
    await _call(db_session, _t(15, 35), _t(15, 45), company_id=other_co,
                location_id=other_loc)

    out = await voice_capacity.record_peaks(db_session, DAY, FakeQuotaClient(cloudwatch=4))

    rows = {r.company_id: r for r in (await db_session.execute(
        select(VoiceCapacityDaily))).scalars().all()}
    assert (rows[COMPANY_ID].peak_concurrent, rows[COMPANY_ID].calls) == (2, 3)
    assert (rows[other_co].peak_concurrent, rows[other_co].calls) == (2, 3)
    # The platform peak is the overlap across companies (3 at 10:15), not 2 + 2.
    platform = await db_session.get(VoicePlatformDaily, DAY)
    assert (platform.peak_concurrent, platform.cloudwatch_peak, platform.calls) == (3, 4, 6)
    assert out["platform_peak"] == 3


async def test_open_call_is_clipped_to_its_last_event(db_session, seed_location, mail):
    await _call(db_session, _t(9), None, last_event=_t(9, 10))
    await _call(db_session, _t(9, 30), _t(9, 40))  # after the open call's last event
    await _call(db_session, _t(20), None)  # no event: an instant
    await voice_capacity.record_peaks(db_session, DAY, None)
    row = (await db_session.execute(select(VoiceCapacityDaily))).scalar_one()
    assert (row.peak_concurrent, row.calls) == (1, 3)


async def test_rerun_replaces_the_day(db_session, seed_location, mail):
    await _call(db_session, _t(10), _t(10, 30))
    await voice_capacity.record_peaks(db_session, DAY, None)
    await _call(db_session, _t(10, 5), _t(10, 10))
    await voice_capacity.record_peaks(db_session, DAY, None)
    row = (await db_session.execute(select(VoiceCapacityDaily))).scalar_one()
    assert row.peak_concurrent == 2
    assert (await db_session.get(VoicePlatformDaily, DAY)).peak_concurrent == 2


# -- the quota target ----------------------------------------------------------------

async def test_target_is_base_plus_per_voice_company(db_session, seed_location):
    await subscribe(db_session)
    db_session.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                               location_id=LOCATION_ID, e164="+12125550199",
                               status="active", connect_phone_number_id="pn-1"))
    await db_session.commit()
    await _voice_company(db_session, "coother2", "ogother2", "lcother2")
    # Comped and ended groups do not count.
    await _voice_company(db_session, "cocomped", "ogcomped", "lccomped", status="comped")
    await _voice_company(db_session, "coended1", "ogended1", "lcended1", status="canceled",
                         number_status="releasing")
    assert await voice_capacity.quota_target(db_session, NOW) == 10 + 10 * 2


async def test_target_follows_the_platform_peak(db_session, seed_location):
    db_session.add(VoicePlatformDaily(day=DAY, peak_concurrent=30, cloudwatch_peak=41,
                                      calls=500))
    db_session.add(VoicePlatformDaily(day=NOW.date() - timedelta(days=45),
                                      peak_concurrent=200, calls=1))  # too old
    await db_session.commit()
    assert await voice_capacity.quota_target(db_session, NOW) == 62  # ceil(41 x 1.5)


async def test_target_covers_the_sum_of_allocations(db_session, seed_location):
    await _voice_company(db_session, "coother3", "ogother3", "lcother3")
    db_session.add(VoiceCompanySettings(company_id="coother3", voice_call_allocation=80))
    await db_session.commit()
    assert await voice_capacity.quota_target(db_session, NOW) == 80


# -- quota requests ------------------------------------------------------------------

async def test_quota_step_is_a_no_op_while_provisioning_is_off(db_session, mail):
    client = FakeQuotaClient(value=1)
    assert (await voice_capacity.ensure_quota(db_session, NOW, client))["disabled"] is True
    assert client.requests == []


async def test_quota_request_is_filed_once(db_session, seed_location, provisioning, mail):
    await _voice_company(db_session, "coother4", "ogother4", "lcother4")
    client = FakeQuotaClient(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "requested" and client.requests == [20]
    row = (await db_session.execute(select(VoiceQuotaRequest))).scalar_one()
    assert (row.desired, row.status, row.aws_request_id) == (20, "pending", "req-1")
    assert len(mail["operator"]) == 1 and "20" in mail["operator"][0][1]

    # Still pending: polled, not filed again.
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "pending" and client.requests == [20]
    assert client.polled == ["req-1"]
    assert len(mail["operator"]) == 1


async def test_quota_request_status_is_polled(db_session, seed_location, provisioning, mail):
    await _voice_company(db_session, "coother5", "ogother5", "lcother5")
    client = FakeQuotaClient(value=10)
    await voice_capacity.ensure_quota(db_session, NOW, client)
    client.status, client.value = "approved", 20
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "none"
    row = (await db_session.execute(select(VoiceQuotaRequest)
                                    .execution_options(populate_existing=True))).scalar_one()
    assert row.status == "approved"


async def test_denied_request_is_not_refiled_at_once(db_session, seed_location,
                                                      provisioning, mail):
    await _voice_company(db_session, "coother6", "ogother6", "lcother6")
    client = FakeQuotaClient(value=10)
    await voice_capacity.ensure_quota(db_session, NOW, client)
    client.status = "denied"
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    # Not filed again within 7 days of a denial (test_voice_capacity_review).
    assert out["action"] == "denied_recently" and client.requests == [20]


async def test_large_target_mails_a_support_case_instead(db_session, seed_location,
                                                         provisioning, mail):
    await _voice_company(db_session, "coother7", "ogother7", "lcother7")
    db_session.add(VoiceCompanySettings(company_id="coother7", voice_call_allocation=300))
    await db_session.commit()
    client = FakeQuotaClient(value=10)
    out = await voice_capacity.ensure_quota(db_session, NOW, client)
    assert out["action"] == "manual" and client.requests == []
    row = (await db_session.execute(select(VoiceQuotaRequest))).scalar_one()
    assert (row.desired, row.status) == (300, "manual")
    subject, body = mail["operator"][0]
    assert "300" in body and settings.VOICE_QUOTA_NAME in body
    # The open manual row suppresses a second mail.
    await voice_capacity.ensure_quota(db_session, NOW, client)
    assert len(mail["operator"]) == 1
    # Once AWS applied it, the row is closed.
    client.value = 300
    await voice_capacity.ensure_quota(db_session, NOW, client)
    row = (await db_session.execute(select(VoiceQuotaRequest)
                                    .execution_options(populate_existing=True))).scalar_one()
    assert row.status == "approved"


async def test_quota_failure_mails_the_number_and_never_raises(db_session, seed_location,
                                                               provisioning, mail):
    await _voice_company(db_session, "coother8", "ogother8", "lcother8")
    out = await voice_capacity.ensure_quota(db_session, NOW, FakeQuotaClient(fail=True))
    assert "error" in out
    assert len(mail["operator"]) == 1 and "20" in mail["operator"][0][1]


async def test_quota_not_found_mails_the_operator(db_session, seed_location,
                                                  provisioning, mail):
    out = await voice_capacity.ensure_quota(db_session, NOW, FakeQuotaClient(found=False))
    assert out["error"] == "quota_not_found"
    assert settings.VOICE_QUOTA_NAME in mail["operator"][0][1]


# -- allocation and down-shift -----------------------------------------------------------

async def test_allocation_defaults_and_overrides(db_session, seed_location):
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == \
        settings.VOICE_DEFAULT_COMPANY_ALLOCATION
    db_session.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=3))
    await db_session.commit()
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == 3


async def test_open_calls_count_recent_open_voice_calls(db_session, seed_location):
    now = datetime.now(timezone.utc)
    a = await _call(db_session, now - timedelta(minutes=2), None, updated=now)
    await _call(db_session, now - timedelta(minutes=2), None, updated=now)
    await _call(db_session, now - timedelta(minutes=5), now - timedelta(minutes=4))
    # Idle past VOICE_CALL_IDLE_SECONDS: the caller hung up.
    await _call(db_session, now - timedelta(hours=1), None,
                updated=now - timedelta(seconds=settings.VOICE_CALL_IDLE_SECONDS + 5))
    await _call(db_session, now, None, channel="web", updated=now)
    assert await voice_capacity.open_calls(db_session, COMPANY_ID, now) == 2
    assert await voice_capacity.open_calls(db_session, COMPANY_ID, now,
                                           exclude_conversation_id=a.id) == 1
    db_session.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=2))
    await db_session.commit()
    assert await voice_capacity.at_allocation(db_session, COMPANY_ID, now)
    assert not await voice_capacity.at_allocation(db_session, COMPANY_ID, now,
                                                  exclude_conversation_id=a.id)


async def _alloc(db, n: int, changed: datetime | None) -> None:
    db.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=n,
                                allocation_changed_at=changed))
    await db.commit()


async def _peak(db, day: date, n: int) -> None:
    db.add(VoiceCapacityDaily(company_id=COMPANY_ID, day=day, peak_concurrent=n, calls=n))
    await db.commit()


async def test_down_shift_lowers_an_idle_allocation(db_session, seed_location, mail):
    await _alloc(db_session, 40, NOW - timedelta(days=60))
    await _peak(db_session, DAY, 12)
    await _peak(db_session, DAY - timedelta(days=40), 39)  # outside the 30 days
    out = await voice_capacity.down_shift(db_session, NOW)
    assert out == [{"company": COMPANY_ID, "from": 40, "to": 18}]
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == 18
    assert mail["admins"][0][0] == COMPANY_ID and "18" in mail["admins"][0][2]


async def test_down_shift_never_goes_below_the_default(db_session, seed_location, mail):
    await _alloc(db_session, 40, None)
    assert (await voice_capacity.down_shift(db_session, NOW))[0]["to"] == \
        settings.VOICE_DEFAULT_COMPANY_ALLOCATION


async def test_down_shift_leaves_busy_recent_and_default_allocations(db_session,
                                                                     seed_location, mail):
    await _alloc(db_session, 40, NOW - timedelta(days=60))
    await _peak(db_session, DAY, 20)  # 50% of 40: not below half
    assert await voice_capacity.down_shift(db_session, NOW) == []
    row = await db_session.get(VoiceCompanySettings, COMPANY_ID)
    row.voice_call_allocation, row.allocation_changed_at = 100, NOW - timedelta(days=3)
    await db_session.commit()
    assert await voice_capacity.down_shift(db_session, NOW) == []  # approved recently
    row.voice_call_allocation, row.allocation_changed_at = 10, None
    await db_session.commit()
    assert await voice_capacity.down_shift(db_session, NOW) == []  # at the default
    assert mail["admins"] == []


# -- the daily run ------------------------------------------------------------------------

async def test_daily_run_is_a_no_op_while_provisioning_is_off(db_session, seed_location,
                                                              mail):
    await _call(db_session, _t(10), _t(10, 30))
    assert (await voice_capacity.run_daily(db_session, NOW, FakeQuotaClient()))["disabled"]
    assert (await db_session.execute(select(VoiceCapacityDaily))).scalars().all() == []


async def test_daily_run_records_shifts_sweeps_and_requests(db_session, seed_location,
                                                            provisioning, mail):
    await _call(db_session, _t(10), _t(10, 30))
    old = NOW.date() - timedelta(days=settings.RETENTION_VOICE_CAPACITY_DAYS + 1)
    await _peak(db_session, old, 5)
    db_session.add(VoicePlatformDaily(day=old, peak_concurrent=5, calls=5))
    await db_session.commit()
    client = FakeQuotaClient(value=100)
    out = await voice_capacity.run_daily(db_session, NOW, client)
    assert out["peaks"]["day"] == DAY.isoformat()
    days = (await db_session.execute(select(VoiceCapacityDaily.day))).scalars().all()
    assert days == [DAY]
    assert await db_session.get(VoicePlatformDaily, old) is None
    assert out["quota"]["action"] == "none"


# -- capacity requests ----------------------------------------------------------------------

async def test_capacity_request_mails_the_operator_with_peaks(phone_line, db_session, seed_location,
                                                              seed_manager, mail):
    await _peak(db_session, DAY, 7)
    req = await voice_capacity.request_capacity(
        db_session, COMPANY_ID, seed_manager.id, [LOCATION_ID], 25, date(2026, 12, 15),
        "Holiday rush", NOW)
    assert req.status == "pending" and req.expected_peak == 25
    subject, body = mail["operator"][0]
    assert "25" in body and "7" in body and req.id in body and "Holiday rush" in body


async def test_one_pending_capacity_request_at_a_time(phone_line, db_session, seed_location,
                                                      seed_manager, mail):
    await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                          [LOCATION_ID], 25, None, "", NOW)
    with pytest.raises(voice_capacity.CapacityRequestPending):
        await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                              [LOCATION_ID], 30, None, "", NOW)


async def test_capacity_request_refuses_other_locations(db_session, seed_location,
                                                        seed_manager, mail, phone_line):
    await _voice_company(db_session, "coother9", "ogother9", "lcother9")
    with pytest.raises(voice_capacity.UnknownLocation):
        await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                              ["lcother9"], 25, None, "", NOW)


async def test_approval_sets_the_allocation_files_the_quota_and_mails(
        db_session, seed_location, seed_manager, provisioning, mail):
    await subscribe(db_session)
    db_session.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                               location_id=LOCATION_ID, e164="+12125550199",
                               status="active", connect_phone_number_id="pn-1"))
    await db_session.commit()
    req = await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                                [LOCATION_ID], 60, None, "", NOW)
    client = FakeQuotaClient(value=20)
    out = await voice_capacity.approve_request(db_session, req.id, None, NOW, client)
    assert out["allocation"] == 60
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == 60
    assert client.requests == [60]
    row = await db_session.get(VoiceCapacityRequest, req.id)
    assert (row.status, row.granted_allocation) == ("approved", 60)
    assert mail["admins"][-1][0] == COMPANY_ID and "60" in mail["admins"][-1][2]
    settings_row = await db_session.get(VoiceCompanySettings, COMPANY_ID)
    assert settings_row.allocation_changed_at == NOW
    with pytest.raises(voice_capacity.CapacityRequestError):
        await voice_capacity.approve_request(db_session, req.id, None, NOW, client)


async def test_approval_can_grant_a_different_number(phone_line, db_session, seed_location,
                                                     seed_manager, mail):
    req = await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                                [LOCATION_ID], 60, None, "", NOW)
    out = await voice_capacity.approve_request(db_session, req.id, 30, NOW,
                                               FakeQuotaClient())
    assert out["allocation"] == 30
    assert out["quota"].get("disabled") is True  # provisioning off: nothing filed


async def test_decline_mails_the_admins(phone_line, db_session, seed_location, seed_manager, mail):
    req = await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                                [LOCATION_ID], 60, None, "", NOW)
    await voice_capacity.decline_request(db_session, req.id, NOW)
    row = await db_session.get(VoiceCapacityRequest, req.id)
    assert row.status == "declined"
    assert await voice_capacity.allocation_for(db_session, COMPANY_ID) == \
        settings.VOICE_DEFAULT_COMPANY_ALLOCATION
    assert mail["admins"][-1][0] == COMPANY_ID


async def test_admin_mail_goes_to_admins_only(db_session, seed_location, seed_manager,
                                              regular_manager, monkeypatch):
    sent: list[list[str]] = []
    monkeypatch.setattr(voice_capacity, "_send", lambda to, subject, body: sent.append(to))
    await voice_capacity._mail_admins(db_session, COMPANY_ID, "s", "b")
    assert sent == [[seed_manager.email]]


# -- the summary card ------------------------------------------------------------------------

async def test_summary_shows_allocation_peak_and_voice_locations(db_session, seed_location,
                                                                 seed_manager, mail):
    await subscribe(db_session)
    db_session.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                               location_id=LOCATION_ID, e164="+12125550199",
                               status="active", connect_phone_number_id="pn-1"))
    await _peak(db_session, DAY, 4)
    await _peak(db_session, DAY - timedelta(days=3), 6)
    await voice_capacity.request_capacity(db_session, COMPANY_ID, seed_manager.id,
                                          [LOCATION_ID], 25, None, "", NOW)
    s = await voice_capacity.capacity_summary(db_session, COMPANY_ID, NOW)
    assert s["allocation"] == settings.VOICE_DEFAULT_COMPANY_ALLOCATION
    assert s["voice_locations"] == [{"location_id": LOCATION_ID, "name": "Downtown Store"}]
    assert s["busiest"] == {"peak_concurrent": 6,
                            "day": (DAY - timedelta(days=3)).isoformat()}
    assert s["pending_request"]["expected_peak"] == 25
    assert s["default_staff_transfer_number"] is None
    assert s["max_expected_peak"] == 500


# -- wiring -------------------------------------------------------------------------------

@pytest.fixture
def connect(monkeypatch):
    from backend.services import connect_numbers
    from tests.reservations.test_voice_numbers import FakeConnect

    fake = FakeConnect()
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONNECT_INSTANCE_ARN",
                        "arn:aws:connect:us-east-1:1:instance/inst-1")
    monkeypatch.setattr(settings, "CONNECT_CONTACT_FLOW_ID", "flow-1")
    monkeypatch.setattr(connect_numbers, "_make_client", lambda: fake)
    connect_numbers.reset_client()
    yield fake
    connect_numbers.reset_client()


async def test_new_numbers_trigger_the_quota_step(db_session, seed_location, connect,
                                                  monkeypatch):
    from backend.reservations import voice_numbers

    calls: list[str] = []

    async def ensure(db, now=None, client=None, reason=""):
        calls.append(reason)
        return {"action": "none"}

    monkeypatch.setattr(voice_capacity, "ensure_quota", ensure)
    await subscribe(db_session)
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert calls == [f"new numbers for group {AGENT_OG_ID}"]
    # Nothing new: no quota step.
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert len(calls) == 1


async def test_quota_step_failure_never_breaks_numbers(db_session, seed_location, connect,
                                                       monkeypatch):
    from backend.reservations import voice_numbers

    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(voice_capacity, "ensure_quota", boom)
    await subscribe(db_session)
    out = await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert out["claimed"] == [LOCATION_ID]


async def test_daily_script_runs_the_capacity_step_and_mails_errors(monkeypatch):
    from contextlib import asynccontextmanager

    import backend.database
    import backend.services.operator_alerts
    from backend.reservations import voice_numbers
    from backend.scripts import run_agent_overage_reconcile as script
    from backend.services import billing_reservations as br

    @asynccontextmanager
    async def session():
        yield object()

    async def billing(db):
        return {"errors": []}

    async def numbers(db):
        return {"errors": []}

    async def capacity(db):
        return {"errors": [{"step": "peaks", "error": "down"}], "quota": {}}

    sent = []
    monkeypatch.setattr(backend.database, "async_session_factory", session)
    monkeypatch.setattr(br, "reconcile_all_agent_subscriptions", billing)
    monkeypatch.setattr(voice_numbers, "run_daily", numbers)
    monkeypatch.setattr(voice_capacity, "run_daily", capacity)
    monkeypatch.setattr(backend.services.operator_alerts, "_send_operator_email",
                        lambda subject, body, label: sent.append((subject, body)) or True)
    await script.main()
    assert len(sent) == 1 and "peaks" in sent[0][1]
