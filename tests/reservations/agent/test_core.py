import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import anthropic
import httpx
import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.reservations import booking
from backend.reservations.agent.core import HANDOFF_TEXT, AgentCore, TurnInput
from backend.reservations.agent.models import AgentEvent
from backend.reservations.agent.tools import TOOLS
from backend.reservations.models import BOOKED, CANCELLED, HELD, PENDING, Reservation
from backend.reservations.rules import get_rules, save_rules
from tests.conftest import COMPANY_ID, LOCATION_ID
from tests.reservations.agent.fakes import (
    ScriptedLlm,
    last_tool_result,
    reply,
    text,
    thinking,
    tool_use,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
PHONE = "+12125550100"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day(now: datetime) -> str:
    return (now.astimezone(NY).date() + timedelta(days=7)).isoformat()


def say(t: str) -> TurnInput:
    return TurnInput(text=t, action=None, channel="web")


def press(action: str) -> TurnInput:
    return TurnInput(text=None, action=action, channel="web")  # type: ignore[arg-type]


def _hold_script(now: datetime, party: int = 2, pick: int = 3) -> list:
    """find_slots -> hold_slot (an offered option) -> a text reply."""
    def hold(request):
        opts = last_tool_result(request)["options"]
        return reply(tool_use("tu_2", "hold_slot", {
            "start_iso": opts[pick]["start_iso"], "party_size": party,
            "name": "Ada", "phone": PHONE}), stop="tool_use")

    return [
        reply(thinking("look up slots"), text("Let me check."),
              tool_use("tu_1", "find_slots", {"party_size": party, "date": _day(now),
                                              "earliest": None, "latest": None}),
              stop="tool_use"),
        hold,
        reply(text("I'm holding that for you. Press Confirm to book it.")),
    ]


async def _held(db, conv, now, party=2):
    llm = ScriptedLlm(*_hold_script(now, party))
    core = AgentCore(llm)
    out = await core.handle_turn(db, conv, say("Table for two next week"), now=now)
    return core, llm, out


async def _events(db, conv) -> list[AgentEvent]:
    return list((await db.execute(
        select(AgentEvent).where(AgentEvent.conversation_id == conv.id)
        .order_by(AgentEvent.seq))).scalars())


async def _res(db, res_id) -> Reservation:
    return (await db.execute(select(Reservation).where(Reservation.id == res_id)
                             .execution_options(populate_existing=True))).scalar_one()


async def test_full_booking_path(db_session, web_conv):
    now = _now()
    core, llm, out = await _held(db_session, web_conv, now)
    assert out.expect == "confirm_hold" and not out.ended and out.handoff is None
    assert out.reply_text == "I'm holding that for you. Press Confirm to book it."
    assert out.hold is not None and out.hold["status"] == "held"
    hold_id = web_conv.held_reservation_id
    assert hold_id and out.hold["hold_id"] == hold_id
    assert (await _res(db_session, hold_id)).status == HELD

    # The user message carries the local "now"; tools are passed every call.
    first = llm.requests[0]
    local = now.astimezone(NY)
    assert first["messages"] == [{"role": "user", "content":
        f"[Now: {local.date().isoformat()} {local:%H:%M}, {local:%A}] "
        "Table for two next week"}]
    assert all(r["tools"] is TOOLS for r in llm.requests)
    assert len(llm.requests) == 3

    out = await core.handle_turn(db_session, web_conv, press("confirm_hold"), now=now)
    assert len(llm.requests) == 3  # the action never reaches the model
    res = await _res(db_session, hold_id)
    assert res.status == BOOKED and res.confirmation_code
    assert res.confirmation_code in out.reply_text
    assert out.reply_text.startswith("You're booked")
    assert out.ended and out.expect == "text" and out.hold is None
    assert (web_conv.outcome, web_conv.status) == ("booked", "closed")
    assert web_conv.held_reservation_id is None and web_conv.reservation_id == hold_id

    types = [e.type for e in await _events(db_session, web_conv)]
    assert types == ["user_turn", "assistant_turn", "tool_call", "tool_result",
                     "assistant_turn", "tool_call", "tool_result",
                     "assistant_turn", "action"]


async def test_typed_yes_does_not_book(db_session, web_conv, monkeypatch):
    now = _now()
    core, llm, _ = await _held(db_session, web_conv, now)
    called = []

    async def spy(*a, **k):
        called.append(a)
        raise AssertionError("confirm must not be called")

    monkeypatch.setattr(booking, "confirm", spy)
    llm.push(reply(text("Please press Confirm to book it.")))
    out = await core.handle_turn(db_session, web_conv, say("yes"), now=now)
    assert called == []
    assert out.expect == "confirm_hold" and out.hold is not None and not out.ended
    assert (await _res(db_session, web_conv.held_reservation_id)).status == HELD
    assert web_conv.status == "open"


async def test_hold_expired_then_confirm(db_session, web_conv):
    now = _now()
    core, llm, _ = await _held(db_session, web_conv, now)
    hold_id = web_conv.held_reservation_id
    later = now + timedelta(minutes=10)  # hold_timeout_minutes is 5
    out = await core.handle_turn(db_session, web_conv, press("confirm_hold"), now=later)
    assert "expired" in out.reply_text
    assert out.expect == "text" and not out.ended and out.hold is None
    assert web_conv.status == "open" and web_conv.outcome is None
    assert web_conv.held_reservation_id is None
    assert (await _res(db_session, hold_id)).status != BOOKED
    # The model continues on the next turn and sees the action note.
    llm.push(reply(text("Sure, what time?")))
    out = await core.handle_turn(db_session, web_conv, say("another time"), now=later)
    assert out.reply_text == "Sure, what time?"
    notes = [m for m in llm.requests[-1]["messages"] if m["role"] == "user"]
    assert "hold_expired" in json.dumps(notes)


async def test_confirm_with_nothing_held_skips_model(db_session, web_conv):
    llm = ScriptedLlm()
    out = await AgentCore(llm).handle_turn(db_session, web_conv, press("confirm_hold"))
    assert llm.requests == []
    assert "nothing to confirm" in out.reply_text
    assert out.expect == "text" and not out.ended


async def test_decline_hold_releases_it(db_session, web_conv):
    now = _now()
    core, llm, _ = await _held(db_session, web_conv, now)
    hold_id = web_conv.held_reservation_id
    out = await core.handle_turn(db_session, web_conv, press("decline_hold"), now=now)
    assert (await _res(db_session, hold_id)).status == CANCELLED
    assert web_conv.held_reservation_id is None
    assert out.expect == "text" and out.hold is None and not out.ended
    assert len(llm.requests) == 3


async def test_pending_request_keeps_conversation_open(db_session, web_conv):
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    await save_rules(db_session, COMPANY_ID, LOCATION_ID,
                     rules.model_copy(update={"max_self_book_party": 4}))
    now = _now()
    core, _, out = await _held(db_session, web_conv, now, party=5)
    assert out.hold["status"] == "pending_approval"
    hold_id = web_conv.held_reservation_id
    out = await core.handle_turn(db_session, web_conv, press("confirm_hold"), now=now)
    res = await _res(db_session, hold_id)
    assert res.status == PENDING and res.confirmation_code in out.reply_text
    assert "restaurant will confirm" in out.reply_text
    assert not out.ended and web_conv.status == "open"
    assert web_conv.held_reservation_id is None and web_conv.reservation_id == hold_id


async def test_confirm_cancel(db_session, web_conv):
    now = _now()
    llm = ScriptedLlm()
    core = AgentCore(llm)
    out = await core.handle_turn(db_session, web_conv, press("confirm_cancel"), now=now)
    assert out.expect == "confirmation_code" and not out.ended

    held = await booking.hold(db_session, COMPANY_ID, LOCATION_ID, 2,
                              datetime.fromisoformat(f"{_day(now)}T19:00").replace(tzinfo=NY),
                              source="web_agent", now=now,
                              diner=booking.DinerContact(name="Ada", phone=PHONE))
    res = await booking.confirm(db_session, COMPANY_ID, held.id, now=now)
    llm.push(
        reply(tool_use("tu_1", "lookup_booking", {"confirmation_code": res.confirmation_code,
                                                  "phone": PHONE}), stop="tool_use"),
        reply(tool_use("tu_2", "cancel_booking", {}), stop="tool_use"),
        reply(text("Press Confirm to cancel it.")),
    )
    out = await core.handle_turn(db_session, web_conv, say("cancel my booking"), now=now)
    assert out.expect == "confirm_cancel"
    assert (await _res(db_session, res.id)).status == BOOKED
    out = await core.handle_turn(db_session, web_conv, press("confirm_cancel"), now=now)
    assert (await _res(db_session, res.id)).status == CANCELLED
    assert out.ended and web_conv.outcome == "cancelled"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
async def test_refusal_or_max_tokens_hands_off(db_session, web_conv, stop):
    llm = ScriptedLlm(reply(text("partial"), stop=stop))
    out = await AgentCore(llm).handle_turn(db_session, web_conv, say("hi"))
    assert out.reply_text == HANDOFF_TEXT and out.ended
    assert out.handoff == {"reason": stop, "phone": None}
    assert web_conv.outcome == "handed_off"
    events = await _events(db_session, web_conv)
    assert events[-1].type == "handoff" and events[-1].payload == {"reason": stop}


async def test_sdk_error_hands_off_without_raising(db_session, web_conv):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.test"))
    out = await AgentCore(ScriptedLlm(err)).handle_turn(db_session, web_conv, say("hi"))
    assert out.reply_text == HANDOFF_TEXT and out.ended
    assert web_conv.outcome == "handed_off"
    types = [e.type for e in await _events(db_session, web_conv)]
    assert types == ["user_turn", "error", "handoff"]


async def test_unexpected_error_fails_without_raising(db_session, web_conv):
    out = await AgentCore(ScriptedLlm(RuntimeError("boom"))).handle_turn(
        db_session, web_conv, say("hi"))
    assert out.reply_text == HANDOFF_TEXT and out.ended
    assert (web_conv.outcome, web_conv.status) == ("failed", "closed")
    events = await _events(db_session, web_conv)
    # The turn's savepoint was rolled back: only the error is left.
    assert [e.type for e in events] == ["error"]
    assert web_conv.turn_count == 0


async def test_round_cap_hands_off(db_session, web_conv, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATION_AGENT_MAX_TOOL_ROUNDS", 2)
    now = _now()
    find = {"party_size": 2, "date": _day(now), "earliest": None, "latest": None}
    llm = ScriptedLlm(*[reply(tool_use(f"tu_{i}", "find_slots", find), stop="tool_use")
                        for i in range(2)])
    out = await AgentCore(llm).handle_turn(db_session, web_conv, say("hi"), now=now)
    assert len(llm.requests) == 2 and out.reply_text == HANDOFF_TEXT
    assert out.handoff["reason"] == "round_cap" and web_conv.outcome == "handed_off"


async def test_turn_cap_abandons(db_session, web_conv, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATION_AGENT_MAX_TURNS", 1)
    llm = ScriptedLlm(reply(text("Hello!")))
    core = AgentCore(llm)
    await core.handle_turn(db_session, web_conv, say("hi"))
    out = await core.handle_turn(db_session, web_conv, say("again"))
    assert len(llm.requests) == 1 and out.ended
    assert web_conv.outcome == "abandoned"


async def test_all_tool_results_in_one_user_message(db_session, web_conv):
    now = _now()
    find = {"party_size": 2, "date": _day(now), "earliest": None, "latest": None}
    llm = ScriptedLlm(
        reply(tool_use("a", "find_slots", find), tool_use("b", "restaurant_info", {}),
              tool_use("c", "hold_slot", {"start_iso": "2000-01-01T19:00:00+00:00",
                                         "party_size": 2, "name": "A", "phone": PHONE}),
              stop="tool_use"),
        reply(text("ok")))
    await AgentCore(llm).handle_turn(db_session, web_conv, say("hi"), now=now)
    msg = llm.requests[1]["messages"][-1]
    assert msg["role"] == "user"
    assert [b["tool_use_id"] for b in msg["content"]] == ["a", "b", "c"]
    assert msg["content"][2]["is_error"] is True
    assert "is_error" not in msg["content"][0]
    events = await _events(db_session, web_conv)
    assert [e.type for e in events].count("tool_result") == 1
    assert all("llm" in e.latency_ms for e in events if e.type == "assistant_turn")
    assert all("tools" in e.latency_ms for e in events if e.type == "tool_result")


async def test_history_replays_assistant_blocks_exactly(db_session, web_conv):
    now = _now()
    core, llm, _ = await _held(db_session, web_conv, now)
    llm.push(reply(text("Anything else?")))
    await core.handle_turn(db_session, web_conv, say("thanks"), now=now)
    turn1 = llm.requests[2]["messages"] + [
        {"role": "assistant", "content": [text("I'm holding that for you. "
                                               "Press Confirm to book it.")]}]
    replayed = llm.requests[3]["messages"]
    assert json.dumps(replayed[:len(turn1)]) == json.dumps(turn1)
    assert replayed[1]["content"][0] == thinking("look up slots")
    assert replayed[-1]["role"] == "user" and replayed[-1]["content"].endswith("thanks")


async def test_system_prompt_is_stable_across_turns(db_session, web_conv):
    llm = ScriptedLlm(reply(text("Hi!")), reply(text("Sure.")))
    core = AgentCore(llm)
    await core.handle_turn(db_session, web_conv, say("hi"))
    await core.handle_turn(db_session, web_conv, say("hello again"),
                           now=_now() + timedelta(minutes=3))
    a, b = (r["system"] for r in llm.requests)
    assert a == b
    assert "find_slots" in a
    assert _now().astimezone(NY).date().isoformat() not in a  # no timestamps


async def test_conversation_cost_equals_sum_of_events(db_session, web_conv):
    now = _now()
    await _held(db_session, web_conv, now)
    total = (await db_session.execute(
        select(func.sum(AgentEvent.cost_usd))
        .where(AgentEvent.conversation_id == web_conv.id))).scalar_one()
    await db_session.refresh(web_conv)
    assert total > Decimal(0)
    assert web_conv.cost_usd == total
    usage = [e for e in await _events(db_session, web_conv) if e.type == "assistant_turn"]
    assert len(usage) == 3 and all(e.cache_read_tokens == 900 for e in usage)


async def test_anthropic_llm_request_and_conversion():
    from anthropic.types import Message

    from backend.reservations.agent.llm import AnthropicLlm

    sent = {}

    class _Messages:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return Message.model_validate({
                "id": "msg_1", "type": "message", "role": "assistant",
                "model": "claude-haiku-5-5", "stop_reason": "tool_use",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "s1"},
                    {"type": "tool_use", "id": "tu_1", "name": "restaurant_info",
                     "input": {}}],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_read_input_tokens": 7,
                          "cache_creation_input_tokens": 3}})

    class _Client:
        messages = _Messages()

    llm = AnthropicLlm()
    llm._client = _Client()
    out = await llm.create(system="sys", messages=[{"role": "user", "content": "hi"}],
                           tools=TOOLS)
    assert sent["model"] == settings.RESERVATION_AGENT_MODEL == "claude-haiku-5-5"
    assert sent["max_tokens"] == settings.RESERVATION_AGENT_MAX_TOKENS
    assert sent["tool_choice"] == {"type": "auto"}
    assert sent["output_config"] == {"effort": "low"}
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in sent and sent["tools"] is TOOLS
    assert out.stop_reason == "tool_use"
    assert out.content[0] == {"type": "thinking", "thinking": "", "signature": "s1"}
    assert out.content[1]["name"] == "restaurant_info"
    json.dumps(out.content)
    assert out.usage == {"input_tokens": 10, "output_tokens": 5,
                         "cache_read_tokens": 7, "cache_write_tokens": 3}
