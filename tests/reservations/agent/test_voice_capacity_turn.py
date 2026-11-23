"""Voice capacity on the call path (decision 103): a company at its call
allocation refuses a new call with the staff transfer, and the company's
default staff number covers locations without their own."""

from datetime import datetime, timezone

import pytest

from backend.reservations.agent import voice
from backend.reservations.agent.core import HANDOFF_TEXT
from backend.reservations.agent.models import AgentConversation
from backend.reservations.rules import get_agent_rules
from backend.reservations.voice_capacity_models import VoiceCompanySettings
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import reply, text
from tests.reservations.agent.test_voice_bridge import (  # noqa: F401 (fixtures)
    STAFF, _body, _conv, _enabled, _post, _staff, line, llm,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

COMPANY_STAFF = "+12125550122"


async def _allocation(db, n: int, default_number: str | None = None) -> None:
    db.add(VoiceCompanySettings(company_id=COMPANY_ID, voice_call_allocation=n,
                                default_staff_transfer_number=default_number))
    await db.commit()


async def _open_call(db) -> None:
    db.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                             channel="voice", status="open", external_ref="other-call",
                             started_at=datetime.now(timezone.utc)))
    await db.commit()


async def test_new_call_at_the_allocation_is_transferred_to_staff(client, db_session,
                                                                  line, llm):
    await _staff(db_session, STAFF)
    await _allocation(db_session, 1)
    await _open_call(db_session)
    out = (await _post(client, _body(0))).json()
    assert out["transfer_to"] == STAFF and out["end_call"] is False
    assert out["text"] == voice.CAPACITY_TEXT and out["reason"] == "capacity"
    conv = await _conv(db_session)
    assert conv.status == "closed" and conv.outcome == "blocked"
    assert llm.requests == []


async def test_new_call_at_the_allocation_without_staff_closes_busy(client, db_session,
                                                                    line, llm):
    await _staff(db_session, None)
    await _allocation(db_session, 1)
    await _open_call(db_session)
    out = (await _post(client, _body(0))).json()
    assert out["end_call"] is True and out["transfer_to"] is None
    assert out["text"] == voice.busy_text(None) and out["reason"] == "capacity"


async def test_below_the_allocation_the_call_is_answered(client, db_session, line, llm):
    await _allocation(db_session, 2)
    await _open_call(db_session)
    out = (await _post(client, _body(0))).json()
    assert out["end_call"] is False and out["reason"] is None
    # Now at 2 of 2: its own next turn still runs.
    llm.push(reply(text("For how many?")))
    out = (await _post(client, _body(1, text_="a table friday"))).json()
    assert out["text"] == "For how many?"


async def test_company_default_number_fills_a_location_without_one(db_session, line):
    await _staff(db_session, None)
    rules, _ = await get_agent_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert rules.staff_transfer_number is None
    await _allocation(db_session, 10, COMPANY_STAFF)
    rules, _ = await get_agent_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert rules.staff_transfer_number == COMPANY_STAFF
    # The location's own number wins.
    await _staff(db_session, STAFF)
    rules, _ = await get_agent_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert rules.staff_transfer_number == STAFF


async def test_handoff_uses_the_company_default_number(client, db_session, line, llm):
    await _staff(db_session, None)
    await _allocation(db_session, 10, COMPANY_STAFF)
    await _post(client, _body(0))
    llm.push(reply(text("x"), stop="refusal"))
    out = (await _post(client, _body(1, text_="hi"))).json()
    assert out["transfer_to"] == COMPANY_STAFF and out["text"] == HANDOFF_TEXT


async def test_spend_cap_busy_line_names_the_company_default(client, db_session, line,
                                                             llm):
    from decimal import Decimal

    from backend.reservations.rules import get_rules, save_rules

    await _allocation(db_session, 10, COMPANY_STAFF)
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.staff_transfer_number = None
    rules.daily_spend_cap_usd = 1.0
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    db_session.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                     channel="web", status="closed", outcome="booked",
                                     started_at=datetime.now(timezone.utc),
                                     cost_usd=Decimal("2")))
    await db_session.commit()
    out = (await _post(client, _body(0))).json()
    assert out["reason"] == "spend_cap"
    assert out["text"] == voice.busy_text(COMPANY_STAFF)
