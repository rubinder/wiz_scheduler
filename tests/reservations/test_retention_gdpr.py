"""Reservations retention and GDPR export (#259): conversations and diner
contact age out after RETENTION_RESERVATIONS_DAYS; the company's export
carries both."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.models import (
    BOOKED, CANCELLED, EXPIRED, HELD, NO_SHOW, SOURCE_STAFF, SOURCE_WEB_AGENT,
    Reservation,
)
from backend.services.data_retention import run_data_retention
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID

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


async def test_gdpr_export_has_reservations_and_conversations(
    client, manager_token, seed_location, db_session: AsyncSession
):
    res = _res(3, BOOKED)
    res.source = SOURCE_WEB_AGENT
    db_session.add(res)
    await db_session.commit()
    conv = await _conv(db_session, 3, text="Hi, it's Ada")

    r = await client.get("/api/v1/gdpr/export", headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    data = r.json()
    [row] = data["reservations"]
    assert row["id"] == res.id and row["diner_name"] == "Ada Lovelace"
    assert row["diner_phone"] == "+12125550100" and row["diner_email"] == "ada@example.com"
    assert row["notes"] == "window seat" and row["start_at"].endswith("+00:00")
    [c] = data["agent_conversations"]
    assert c["id"] == conv.id
    assert c["events"][0]["payload"]["content"][0]["text"] == "Hi, it's Ada"
    assert "source_key" not in r.text and conv.public_token not in r.text


async def test_gdpr_export_respects_location_scope_and_role(
    client, regular_token, employee_token, seed_employee_user, other_location,
    db_session: AsyncSession,
):
    db_session.add_all([_res(3, BOOKED, name="In Scope"),
                        _res(3, BOOKED, OTHER_LOCATION_ID, name="Out Of Scope")])
    await db_session.commit()
    await _conv(db_session, 3, OTHER_LOCATION_ID)

    data = (await client.get("/api/v1/gdpr/export", headers=_auth(regular_token))).json()
    assert [x["diner_name"] for x in data["reservations"]] == ["In Scope"]
    assert data["agent_conversations"] == []

    data = (await client.get("/api/v1/gdpr/export", headers=_auth(employee_token))).json()
    assert data["reservations"] == [] and data["agent_conversations"] == []
