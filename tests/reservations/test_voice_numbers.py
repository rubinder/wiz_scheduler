"""Voice numbers per location and Connect provisioning (#259 step 6, Task 1).
The boto3 Connect client is faked through connect_numbers._make_client;
nothing here calls AWS."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from backend.config import settings
from backend.models import Location
from backend.reservations import voice_numbers
from backend.reservations.subscription import ReservationSubscription
from backend.reservations.voice_number_models import VoiceNumber
from backend.services import billing_reservations, connect_numbers, operator_alerts
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = pytest.mark.asyncio

ARN = "arn:aws:connect:us-east-1:123456789012:instance/inst-1"
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class FakeConnect:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.n = 0
        self.fail_claim = False
        self.fail_associate = False
        self.fixed: str | None = None  # offer this number on every search

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def search_available_phone_numbers(self, **kw):
        self.calls.append(("search", kw))
        self.n += 1
        number = self.fixed or f"+1212555{self.n:04d}"
        return {"AvailableNumbersList": [{"PhoneNumber": number, "PhoneNumberType": "DID"}]}

    def claim_phone_number(self, **kw):
        self.calls.append(("claim", kw))
        if self.fail_claim:
            raise RuntimeError("ServiceQuotaExceededException")
        return {"PhoneNumberId": f"pn-{kw['PhoneNumber']}", "PhoneNumberArn": "arn:x"}

    def associate_phone_number_contact_flow(self, **kw):
        self.calls.append(("associate", kw))
        if self.fail_associate:
            raise RuntimeError("throttled")

    def release_phone_number(self, **kw):
        self.calls.append(("release", kw))


@pytest.fixture
def fake(monkeypatch) -> FakeConnect:
    client = FakeConnect()
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", True)
    monkeypatch.setattr(settings, "CONNECT_INSTANCE_ARN", ARN)
    monkeypatch.setattr(settings, "CONNECT_CONTACT_FLOW_ID", "flow-1")
    monkeypatch.setattr(connect_numbers, "_make_client", lambda: client)
    connect_numbers.reset_client()
    yield client
    connect_numbers.reset_client()


async def _rows(db) -> dict[str, VoiceNumber]:
    db.expire_all()
    rows = (await db.execute(select(VoiceNumber))).scalars().all()
    return {r.location_id: r for r in rows}


async def _set_status(db, status: str) -> None:
    await db.execute(update(ReservationSubscription)
                     .where(ReservationSubscription.ownership_group_id == AGENT_OG_ID)
                     .values(status=status))
    await db.commit()


async def test_claims_one_number_per_active_location_on_subscribe(
        db_session, seed_location, other_location, fake):
    await subscribe(db_session, "active")
    out = await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)

    rows = await _rows(db_session)
    assert set(rows) == {LOCATION_ID, OTHER_LOCATION_ID}
    assert sorted(out["claimed"]) == sorted([LOCATION_ID, OTHER_LOCATION_ID])
    for row in rows.values():
        assert row.status == "active"
        assert row.e164.startswith("+1212555")
        assert row.connect_phone_number_id == f"pn-{row.e164}"
        assert row.company_id == COMPANY_ID and row.ownership_group_id == AGENT_OG_ID
    search = next(kw for n, kw in fake.calls if n == "search")
    assert search == {"TargetArn": ARN, "PhoneNumberCountryCode": "US",
                      "PhoneNumberType": "DID", "MaxResults": 1}
    claims = [kw for n, kw in fake.calls if n == "claim"]
    assert {c["ClientToken"] for c in claims} == {
        f"wizscheduler-voice-{r.id}" for r in rows.values()}
    assoc = [kw for n, kw in fake.calls if n == "associate"]
    assert {a["InstanceId"] for a in assoc} == {"inst-1"}
    assert {a["ContactFlowId"] for a in assoc} == {"flow-1"}

    # A second run claims nothing more.
    fake.calls.clear()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert fake.calls == []


async def test_nothing_happens_with_provisioning_off(db_session, seed_location, monkeypatch):
    def boom():
        raise AssertionError("Connect must not be called")
    monkeypatch.setattr(connect_numbers, "_make_client", boom)
    connect_numbers.reset_client()
    assert settings.VOICE_PROVISIONING_ENABLED is False
    await subscribe(db_session, "active")

    assert (await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW))["disabled"]
    assert await voice_numbers.release_due(db_session, NOW) == {"disabled": True}
    assert await voice_numbers.run_daily(db_session, NOW) == {"disabled": True}
    assert await connect_numbers.claim(client_token="t") == connect_numbers.DISABLED
    assert await connect_numbers.release("pn") == connect_numbers.DISABLED
    assert await _rows(db_session) == {}


async def test_ended_subscription_holds_seven_days_then_releases(db_session, seed_location, fake):
    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    pn = (await _rows(db_session))[LOCATION_ID].connect_phone_number_id
    e164 = (await _rows(db_session))[LOCATION_ID].e164

    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "releasing"
    assert row.release_after == NOW + timedelta(days=7)

    fake.calls.clear()
    assert (await voice_numbers.release_due(db_session, NOW + timedelta(days=6)))["released"] == []
    assert fake.calls == []
    out = await voice_numbers.release_due(db_session, NOW + timedelta(days=7, seconds=1))
    assert out["released"] == [LOCATION_ID]
    assert fake.calls == [("release", {"PhoneNumberId": pn})]
    released = (await _rows(db_session))[LOCATION_ID]
    assert (released.status, released.e164) == ("released", None)
    # A released number no longer routes calls.
    assert await voice_numbers.location_for_number(db_session, e164) is None


async def test_resubscribe_within_hold_reactivates_without_a_new_claim(
        db_session, seed_location, fake):
    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    e164 = (await _rows(db_session))[LOCATION_ID].e164
    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)

    fake.calls.clear()
    await _set_status(db_session, "active")
    out = await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW + timedelta(days=3))
    row = (await _rows(db_session))[LOCATION_ID]
    assert out["reactivated"] == [LOCATION_ID]
    assert (row.status, row.e164, row.release_after) == ("active", e164, None)
    assert fake.calls == []


async def test_resubscribe_after_release_claims_a_fresh_number(db_session, seed_location, fake):
    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    old = (await _rows(db_session))[LOCATION_ID]
    old_id, old_e164 = old.id, old.e164
    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    await voice_numbers.release_due(db_session, NOW + timedelta(days=8))

    await _set_status(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW + timedelta(days=9))
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "active" and row.id != old_id and row.e164 != old_e164


async def test_archived_location_gets_no_number_and_releases_its_own(
        db_session, seed_location, other_location, fake):
    await subscribe(db_session, "active")
    await db_session.execute(update(Location).where(Location.id == OTHER_LOCATION_ID)
                             .values(archived_at=NOW))
    await db_session.commit()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert set(await _rows(db_session)) == {LOCATION_ID}

    # Archiving a location that has a number puts it on hold.
    await db_session.execute(update(Location).where(Location.id == LOCATION_ID)
                             .values(archived_at=NOW))
    await db_session.commit()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert (await _rows(db_session))[LOCATION_ID].status == "releasing"


async def test_claim_failure_stays_requested_and_the_daily_run_retries(
        db_session, seed_location, fake):
    await subscribe(db_session, "active")
    fake.fail_claim = True
    out = await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert out["pending"] == [LOCATION_ID]
    row = (await _rows(db_session))[LOCATION_ID]
    assert (row.status, row.connect_phone_number_id) == ("requested", None)
    token = f"wizscheduler-voice-{row.id}"

    fake.fail_claim = False
    daily = await voice_numbers.run_daily(db_session, NOW)
    assert daily["errors"] == []
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "active"
    # Same row, same number, same idempotency token on the retry; no new search.
    claims = [kw for n, kw in fake.calls if n == "claim"]
    assert len(claims) == 2
    assert {kw["ClientToken"] for kw in claims} == {token}
    assert {kw["PhoneNumber"] for kw in claims} == {row.e164}
    assert fake.names().count("search") == 1


async def test_routing_failure_keeps_the_claimed_number_and_retries_routing_only(
        db_session, seed_location, fake):
    await subscribe(db_session, "active")
    fake.fail_associate = True
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "requested" and row.connect_phone_number_id is not None

    fake.fail_associate = False
    fake.calls.clear()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert fake.names() == ["associate"]
    assert (await _rows(db_session))[LOCATION_ID].status == "active"


async def test_location_for_number_is_an_exact_cross_tenant_match(
        db_session, seed_location, fake):
    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    e164 = (await _rows(db_session))[LOCATION_ID].e164

    assert await voice_numbers.location_for_number(db_session, e164) == (COMPANY_ID, LOCATION_ID)
    assert await voice_numbers.location_for_number(db_session, e164[:-1]) is None
    assert await voice_numbers.location_for_number(db_session, "") is None
    # Still routed during the release hold.
    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert await voice_numbers.location_for_number(db_session, e164) == (COMPANY_ID, LOCATION_ID)


async def test_billing_hook_never_raises(db_session, seed_location, monkeypatch, fake):
    calls: list[str] = []

    async def broken(db, og_id, now=None, **kw):
        calls.append(og_id)
        raise RuntimeError("db gone")
    monkeypatch.setattr(voice_numbers, "ensure_for_group", broken)
    await billing_reservations._ensure_voice(db_session, AGENT_OG_ID, claim=True)
    assert calls == [AGENT_OG_ID]


async def test_manager_route_shows_the_number(
        client, db_session, seed_location, manager_token, monkeypatch, fake):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    url = f"/api/v1/reservations/locations/{LOCATION_ID}/voice"
    headers = {"Authorization": f"Bearer {manager_token}"}

    r = await client.get(url, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"e164": None, "status": "none", "release_after": None}

    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    r = await client.get(url, headers=headers)
    assert r.json()["status"] == "active" and r.json()["e164"].startswith("+1212555")

    assert (await client.get("/api/v1/reservations/locations/nope/voice",
                             headers=headers)).status_code == 404
    # An archived location still shows its number (on hold).
    await db_session.execute(update(Location).where(Location.id == LOCATION_ID)
                             .values(archived_at=NOW))
    await db_session.commit()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    r = await client.get(url, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "releasing"
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    assert (await client.get(url, headers=headers)).status_code == 404


async def test_group_deletion_releases_numbers_and_deletes_rows(
        db_session, seed_location, fake, monkeypatch):
    from backend.models import OwnershipGroup
    from backend.services.billing import delete_og_and_dependents

    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    pn = (await _rows(db_session))[LOCATION_ID].connect_phone_number_id
    fake.calls.clear()

    alerts: list = []
    monkeypatch.setattr(operator_alerts, "send_voice_numbers_unreleased_alert",
                        lambda *a: alerts.append(a))
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert fake.calls == [("release", {"PhoneNumberId": pn})]
    assert await _rows(db_session) == {}
    assert alerts == []


async def test_group_deletion_carries_on_when_connect_fails(
        db_session, seed_location, fake, monkeypatch):
    from backend.models import OwnershipGroup
    from backend.services.billing import delete_og_and_dependents

    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)

    pn = (await _rows(db_session))[LOCATION_ID].connect_phone_number_id
    alerts: list = []
    monkeypatch.setattr(operator_alerts, "send_voice_numbers_unreleased_alert",
                        lambda *a: alerts.append(a))

    def boom(**kw):
        raise RuntimeError("AccessDenied")
    fake.release_phone_number = boom
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert await _rows(db_session) == {}
    assert alerts == [(AGENT_OG_ID, [pn], "Connect refused or failed")]


async def test_group_deletion_with_provisioning_off_alerts_the_operator(
        db_session, seed_location, fake, monkeypatch):
    from backend.models import OwnershipGroup
    from backend.services.billing import delete_og_and_dependents

    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    pn = (await _rows(db_session))[LOCATION_ID].connect_phone_number_id
    alerts: list = []
    monkeypatch.setattr(operator_alerts, "send_voice_numbers_unreleased_alert",
                        lambda *a: alerts.append(a))
    monkeypatch.setattr(settings, "VOICE_PROVISIONING_ENABLED", False)
    og = await db_session.get(OwnershipGroup, AGENT_OG_ID)
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    assert alerts == [(AGENT_OG_ID, [pn], "VOICE_PROVISIONING_ENABLED is off")]


async def test_a_claim_lost_after_search_retries_the_same_number_and_token(
        db_session, seed_location, fake):
    """The searched e164 is committed before claim_phone_number, so a claim
    whose answer was lost is repeated exactly (Connect: idempotent)."""
    await subscribe(db_session, "active")
    fake.fail_claim = True
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "requested" and row.e164 is not None
    assert row.connect_phone_number_id is None
    searched = row.e164

    fake.fail_claim = False
    fake.calls.clear()
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert fake.names() == ["claim", "associate"]
    assert fake.calls[0][1]["PhoneNumber"] == searched
    assert fake.calls[0][1]["ClientToken"] == f"wizscheduler-voice-{row.id}"
    assert (await _rows(db_session))[LOCATION_ID].status == "active"


async def test_a_request_unclaimed_for_a_day_is_a_daily_error(db_session, seed_location, fake):
    await subscribe(db_session, "active")
    fake.fail_claim = True
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert (await voice_numbers.run_daily(db_session, NOW))["errors"] == []

    await db_session.execute(update(VoiceNumber).values(created_at=NOW - timedelta(hours=25)))
    await db_session.commit()
    errors = (await voice_numbers.run_daily(db_session, NOW))["errors"]
    assert [(e["og"], e["location"]) for e in errors] == [(AGENT_OG_ID, LOCATION_ID)]
    assert "not claimed" in errors[0]["error"]


async def test_a_released_number_offered_again_can_be_claimed_by_another_location(
        db_session, seed_location, other_location, fake):
    await subscribe(db_session, "active")
    await db_session.execute(update(Location).where(Location.id == OTHER_LOCATION_ID)
                             .values(archived_at=NOW))
    await db_session.commit()
    fake.fixed = "+12125550999"
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    await voice_numbers.release_due(db_session, NOW + timedelta(days=8))

    # Connect offers the same number again, now to the other location.
    await db_session.execute(update(Location).where(Location.id == OTHER_LOCATION_ID)
                             .values(archived_at=None))
    await db_session.execute(update(Location).where(Location.id == LOCATION_ID)
                             .values(archived_at=NOW))
    await db_session.commit()
    await _set_status(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW + timedelta(days=9))
    rows = await _rows(db_session)
    assert (rows[OTHER_LOCATION_ID].status, rows[OTHER_LOCATION_ID].e164) == (
        "active", "+12125550999")
    assert rows[LOCATION_ID].status == "released"


async def test_a_claimed_but_unrouted_number_is_held_never_dropped(
        db_session, seed_location, fake):
    await subscribe(db_session, "active")
    fake.fail_associate = True
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    assert (await _rows(db_session))[LOCATION_ID].connect_phone_number_id is not None

    await _set_status(db_session, "canceled")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW)
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "releasing"  # Connect still holds it: release_due gives it back
    await voice_numbers.release_due(db_session, NOW + timedelta(days=8))
    assert "release" in fake.names()


async def test_a_hold_racing_a_claim_holds_the_claimed_number(
        db_session, seed_location, fake, monkeypatch):
    """The hold read the row before a concurrent claim stored its phone
    number id: its compare-and-set misses, it re-reads, and the number goes
    on hold (releasing) instead of being dropped as never claimed."""
    await subscribe(db_session, "active")
    await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW, claim=False)
    row = (await _rows(db_session))[LOCATION_ID]
    assert row.status == "requested"
    row_id = row.id

    real_state = voice_numbers._state
    reads: list = []

    async def stale_then_real(db, rid):
        seen = await real_state(db, rid)
        reads.append(seen)
        if len(reads) == 1:
            # What the hold saw before the claim committed...
            # ...and the claim commits now, from another session.
            from tests.conftest import get_test_session_factory
            async with get_test_session_factory()() as other:
                await other.execute(update(VoiceNumber).where(VoiceNumber.id == rid)
                                    .values(e164="+12125550111",
                                            connect_phone_number_id="pn-raced"))
                await other.commit()
            return ("requested", None)
        return seen
    monkeypatch.setattr(voice_numbers, "_state", stale_then_real)

    assert await voice_numbers._hold(db_session, row_id, NOW) == "releasing"
    assert len(reads) == 2
    row = (await _rows(db_session))[LOCATION_ID]
    assert (row.status, row.connect_phone_number_id) == ("releasing", "pn-raced")
    assert row.release_after == NOW + timedelta(days=7)


async def test_webhook_mode_requests_without_calling_connect(db_session, seed_location, fake):
    await subscribe(db_session, "active")
    out = await voice_numbers.ensure_for_group(db_session, AGENT_OG_ID, NOW, claim=False)
    assert out["pending"] == [LOCATION_ID] and fake.calls == []
    assert (await _rows(db_session))[LOCATION_ID].status == "requested"
