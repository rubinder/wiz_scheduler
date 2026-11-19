"""Voice bridge (#259 step 6): the endpoint the Connect Lambda calls per turn."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sql

from backend.config import settings
from backend.main import app
from backend.models import Location
from backend.reservations.agent import voice
from backend.reservations.agent.core import HANDOFF_TEXT, AgentCore, TurnOutput
from backend.reservations.agent.gate import get_agent_core
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.models import BOOKED, HELD, Reservation
from backend.reservations.rules import get_rules, save_rules
from backend.reservations.voice_number_models import VoiceNumber
from backend.services.rate_limit import source_key_for
from backend.tenant import get_tenant_context
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import ScriptedLlm, reply, text
from tests.reservations.agent.test_core import _day, _hold_script
from tests.reservations.conftest import AGENT_OG_ID, subscribe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/v1/voice/turn"
SECRET = "s3cret-bridge"
DIALED = "+12125550199"
CALLER = "+12125550142"
STAFF = "+12125550111"
HEADERS = {"X-Voice-Bridge-Secret": SECRET}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", SECRET)


@pytest_asyncio.fixture
async def llm(client) -> ScriptedLlm:
    scripted = ScriptedLlm()
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(scripted)
    return scripted


@pytest_asyncio.fixture
async def line(db_session, seeded_floor):
    """A subscribed group whose location answers DIALED."""
    sub = await subscribe(db_session)
    db_session.add(VoiceNumber(company_id=COMPANY_ID, ownership_group_id=AGENT_OG_ID,
                               location_id=LOCATION_ID, e164=DIALED, status="active",
                               connect_phone_number_id="pn-1"))
    await db_session.commit()
    return sub


def _body(seq: int, *, text_: str | None = None, action: str | None = None,
          contact: str = "contact-1", caller: str | None = CALLER,
          timings: dict | None = None) -> dict:
    return {"contact_id": contact, "dialed": DIALED, "caller": caller, "seq": seq,
            "text": text_, "action": action, "timings": timings or {}}


async def _post(client, body: dict, headers: dict | None = HEADERS):
    return await client.post(URL, json=body, headers=headers or {})


async def _conv(db) -> AgentConversation:
    return (await db.execute(
        select(AgentConversation).where(AgentConversation.external_ref == "contact-1")
        .execution_options(populate_existing=True))).scalar_one()


async def _staff(db, phone: str | None) -> None:
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    rules.staff_transfer_number = phone
    await save_rules(db, COMPANY_ID, LOCATION_ID, rules)
    await db.commit()


# -- the secret ---------------------------------------------------------------

async def test_missing_or_wrong_secret_404s_before_validation(client, line, llm,
                                                              monkeypatch):
    for headers in ({}, {"X-Voice-Bridge-Secret": "nope"},
                    {"X-Voice-Bridge-Secret": ""}):
        r = await client.post(URL, json=_body(0), headers=headers)
        assert r.status_code == 404 and r.json()["detail"] == "Not found"
        # A malformed body is still a 404, not a 422 that admits the route.
        r = await client.post(URL, json={"junk": 1}, headers=headers)
        assert r.status_code == 404
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", "")
    assert (await _post(client, _body(0))).status_code == 404
    assert (await client.post(URL, json=_body(0),
                              headers={"X-Voice-Bridge-Secret": ""})).status_code == 404
    monkeypatch.setattr(settings, "VOICE_BRIDGE_SECRET", SECRET)
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", False)
    assert (await _post(client, _body(0))).status_code == 404
    assert llm.requests == []


async def test_secret_compare_is_constant_time(monkeypatch):
    from backend.routers import voice_bridge

    seen = []
    real = voice_bridge.hmac.compare_digest
    monkeypatch.setattr(voice_bridge.hmac, "compare_digest",
                        lambda a, b: seen.append((a, b)) or real(a, b))
    for given in (None, "nope", SECRET):
        try:
            voice_bridge.require_bridge_secret(given)
        except Exception:
            pass
    assert len(seen) == 3  # also when the header is missing


# -- polite closes --------------------------------------------------------------

async def test_unknown_number_closes_without_a_model_call(client, db_session, line,
                                                          llm):
    r = await _post(client, {**_body(0, text_="hi"), "dialed": "+12125550000"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text"] == voice.UNAVAILABLE_TEXT and out["end_call"] is True
    assert out["reason"] == "unknown_number"
    assert llm.requests == []
    assert (await db_session.execute(select(AgentConversation))).scalars().all() == []


async def test_unsubscribed_closes(client, db_session, line, llm):
    line.status = "canceled"
    await db_session.commit()
    out = (await _post(client, _body(0, text_="hi"))).json()
    assert out["text"] == voice.UNAVAILABLE_TEXT and out["end_call"]
    assert llm.requests == []
    assert (await db_session.execute(select(AgentConversation))).scalars().all() == []


async def test_archived_location_closes(client, db_session, line, llm):
    loc = await db_session.get(Location, LOCATION_ID)
    loc.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    out = (await _post(client, _body(0, text_="hi"))).json()
    assert out["text"] == voice.UNAVAILABLE_TEXT and out["end_call"]
    assert llm.requests == []


async def test_spend_cap_closes_a_new_call(client, db_session, line, llm):
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.daily_spend_cap_usd = 1.0
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    db_session.add(AgentConversation(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                     channel="web", status="closed", outcome="booked",
                                     started_at=datetime.now(timezone.utc),
                                     cost_usd=Decimal("2")))
    await db_session.commit()
    out = (await _post(client, _body(0))).json()
    assert out["end_call"] and out["reason"] == "spend_cap"
    assert out["text"] == voice.busy_text(None)
    conv = await _conv(db_session)
    assert conv.status == "closed" and conv.outcome == "blocked"
    assert llm.requests == []


async def test_turn_in_flight_asks_to_repeat(client, db_session, line, llm):
    await _post(client, _body(0))
    conv = await _conv(db_session)
    conv.turn_started_at = datetime.now(timezone.utc)
    await db_session.commit()
    out = (await _post(client, _body(1, text_="hello"))).json()
    assert out["text"] == voice.INFLIGHT_TEXT and out["end_call"] is False
    assert llm.requests == []


async def test_caller_limit(client, line, llm, monkeypatch):
    from backend.services.rate_limit import voice_caller_limiter

    monkeypatch.setattr(voice_caller_limiter, "max_requests", 2)
    for seq in range(2):
        assert (await _post(client, _body(seq))).json()["reason"] != "rate_limited"
    out = (await _post(client, _body(2))).json()
    assert out["reason"] == "rate_limited" and out["end_call"]


# -- a call ---------------------------------------------------------------------

async def test_greeting_creates_the_voice_conversation(client, db_session, line, llm):
    r = await _post(client, _body(0))
    assert r.status_code == 200, r.text
    out = r.json()
    assert "Downtown Store" in out["text"] and out["end_call"] is False
    assert out["expect"] == "text" and out["confirm_prompt"] is None
    conv = await _conv(db_session)
    assert conv.channel == "voice" and conv.caller_number == CALLER
    assert conv.source_key == source_key_for(CALLER)
    assert conv.last_seq == 0
    assert llm.requests == []
    assert get_tenant_context() is None


async def test_withheld_number_is_anonymous(client, db_session, line, llm):
    await _post(client, _body(0, caller=None))
    conv = await _conv(db_session)
    assert conv.caller_number is None
    assert conv.source_key == source_key_for("anonymous")


async def test_booking_by_voice_needs_the_confirm_action(client, db_session, line,
                                                          llm):
    now = datetime.now(timezone.utc)
    await _post(client, _body(0))
    llm.push(*_hold_script(now))
    out = (await _post(client, _body(1, text_="table for two next week"))).json()
    assert out["expect"] == "confirm_hold" and out["end_call"] is False
    assert out["confirm_prompt"].startswith(
        f"Shall I book a table for 2 on {voice.spoken_date(_day(now))} at ")
    assert out["confirm_prompt"].endswith(" pm?")

    # Saying "yes" as free text goes to the model, which cannot book.
    llm.push(reply(text("Please confirm when asked.")))
    out = (await _post(client, _body(2, text_="yes"))).json()
    assert out["expect"] == "confirm_hold"
    res = (await db_session.execute(select(Reservation).execution_options(
        populate_existing=True))).scalar_one()
    assert res.status == HELD

    calls = len(llm.requests)
    out = (await _post(client, _body(3, action="confirm_hold"))).json()
    assert len(llm.requests) == calls
    assert out["end_call"] is True and out["text"].startswith("You're booked")
    res = (await db_session.execute(select(Reservation).execution_options(
        populate_existing=True))).scalar_one()
    assert res.status == BOOKED and res.confirmation_code in out["text"]
    assert (await _conv(db_session)).outcome == "booked"


async def test_duplicate_seq_returns_the_cached_reply(client, db_session, line, llm):
    await _post(client, _body(0))
    llm.push(reply(text("What date would you like?")))
    first = (await _post(client, _body(1, text_="hi"))).json()
    again = (await _post(client, _body(1, text_="hi"))).json()
    stale = (await _post(client, _body(0))).json()
    assert first == again == stale
    assert first["text"] == "What date would you like?"
    assert len(llm.requests) == 1
    conv = await _conv(db_session)
    assert conv.last_seq == 1 and conv.turn_count == 1
    # Lex may skip a number: a later seq is a new turn.
    llm.push(reply(text("For how many?")))
    assert (await _post(client, _body(5, text_="friday"))).json()["text"] == \
        "For how many?"


async def test_handoff_transfers_to_staff(client, db_session, line, llm):
    await _staff(db_session, STAFF)
    await _post(client, _body(0))
    llm.push(reply(text("x"), stop="refusal"))
    out = (await _post(client, _body(1, text_="hi"))).json()
    assert out["transfer_to"] == STAFF and out["end_call"] is False
    assert out["text"] == HANDOFF_TEXT and out["reason"] == "refusal"
    assert (await _conv(db_session)).outcome == "handed_off"


async def test_handoff_without_a_staff_number_ends_the_call(client, db_session, line,
                                                            llm):
    await _staff(db_session, None)
    await _post(client, _body(0))
    llm.push(reply(text("x"), stop="refusal"))
    out = (await _post(client, _body(1, text_="hi"))).json()
    assert out["transfer_to"] is None and out["end_call"] is True
    assert out["text"] == voice.NO_TRANSFER_TEXT


async def test_timings_are_recorded_as_latency_stages(client, db_session, line, llm):
    await _post(client, _body(0))
    llm.push(reply(text("Sure.")))
    await _post(client, _body(1, text_="hi", timings={"asr_ms": 420, "tts_ms": 180}))
    events = (await db_session.execute(
        select(AgentEvent).order_by(AgentEvent.seq)
        .execution_options(populate_existing=True))).scalars().all()
    assert [e.type for e in events] == ["user_turn", "assistant_turn"]
    assert events[0].latency_ms == {"asr": 420, "tts": 180}
    assert set(events[1].latency_ms) == {"llm"}


async def test_closed_call_answers_goodbye(client, db_session, line, llm):
    await _post(client, _body(0))
    conv = await _conv(db_session)
    conv.status = "closed"
    await db_session.commit()
    out = (await _post(client, _body(1, text_="hi"))).json()
    assert out["end_call"] and out["text"] == voice.ENDED_TEXT
    assert llm.requests == []


# -- fix round 1: seq in the claim, replays, actions --------------------------------

@pytest.fixture
def no_wait(monkeypatch):
    from backend.reservations.agent import voice_turn

    async def _noop(_s):
        return None
    monkeypatch.setattr(voice_turn, "_sleep", _noop)
    return voice_turn


async def test_claim_takes_the_seq_once(db_session, line):
    from backend.reservations.agent import recording

    conv = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID,
                                              "voice")
    await db_session.commit()
    assert await recording.claim_turn(db_session, conv, seq=3, seen_seq=None) == \
        "claimed"
    assert conv.last_seq == 3 and conv.last_reply is None
    conv.turn_started_at = None  # the turn ended without storing its reply
    await db_session.commit()
    assert await recording.claim_turn(db_session, conv, seq=3, seen_seq=3) == "seen"
    assert await recording.claim_turn(db_session, conv, seq=2, seen_seq=3) == "seen"
    # Judged against an older read: another turn ran meanwhile.
    assert await recording.claim_turn(db_session, conv, seq=4, seen_seq=1) == "moved"
    assert await recording.claim_turn(db_session, conv, seq=4, seen_seq=3) == \
        "claimed"


async def test_retry_after_the_mark_clears_does_not_run_the_core_again(
        client, db_session, line, llm, no_wait):
    await _post(client, _body(0))
    # Ordering: turn 1 was claimed and its in-flight mark cleared, but its
    # reply was never stored (the process died between the two commits).
    conv = await _conv(db_session)
    conv.last_seq, conv.last_reply, conv.turn_started_at = 1, None, None
    await db_session.commit()
    out = (await _post(client, _body(1, text_="table for two"))).json()
    assert out["text"] == voice.INFLIGHT_TEXT and out["end_call"] is False
    assert llm.requests == []
    assert (await _conv(db_session)).turn_count == 0


async def test_retry_while_running_gets_the_reply_once_stored(
        client, db_session, line, llm, no_wait, monkeypatch):
    await _post(client, _body(0))
    conv = await _conv(db_session)
    conv.last_seq, conv.last_reply = 1, None
    conv.turn_started_at = datetime.now(timezone.utc)
    await db_session.commit()
    stored = voice.greeting("What date would you like?")
    polls = []

    async def _store_on_second_poll(_s):
        polls.append(1)
        if len(polls) == 2:
            c = await _conv(db_session)
            c.last_reply, c.turn_started_at = stored, None
            await db_session.commit()
    monkeypatch.setattr(no_wait, "_sleep", _store_on_second_poll)
    out = (await _post(client, _body(1, text_="hi"))).json()
    assert out == stored and llm.requests == []
    assert len(polls) == 2


async def test_confirm_needs_the_matching_question(client, db_session, line, llm):
    now = datetime.now(timezone.utc)
    await _post(client, _body(0))
    llm.push(*_hold_script(now))
    held = (await _post(client, _body(1, text_="table for two next week"))).json()
    assert held["expect"] == "confirm_hold"
    # A cancel confirmation does not answer a booking question.
    out = (await _post(client, _body(2, action="confirm_cancel"))).json()
    assert out["text"] == voice.NOT_CAUGHT_TEXT and out["expect"] == "confirm_hold"
    assert out["confirm_prompt"] == held["confirm_prompt"]
    # The hold is staged, but the last question was not "shall I book?".
    conv = await _conv(db_session)
    conv.last_reply = {**held, "expect": "text", "confirm_prompt": None}
    await db_session.commit()
    calls = len(llm.requests)
    out = (await _post(client, _body(3, action="confirm_hold"))).json()
    assert out["text"] == voice.NOT_CAUGHT_TEXT and out["expect"] == "text"
    assert out["end_call"] is False and len(llm.requests) == calls
    res = (await db_session.execute(select(Reservation).execution_options(
        populate_existing=True))).scalar_one()
    assert res.status == HELD
    assert (await _conv(db_session)).turn_count == 1  # nothing was claimed


async def test_action_on_the_first_turn_is_not_run(client, db_session, line, llm):
    out = (await _post(client, _body(0, action="confirm_hold"))).json()
    assert out["text"] == voice.NOT_CAUGHT_TEXT and out["end_call"] is False


async def test_replays_are_served_before_the_limit(client, line, llm, monkeypatch):
    from backend.services.rate_limit import voice_caller_limiter

    monkeypatch.setattr(voice_caller_limiter, "max_requests", 2)
    first = (await _post(client, _body(0))).json()
    llm.push(reply(text("For how many?")))
    second = (await _post(client, _body(1, text_="hi"))).json()
    assert (await _post(client, _body(1, text_="hi"))).json() == second
    assert (await _post(client, _body(0))).json() == second  # stale: the latest
    out = (await _post(client, _body(2, text_="four"))).json()
    assert out["reason"] == "rate_limited" and out["text"] == voice.RATE_LIMITED_TEXT
    assert first["reason"] is None


async def test_transport_timing_is_a_stage(client, db_session, line, llm):
    await _post(client, _body(0))
    llm.push(reply(text("Sure.")))
    await _post(client, _body(1, text_="hi", timings={"transport_ms": 35}))
    event = (await db_session.execute(
        select(AgentEvent).order_by(AgentEvent.seq).limit(1)
        .execution_options(populate_existing=True))).scalar_one()
    assert event.latency_ms == {"transport": 35}


# -- spoken prompts -------------------------------------------------------------

async def test_spoken_date_and_time():
    assert voice.spoken_date("2026-11-21") == "Saturday, November 21st"
    assert voice.spoken_date("2026-11-12") == "Thursday, November 12th"
    assert voice.spoken_date("2026-11-22") == "Sunday, November 22nd"
    assert voice.spoken_time("19:00") == "7 pm"
    assert voice.spoken_time("19:30") == "7:30 pm"
    assert voice.spoken_time("12:00") == "12 pm"
    assert voice.spoken_time("00:15") == "12:15 am"


async def test_confirm_prompts_from_structured_data():
    slot = {"party_size": 4, "local_date": "2026-11-20", "local_time": "19:00"}
    hold = TurnOutput("x", "confirm_hold", {**slot, "status": "held"}, False, None)
    assert voice.confirm_prompt(hold) == \
        "Shall I book a table for 4 on Friday, November 20th at 7 pm?"
    pending = TurnOutput("x", "confirm_hold", {**slot, "status": "pending_approval"},
                         False, None)
    assert voice.confirm_prompt(pending).startswith("Shall I request a table for 4")
    modify = TurnOutput("x", "confirm_modify", None, False, None,
                        {"kind": "modify", "current": slot,
                         "proposed": {**slot, "local_time": "20:30"}})
    assert voice.confirm_prompt(modify) == \
        "Shall I change your booking to a table for 4 on Friday, November 20th at 8:30 pm?"
    cancel = TurnOutput("x", "confirm_cancel", None, False, None,
                        {"kind": "cancel", "current": slot, "proposed": None})
    assert voice.confirm_prompt(cancel) == \
        "Shall I cancel your booking for 4 on Friday, November 20th at 7 pm?"
    assert voice.confirm_prompt(TurnOutput("x", "text", None, False, None)) is None


# -- erasure during a turn ----------------------------------------------------------

async def test_erasure_during_a_turn_is_not_written_back(client, db_session, line, llm):
    """The diner's data is erased while their turn waits on the model: the
    conversation is closed, and the turn's reply and later events must not
    put the diner's words back (remember_reply, record_event)."""
    from backend.reservations.diner_data import erase_diner_data
    from tests.conftest import get_test_session_factory

    await _post(client, _body(0))
    counts: dict = {}

    async def erase_then_answer(_request):
        async with get_test_session_factory()() as eraser:
            counts.update(await erase_diner_data(eraser, COMPANY_ID, LOCATION_ID,
                                                 CALLER))
            await eraser.commit()
        return reply(text("Ada, your table at seven is ready to book."))
    llm.push(erase_then_answer)

    out = (await _post(client, _body(1, text_="table for two, I'm Ada"))).json()
    assert counts["conversations_closed_in_flight"] == 1
    assert out["text"]  # the caller still hears the answer
    conv = await _conv(db_session)
    assert conv.status == "closed"
    assert conv.last_reply is None
    events = (await db_session.execute(
        select(AgentEvent).where(AgentEvent.conversation_id == conv.id)
        .order_by(AgentEvent.seq).execution_options(populate_existing=True)
    )).scalars().all()
    after = [e for e in events if e.type != "user_turn"]
    assert after and all("Ada" not in str(e.payload) for e in after)
    # The model call is still recorded and metered, without its words.
    assert any(e.cost_usd > 0 for e in after)
    assert conv.cost_usd == sum((e.cost_usd for e in events), Decimal(0))
    # A retry of the erased turn is not answered from a stored reply.
    again = (await _post(client, _body(1, text_="table for two, I'm Ada"))).json()
    assert "Ada" not in again["text"]


