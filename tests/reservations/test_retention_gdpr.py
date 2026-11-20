"""Reservations retention (#259): conversations and diner contact age out
after RETENTION_RESERVATIONS_DAYS; diner data stays out of the per-user
GDPR export (served by test_diner_data.py's routes)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.models import (
    BOOKED, CANCELLED, EXPIRED, HELD, NO_SHOW, SOURCE_STAFF,
    Reservation,
)
from backend.services.data_retention import run_data_retention
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _res(age_days: int, status: str, location_id: str = LOCATION_ID,
         name: str = "Ada Lovelace") -> Reservation:
    end = datetime.now(timezone.utc) - timedelta(days=age_days)
    return Reservation(
        company_id=COMPANY_ID, location_id=location_id, party_size=2,
        start_at=end - timedelta(hours=2), end_at=end, status=status,
        diner_name=name, diner_phone="+12125550100", diner_email="ada@example.com",
        notes="window seat", source=SOURCE_STAFF,
    )


async def _conv(db: AsyncSession, age_days: int, location_id: str = LOCATION_ID,
                text: str = "Table for two?") -> AgentConversation:
    conv = await recording.start_conversation(
        db, COMPANY_ID, location_id, "web", source_key="s" * 64)
    conv.started_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    await recording.record_event(db, conv, "user_turn",
                                 {"content": [{"type": "text", "text": text}]})
    await db.commit()
    return conv


async def test_retention_purges_old_conversations_and_scrubs_old_diners(
    db_session: AsyncSession, seed_location
):
    old_conv = await _conv(db_session, 400)
    new_conv = await _conv(db_session, 10)
    # Every status ages out: holds and cancellations store diner contact too.
    old = [_res(400, s) for s in (BOOKED, CANCELLED, EXPIRED, NO_SHOW, HELD)]
    new = _res(10, CANCELLED)
    db_session.add_all([*old, new])
    await db_session.commit()
    old_ids, new_id = [r.id for r in old], new.id
    old_conv_id, new_conv_id = old_conv.id, new_conv.id

    summary = await run_data_retention(db_session)
    assert summary["old_agent_conversations_deleted"] == 1
    assert summary["old_reservation_diners_scrubbed"] == 5

    db_session.expire_all()
    assert await db_session.get(AgentConversation, old_conv_id) is None
    events = (await db_session.execute(
        select(AgentEvent).where(AgentEvent.conversation_id == old_conv_id))).all()
    assert events == []
    assert await db_session.get(AgentConversation, new_conv_id) is not None

    for rid in old_ids:
        r = await db_session.get(Reservation, rid)
        assert r is not None, "the row stays for covers analytics"
        assert (r.diner_name, r.diner_phone, r.diner_email, r.notes) == (None,) * 4
        assert r.party_size == 2
    kept = await db_session.get(Reservation, new_id)
    assert kept.diner_name == "Ada Lovelace" and kept.diner_phone == "+12125550100"
    assert kept.diner_email == "ada@example.com" and kept.notes == "window seat"

    # A second sweep finds nothing left to scrub.
    again = await run_data_retention(db_session)
    assert again["old_reservation_diners_scrubbed"] == 0



async def test_retention_purges_old_screened_calls(db_session: AsyncSession, seed_location):
    from backend.config import settings
    from backend.reservations.screening_models import ScreenedCall

    now = datetime.now(timezone.utc)
    window = timedelta(days=settings.RETENTION_RESERVATIONS_DAYS)
    old = ScreenedCall(company_id=COMPANY_ID, location_id=LOCATION_ID, caller_key=None,
                       decision="answer", reason="withheld",
                       created_at=now - window - timedelta(days=1))
    new = ScreenedCall(company_id=COMPANY_ID, location_id=LOCATION_ID, caller_key=None,
                       decision="block", reason="ftc", created_at=now - timedelta(days=10))
    db_session.add_all([old, new])
    await db_session.commit()
    old_id, new_id = old.id, new.id

    summary = await run_data_retention(db_session)
    assert summary["old_screened_calls_deleted"] == 1
    db_session.expire_all()
    assert await db_session.get(ScreenedCall, old_id) is None
    assert await db_session.get(ScreenedCall, new_id) is not None


async def test_retention_purges_long_expired_number_rules(db_session: AsyncSession,
                                                          seed_location):
    from backend.config import settings
    from backend.reservations.screening_models import NumberRule

    now = datetime.now(timezone.utc)
    window = timedelta(days=settings.RETENTION_RESERVATIONS_DAYS)
    old = NumberRule(company_id=COMPANY_ID, location_id=LOCATION_ID, e164="+13475550101",
                     action="block", expires_at=now - window - timedelta(days=1))
    recent = NumberRule(company_id=COMPANY_ID, location_id=None, e164="+13475550102",
                        action="block", expires_at=now - timedelta(days=10))
    permanent = NumberRule(company_id=COMPANY_ID, location_id=LOCATION_ID,
                           e164="+13475550103", action="allow", expires_at=None,
                           created_at=now - window - timedelta(days=30))
    db_session.add_all([old, recent, permanent])
    await db_session.commit()
    ids = old.id, recent.id, permanent.id

    summary = await run_data_retention(db_session)
    assert summary["old_number_rules_deleted"] == 1
    db_session.expire_all()
    assert await db_session.get(NumberRule, ids[0]) is None
    assert await db_session.get(NumberRule, ids[1]) is not None
    assert await db_session.get(NumberRule, ids[2]) is not None


async def test_gdpr_user_export_carries_no_diner_data(
    client, manager_token, seed_location, db_session: AsyncSession
):
    """Ruling P: a manager's own export is about the manager; diner data is
    served by the reservations diner-data tools instead."""
    db_session.add(_res(3, BOOKED))
    await db_session.commit()
    await _conv(db_session, 3)
    r = await client.get("/api/v1/gdpr/export", headers=_auth(manager_token))
    assert r.status_code == 200
    assert "reservations" not in r.json() and "agent_conversations" not in r.json()
    assert "Ada Lovelace" not in r.text


async def test_retention_purges_mock_external_idempotency_keys_after_a_day(
        db_session: AsyncSession, seed_location):
    from backend.reservations.mock_external_models import MockExternalIdempotency

    now = datetime.now(timezone.utc)
    db_session.add_all([
        MockExternalIdempotency(key="c:book:1", company_id=COMPANY_ID, response={},
                                created_at=now - timedelta(hours=25)),
        MockExternalIdempotency(key="c:book:2", company_id=COMPANY_ID, response={},
                                created_at=now - timedelta(hours=1)),
    ])
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["old_mock_external_idempotency_deleted"] == 1
    db_session.expire_all()
    assert await db_session.get(MockExternalIdempotency, (COMPANY_ID, "c:book:1")) is None
    assert await db_session.get(MockExternalIdempotency, (COMPANY_ID, "c:book:2")) is not None


# ---------------------------------------------------------------------------
# The public demo location (decision 94): its manager login is shown to every
# visitor, so the daily sweep purges its diner data after 24 hours.
# ---------------------------------------------------------------------------


def _visitor_res(company_id: str, location_id: str, hours: int,
                 res_id: str | None = None) -> Reservation:
    start = datetime.now(timezone.utc) + timedelta(days=2)
    return Reservation(
        id=res_id, company_id=company_id, location_id=location_id, party_size=2,
        start_at=start, end_at=start + timedelta(hours=2), status=BOOKED,
        diner_name="Real Person", diner_phone="+447700900123",
        diner_email="real@person.test", notes="my birthday", source=SOURCE_STAFF,
        created_at=datetime.now(timezone.utc) - timedelta(hours=hours))


async def _visitor_conv(db: AsyncSession, company_id: str, location_id: str,
                        hours: int) -> str:
    conv = await recording.start_conversation(
        db, company_id, location_id, "web", source_key="s" * 64)
    conv.started_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    await recording.record_event(db, conv, "user_turn",
                                 {"content": [{"type": "text", "text": "I'm Real Person"}]})
    await db.commit()
    return conv.id


def _contact(r: Reservation) -> tuple:
    return (r.diner_name, r.diner_phone, r.diner_email, r.notes)


async def test_the_sweep_purges_the_demo_locations_diner_data_after_a_day(
    db_session: AsyncSession, seed_location, test_location, monkeypatch
):
    from backend.config import settings
    monkeypatch.setattr(settings, "RESERVATIONS_DEMO_COMPANY_ID", COMPANY_ID)
    monkeypatch.setattr(settings, "RESERVATIONS_DEMO_LOCATION_ID", LOCATION_ID)
    other_co, other_loc = test_location.company_id, test_location.id

    old, fresh = _visitor_res(COMPANY_ID, LOCATION_ID, 25), _visitor_res(COMPANY_ID, LOCATION_ID, 23)
    # A seeded fictional booking: deterministic rsv id, example.com email.
    seeded = _visitor_res(COMPANY_ID, LOCATION_ID, 25, res_id="rsv1a2b3")
    seeded.diner_name, seeded.diner_email = "Maya Patel", "maya.patel@example.com"
    elsewhere = _visitor_res(other_co, other_loc, 25)
    db_session.add_all([old, fresh, seeded, elsewhere])
    await db_session.commit()
    ids = {k: r.id for k, r in
           {"old": old, "fresh": fresh, "seeded": seeded, "elsewhere": elsewhere}.items()}
    old_conv = await _visitor_conv(db_session, COMPANY_ID, LOCATION_ID, 25)
    fresh_conv = await _visitor_conv(db_session, COMPANY_ID, LOCATION_ID, 23)
    elsewhere_conv = await _visitor_conv(db_session, other_co, other_loc, 25)

    summary = await run_data_retention(db_session)
    assert summary["demo_reservations_scrubbed"] == 1
    assert summary["demo_conversations_deleted"] == 1

    db_session.expire_all()
    assert _contact(await db_session.get(Reservation, ids["old"])) == (None,) * 4
    assert _contact(await db_session.get(Reservation, ids["fresh"]))[0] == "Real Person"
    assert _contact(await db_session.get(Reservation, ids["seeded"]))[0] == "Maya Patel"
    assert _contact(await db_session.get(Reservation, ids["elsewhere"]))[0] == "Real Person"
    assert await db_session.get(AgentConversation, old_conv) is None
    assert await db_session.get(AgentConversation, fresh_conv) is not None
    assert await db_session.get(AgentConversation, elsewhere_conv) is not None


async def test_the_demo_purge_never_touches_another_companys_location(
    db_session: AsyncSession, seed_location, test_location, monkeypatch
):
    """The demo location id under another company is not the demo."""
    from backend.config import settings
    monkeypatch.setattr(settings, "RESERVATIONS_DEMO_COMPANY_ID", test_location.company_id)
    monkeypatch.setattr(settings, "RESERVATIONS_DEMO_LOCATION_ID", LOCATION_ID)
    res = _visitor_res(COMPANY_ID, LOCATION_ID, 25)
    db_session.add(res)
    await db_session.commit()
    rid = res.id
    conv = await _visitor_conv(db_session, COMPANY_ID, LOCATION_ID, 25)

    summary = await run_data_retention(db_session)
    assert summary["demo_reservations_scrubbed"] == 0
    db_session.expire_all()
    assert _contact(await db_session.get(Reservation, rid))[0] == "Real Person"
    assert await db_session.get(AgentConversation, conv) is not None
