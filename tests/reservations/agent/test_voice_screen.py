"""Call screening endpoint (#259 step 7): POST /voice/screen, which the
Connect Lambda calls before answering."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.reservations.agent.models import AgentConversation
from backend.reservations.screening_models import NumberRule, ScreenedCall, SpamNumber
from backend.services.rate_limit import source_key_for
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.agent.test_voice_bridge import (  # noqa: F401 (fixtures)
    CALLER,
    DIALED,
    HEADERS,
    _enabled,
    line,
    llm,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/voice/screen"


async def _screen(client, caller: str | None = CALLER, dialed: str = DIALED,
                  headers: dict | None = HEADERS):
    return await client.post(URL, json={"dialed": dialed, "caller": caller},
                             headers=headers or {})


def _without_greeting(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "greeting"}


async def _calls(db) -> list[ScreenedCall]:
    return list((await db.execute(
        select(ScreenedCall).order_by(ScreenedCall.created_at)
        .execution_options(populate_existing=True))).scalars().all())


async def test_secret_and_flag_are_enforced(client, db_session, line, monkeypatch):
    for headers in ({}, {"X-Voice-Bridge-Secret": "nope"}):
        r = await client.post(URL, json={"dialed": DIALED, "caller": CALLER},
                              headers=headers)
        assert r.status_code == 404
        r = await client.post(URL, json={"junk": 1}, headers=headers)
        assert r.status_code == 404  # before validation
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", "")
    assert (await _screen(client)).status_code == 404
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", HEADERS["X-Voice-Bridge-Secret"])
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    assert (await _screen(client)).status_code == 404
    assert await _calls(db_session) == []


async def test_default_answer_records_a_screened_call(client, db_session, line, llm):
    r = await _screen(client)
    assert r.status_code == 200, r.text
    assert _without_greeting(r.json()) == {"action": "answer", "reason": "default"}
    assert "automated reservations assistant" in r.json()["greeting"]
    [call] = await _calls(db_session)
    assert (call.company_id, call.location_id) == (COMPANY_ID, LOCATION_ID)
    assert call.caller_key == source_key_for(CALLER)
    assert (call.decision, call.reason) == ("answer", "default")
    # No model call, no conversation: a screened call is not usage.
    assert llm.requests == []
    assert (await db_session.execute(select(AgentConversation))).scalars().all() == []


async def test_blocked_caller(client, db_session, line, llm):
    db_session.add(NumberRule(company_id=COMPANY_ID, location_id=LOCATION_ID,
                              e164=CALLER, action="block"))
    await db_session.commit()
    assert (await _screen(client)).json() == {
        "action": "block", "reason": "block_list", "greeting": ""}
    [call] = await _calls(db_session)
    assert (call.decision, call.reason) == ("block", "block_list")
    assert llm.requests == []
    assert (await db_session.execute(select(AgentConversation))).scalars().all() == []


async def test_company_block_with_a_location_allow_is_answered(client, db_session, line):
    db_session.add_all([
        NumberRule(company_id=COMPANY_ID, location_id=None, e164=CALLER, action="block"),
        NumberRule(company_id=COMPANY_ID, location_id=LOCATION_ID, e164=CALLER,
                   action="allow"),
    ])
    await db_session.commit()
    assert _without_greeting((await _screen(client)).json()) == {
        "action": "answer", "reason": "allow_list"}


async def test_ftc_listed_caller_is_blocked(client, db_session, line):
    today = datetime.now(timezone.utc).date()
    db_session.add(SpamNumber(e164=CALLER, report_date=today - timedelta(days=2),
                              reports=3))
    await db_session.commit()
    assert (await _screen(client)).json() == {"action": "block", "reason": "ftc", "greeting": ""}
    [call] = await _calls(db_session)
    assert call.reason == "ftc"


@pytest.mark.parametrize("caller", [None, "", "anonymous", "Restricted"])
async def test_withheld_caller_is_answered_with_no_key(client, db_session, line, caller):
    r = await _screen(client, caller=caller)
    assert r.status_code == 200, r.text
    assert _without_greeting(r.json()) == {"action": "answer", "reason": "withheld"}
    [call] = await _calls(db_session)
    assert call.caller_key is None and call.reason == "withheld"


async def test_unknown_dialed_number_answers_and_records_nothing(client, db_session, line,
                                                                 llm):
    r = await _screen(client, dialed="+12125550000")
    # Not a location's number: no greeting, the Lambda's disclosing fallback plays.
    assert r.json() == {"action": "answer", "reason": "default", "greeting": ""}
    assert await _calls(db_session) == []
    assert llm.requests == []


async def test_the_answer_greeting_follows_the_disclosure_setting(client, db_session, line):
    from backend.reservations.rules import get_rules, save_rules
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.disclose_automated = False
    rules.persona.greeting = "Thanks for calling Luigi's, how can I help?"
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    await db_session.commit()
    assert (await _screen(client)).json()["greeting"] == (
        "Thanks for calling Luigi's, how can I help?")
    rules.persona.greeting = ""
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    await db_session.commit()
    greeting = (await _screen(client)).json()["greeting"]
    assert greeting and "automated" not in greeting.lower()