# -- toll fraud: transfers ------------------------------------------------------------

async def test_transfers_are_capped_per_location_per_day(client, db_session, line, llm,
                                                          monkeypatch):
    """Past VOICE_TRANSFERS_PER_DAY transfers today, a handoff closes the
    call with the call-us line instead of dialling out."""
    monkeypatch.setattr(settings, "VOICE_TRANSFERS_PER_DAY", 1)
    await _staff(db_session, STAFF)
    for contact, expect in (("contact-1", STAFF), ("contact-2", None)):
        await _post(client, _body(0, contact=contact))
        llm.push(reply(text("x"), stop="refusal"))
        out = (await _post(client, _body(1, text_="hi", contact=contact))).json()
        assert out["transfer_to"] == expect, contact
    assert out["end_call"] is True and out["text"] == voice.NO_TRANSFER_TEXT
    assert out["reason"] == "transfer_cap"


async def test_a_disallowed_stored_transfer_number_is_never_dialled(
        client, db_session, line, llm):
    """A number saved before the screening (or the AI host's own number) is
    refused at transfer time too."""
    await _staff(db_session, STAFF)
    for i, number in enumerate(("+19005550100", DIALED, "+442071234567")):
        await db_session.execute(sql(
            "UPDATE reservation_rules SET rules = jsonb_set(rules, "
            "'{staff_transfer_number}', to_jsonb(CAST(:n AS text))) "
            "WHERE location_id = :loc"), {"n": number, "loc": LOCATION_ID})
        await db_session.commit()
        contact = f"contact-{i + 10}"
        await _post(client, _body(0, contact=contact))
        llm.push(reply(text("x"), stop="refusal"))
        out = (await _post(client, _body(1, text_="hi", contact=contact))).json()
        assert out["transfer_to"] is None and out["end_call"] is True, number
        assert out["reason"] == "transfer_refused"


async def test_failed_confirm_style_read_falls_back_and_the_turn_still_works(
        client, db_session, line, llm, monkeypatch):
    from backend.reservations.agent import voice_turn

    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    rules.voice.voice_confirm_style = "statement"
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, rules)
    await db_session.commit()

    async def _broken(db, company_id, location_id):
        await db.execute(sql("select * from no_such_table_259"))  # aborts the txn

    now = datetime.now(timezone.utc)
    await _post(client, _body(0))
    monkeypatch.setattr(voice_turn, "get_rules", _broken)
    llm.push(*_hold_script(now))
    r = await _post(client, _body(1, text_="table for two next week"))
    assert r.status_code == 200
    out = r.json()
    assert out["expect"] == "confirm_hold"
    assert out["confirm_prompt"].startswith("Shall I book a table for 2")  # "question"
    assert (await _conv(db_session)).last_seq == 1
