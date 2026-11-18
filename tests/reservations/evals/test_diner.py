"""DinerSim's request (with a fake client), ScriptedDiner, and the button
policy. No database, no network."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from backend.reservations.agent.core import TurnOutput
from backend.reservations.evals.diner import (
    DINER_MODEL,
    DONE,
    DinerSim,
    ScriptedDiner,
    diner_messages,
    diner_system_prompt,
)
from backend.reservations.evals.runner import PolicyState, choose_action, visible_text
from backend.reservations.evals.scenarios_io import Scenario, load_scenarios

def _scenario(sid: str, **changes: Any) -> Scenario:
    [s] = load_scenarios(ids=[sid])
    return replace(s, **changes) if changes else s


class FakeMessages:
    def __init__(self, blocks: list[Any]) -> None:
        self.blocks = blocks
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.blocks)


def _client(*blocks: Any) -> Any:
    return SimpleNamespace(messages=FakeMessages(list(blocks)))


def _out(expect: str = "text", hold: dict | None = None,
         proposal: dict | None = None, reply_text: str = "ok") -> TurnOutput:
    return TurnOutput(reply_text, expect, hold, False, None, proposal)  # type: ignore[arg-type]


def _hold(party: int, d: str, t: str) -> dict[str, Any]:
    return {"hold_id": "h1", "party_size": party, "local_date": d, "local_time": t,
            "status": "held"}


# -- DinerSim -------------------------------------------------------------

@pytest.mark.asyncio
async def test_dinersim_request_shape() -> None:
    scenario = _scenario("book_four_friday_7pm")
    client = _client(SimpleNamespace(type="thinking", thinking="hm"),
                     SimpleNamespace(type="text", text="  Table for 4, please. "))
    sim = DinerSim(client=client)
    said = await sim.reply(scenario, [])
    assert said == "Table for 4, please."
    [call] = client.messages.calls
    assert call["model"] == DINER_MODEL == "claude-haiku-5-5"
    assert call["max_tokens"] == 300
    assert call["output_config"] == {"effort": "low"}
    assert "tools" not in call
    assert call["system"] == diner_system_prompt(scenario)
    # The diner speaks first: one opening user message.
    assert [m["role"] for m in call["messages"]] == ["user"]


def test_system_prompt_carries_persona_goal_facts_and_done_rule() -> None:
    scenario = _scenario("change_booking_with_code")
    prompt = diner_system_prompt(scenario)
    assert scenario.diner.persona in prompt
    assert scenario.diner.goal in prompt
    for k, v in scenario.diner.facts.items():
        assert f"{k}: {v}" in prompt
    assert DONE in prompt
    assert "say you don't know" in prompt


def test_messages_map_host_to_user_and_diner_to_assistant() -> None:
    transcript = [("diner", "hi"), ("host", "Hello"), ("diner", "[pressed No]"),
                  ("host", "OK"), ("host", "Anything else?")]
    msgs = diner_messages(transcript)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"] == "hi"
    assert msgs[-1]["content"] == "OK\n\nAnything else?"  # consecutive host lines merged


@pytest.mark.asyncio
async def test_scripted_diner_replays_then_says_done() -> None:
    d = ScriptedDiner(["a", "b"])
    s = _scenario("book_four_friday_7pm")
    assert [await d.reply(s, []), await d.reply(s, []), await d.reply(s, [])] == \
        ["a", "b", DONE]


def test_visible_text_notes_buttons_only() -> None:
    assert visible_text(_out(reply_text="Hi")) == "Hi"
    shown = visible_text(_out("confirm_hold", _hold(4, "2026-12-04", "19:00"),
                              reply_text="Holding it"))
    assert shown.startswith("Holding it\n[buttons shown: Confirm / No")
    assert "4 people on 2026-12-04 at 19:00" in shown


# -- policy ----------------------------------------------------------------

def test_confirm_when_offered_checks_the_hold_against_the_goal() -> None:
    s = _scenario("book_four_friday_7pm")
    st = PolicyState()
    assert choose_action(s, _out("confirm_hold", _hold(4, "2026-12-04", "19:00")), st) \
        == "confirm_hold"
    assert choose_action(s, _out("confirm_hold", _hold(4, "2026-12-04", "20:00")), st) is None
    assert choose_action(s, _out("confirm_hold", _hold(5, "2026-12-04", "19:00")), st) is None
    assert choose_action(s, _out("text"), st) is None


def test_confirm_when_offered_without_expected_booking_always_confirms() -> None:
    s = _scenario("party_of_14_voice")
    assert s.expect.booking is None
    assert choose_action(s, _out("confirm_hold", _hold(2, "2026-12-09", "17:00")),
                         PolicyState()) == "confirm_hold"


def test_never_confirm_presses_nothing() -> None:
    s = _scenario("book_four_friday_7pm", policy="never_confirm")
    st = PolicyState()
    for out in (_out("confirm_hold", _hold(4, "2026-12-04", "19:00")),
                _out("confirm_cancel"), _out("confirm_modify")):
        assert choose_action(s, out, st) is None


def test_decline_first_offer_declines_once() -> None:
    s = _scenario("book_four_friday_7pm", policy="decline_first_offer")
    st = PolicyState()
    out = _out("confirm_hold", _hold(4, "2026-12-04", "19:00"))
    assert choose_action(s, out, st) == "decline_hold"
    assert choose_action(s, out, st) == "confirm_hold"


def test_modify_pressed_only_for_a_change_goal_matching_the_proposal() -> None:
    s = _scenario("change_booking_with_code")
    cur = {"party_size": 2, "local_date": "2026-12-04", "local_time": "19:00"}
    good = {"kind": "modify", "current": cur,
            "proposed": {**cur, "local_time": "20:00"}}
    bad = {"kind": "modify", "current": cur,
           "proposed": {**cur, "local_time": "21:00"}}
    st = PolicyState()
    assert choose_action(s, _out("confirm_modify", proposal=good), st) == "confirm_modify"
    assert choose_action(s, _out("confirm_modify", proposal=bad), st) is None
    # A change goal is not a cancel goal.
    assert choose_action(s, _out("confirm_cancel"), st) is None
    # A booking goal presses neither.
    b = _scenario("book_four_friday_7pm")
    assert choose_action(b, _out("confirm_modify", proposal=good), st) is None
    assert choose_action(b, _out("confirm_cancel"), st) is None


def test_cancel_pressed_for_a_cancel_goal() -> None:
    s = _scenario("cancel_with_wrong_phone")
    assert choose_action(s, _out("confirm_cancel"), PolicyState()) == "confirm_cancel"
    assert choose_action(s, _out("confirm_modify"), PolicyState()) is None
