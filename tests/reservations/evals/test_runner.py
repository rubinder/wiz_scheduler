"""The scenario runner end to end, with a scripted agent model (ScriptedLlm)
and a scripted diner (ScriptedDiner). No network."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.models import AgentConversation
from backend.reservations.evals.diner import DONE, ScriptedDiner
from backend.reservations.evals.runner import (
    ACTION_STEP,
    EMPTY_STAND_IN,
    MAX_DINER_TURNS,
    run_scenario,
    run_suite,
)
from backend.reservations.evals.scenarios_io import Scenario, load_scenarios
from backend.reservations.models import BOOKED, PENDING, Reservation
from tests.conftest import get_test_session_factory
from tests.reservations.agent.fakes import (
    ScriptedLlm,
    last_tool_result,
    reply,
    text,
    tool_use,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ASK = ("Hi, a table for 4 this Friday 4 December at 7pm please. "
       "Dana Whitfield, +12125550142.")


def _scenario(sid: str, **changes: Any) -> Scenario:
    [s] = load_scenarios(ids=[sid])
    return replace(s, **changes) if changes else s


def _hold_script(scenario: Scenario, party: int, date: str, at: str,
                 expire: bool = False) -> list:
    """find_slots at exactly `at` -> hold_slot on that option -> a text reply.
    With `expire`, the hold lapses (held_until moved into the past, on its own
    session: the core committed it) before the reply, so the diner's press
    comes too late."""
    facts = scenario.diner.facts

    def hold(request: dict[str, Any]):
        [opt] = [o for o in last_tool_result(request)["options"] if o["local_time"] == at]
        return reply(tool_use("tu_h", "hold_slot", {
            "start_iso": opt["start_iso"], "party_size": party,
            "name": facts["name"], "phone": facts["phone"]}), stop="tool_use")

    async def offer(request: dict[str, Any]):
        if expire:
            hold_id = last_tool_result(request)["hold_id"]
            async with get_test_session_factory()() as db:
                await db.execute(update(Reservation).where(Reservation.id == hold_id)
                                 .values(held_until=scenario.now - timedelta(minutes=1)))
                await db.commit()
        return reply(text(f"I'm holding {at} for {party}. Press Confirm to book it."))

    return [
        reply(tool_use("tu_f", "find_slots", {"party_size": party, "date": date,
                                              "earliest": at, "latest": at}),
              stop="tool_use"),
        hold,
        offer,
    ]


async def _conv(conv_id: str) -> AgentConversation:
    async with get_test_session_factory()() as db:
        return (await db.execute(select(AgentConversation)
                                 .where(AgentConversation.id == conv_id))).scalar_one()


async def _rows(conv_id: str) -> list[Reservation]:
    async with get_test_session_factory()() as db:
        conv = (await db.execute(select(AgentConversation)
                                 .where(AgentConversation.id == conv_id))).scalar_one()
        return list((await db.execute(select(Reservation).where(
            Reservation.location_id == conv.location_id))).scalars().all())


async def test_scripted_booking_passes_the_checker() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario,
                             core=AgentCore(llm), diner=ScriptedDiner([ASK]))
    assert out.check.failures == []
    assert out.check.passed is True
    assert out.scenario_id == "book_four_friday_7pm"
    assert out.stop == "action"
    assert out.diner_turns == 1
    # The diner saw the reply and the buttons, never the tool calls.
    assert out.transcript[0] == ("diner", ASK)
    assert "[buttons shown:" in out.transcript[1][1]
    assert "find_slots" not in str(out.transcript)
    assert out.transcript[2] == ("diner", "[pressed Confirm]")
    assert out.presses == [("confirm_hold", "action")]
    # One timing per diner turn: the typed line and the press.
    m = out.check.metrics
    assert len(m["turn_ms"]) == 2 and all(ms > 0 for ms in m["turn_ms"])
    assert m["turn_p50_ms"] == min(m["turn_ms"])
    assert m["turn_p95_ms"] == max(m["turn_ms"])
    assert m["book_goal"] is True
    # The press runs ACTION_STEP after the text turn that raised it.
    [booked] = [r for r in await _rows(out.conversation_id) if r.status == BOOKED]
    assert booked.updated_at == scenario.now + ACTION_STEP
    conv = await _conv(out.conversation_id)
    assert conv.outcome == "booked"
    assert conv.turn_count == 2  # the text turn and the button press, both claimed
    assert conv.turn_started_at is None


async def test_never_confirm_leaves_no_booking() -> None:
    scenario = _scenario("book_four_friday_7pm", policy="never_confirm")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK, DONE]))
    assert out.stop == "done"
    assert out.check.passed is False
    assert not any(r.status in (BOOKED, PENDING) for r in await _rows(out.conversation_id))
    conv = await _conv(out.conversation_id)
    # Closed after the loop the way the turn cap closes it; the hold released.
    assert conv.status == "closed"
    assert conv.outcome == "abandoned"
    assert all(r.status != "held" for r in await _rows(out.conversation_id))


async def test_a_hold_that_misses_the_goal_is_not_confirmed() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "20:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK, DONE]))
    assert out.stop == "done"
    assert not any(r.status == BOOKED for r in await _rows(out.conversation_id))
    assert out.check.passed is False


async def test_decline_first_offer_then_confirms() -> None:
    scenario = _scenario("book_four_friday_7pm", policy="decline_first_offer")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"),
                      *_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK, "Sorry, yes, 7pm after all."]))
    assert ("diner", "[pressed No]") in out.transcript
    assert out.diner_turns == 2
    assert out.check.failures == []


async def test_turn_cap_ends_the_run() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(*[reply(text("Could you tell me more?"))
                        for _ in range(MAX_DINER_TURNS)])
    diner = ScriptedDiner(["hmm"] * (MAX_DINER_TURNS + 5))
    out = await run_scenario(get_test_session_factory(), scenario,
                             core=AgentCore(llm), diner=diner)
    assert MAX_DINER_TURNS == 20
    assert out.stop == "turn_cap"
    assert out.diner_turns == MAX_DINER_TURNS
    assert len(llm.requests) == MAX_DINER_TURNS
    conv = await _conv(out.conversation_id)
    assert conv.turn_count == MAX_DINER_TURNS
    assert (conv.status, conv.outcome) == ("closed", "abandoned")
    # `now` advances 30 s per diner turn from scenario.now (10:00 in New York):
    # the third turn is at 10:01.
    first = llm.requests[0]["messages"][-1]["content"][0]["text"]
    third = llm.requests[2]["messages"][-1]["content"][0]["text"]
    assert "2026-12-01 10:00" in first
    assert "2026-12-01 10:01" in third


async def test_done_ends_the_run() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(reply(text("Hello! How can I help?")))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner(["hi", DONE, "never sent"]))
    assert out.stop == "done"
    assert out.diner_turns == 1
    assert len(llm.requests) == 1
    assert out.transcript == [("diner", "hi"), ("host", "Hello! How can I help?")]
    conv = await _conv(out.conversation_id)
    assert (conv.status, conv.outcome) == ("closed", "abandoned")


async def test_text_before_done_is_sent_then_the_run_stops() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(reply(text("Hello!")), reply(text("Bye!")))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner(["hi", f"Never mind, bye. {DONE}",
                                                  "never sent"]))
    assert out.stop == "done"
    assert out.diner_turns == 2
    assert len(llm.requests) == 2
    assert out.transcript[-2:] == [("diner", "Never mind, bye."), ("host", "Bye!")]


async def test_one_empty_reply_is_a_repeat_request_and_two_stop_the_run() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(reply(text("Hello!")), reply(text("Tell me more.")))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner(["", "hi", "  ", "never sent"]))
    assert out.stop == "diner_empty"
    assert out.check.passed is False
    assert "run stopped: diner_empty" in out.check.failures
    assert out.diner_turns == 2
    assert [line for who, line in out.transcript if who == "diner"] == \
        [EMPTY_STAND_IN, "hi"]


async def test_a_failed_confirm_continues_and_the_diner_picks_again() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00", expire=True),
                      *_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK, "Yes, 7pm again please."]))
    assert out.presses == [("confirm_hold", "action_failed"), ("confirm_hold", "action")]
    assert out.stop == "action"
    assert out.diner_turns == 2
    assert "expired" in out.transcript[3][1]
    assert out.check.failures == []


async def test_submitted_request_is_closed_as_handed_off() -> None:
    scenario = _scenario("party_of_14_web")
    llm = ScriptedLlm(*_hold_script(scenario, 14, "2026-12-05", "18:00"))
    out = await run_scenario(
        get_test_session_factory(), scenario, core=AgentCore(llm),
        diner=ScriptedDiner(["14 people Saturday 5 December at 6pm, Dana Whitfield "
                             "+12125550142, team dinner."]))
    assert out.stop == "action"
    assert out.check.failures == []
    conv = await _conv(out.conversation_id)
    assert (conv.status, conv.outcome) == ("closed", "handed_off")


async def test_now_override_moves_the_run() -> None:
    scenario = _scenario("book_four_friday_7pm")
    later = scenario.now.replace(hour=16)
    llm = ScriptedLlm(reply(text("Hello!")))
    await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                       diner=ScriptedDiner(["hi", DONE]), now_override=later)
    assert "2026-12-01 11:00" in llm.requests[0]["messages"][-1]["content"][0]["text"]


async def test_run_suite_repeats_and_counts() -> None:
    scenario = _scenario("book_four_friday_7pm")
    script = _hold_script(scenario, 4, "2026-12-04", "19:00")
    llm = ScriptedLlm(*script, *script)
    result = await run_suite(get_test_session_factory(), [scenario], core=AgentCore(llm),
                             diner_factory=lambda s: ScriptedDiner([ASK]), repeat=2)
    assert [o.scenario_id for o in result.outcomes] == ["book_four_friday_7pm"] * 2
    assert result.total == 2
    assert result.passed == 2
    assert result.pass_rate == 1.0
    assert result.by_scenario == {"book_four_friday_7pm": [True, True]}


async def test_run_suite_records_a_runner_error_as_a_failure() -> None:
    scenario = _scenario("book_four_friday_7pm")

    class Broken:
        async def reply(self, scenario, transcript):
            raise RuntimeError("diner down")

    result = await run_suite(get_test_session_factory(), [scenario],
                             core=AgentCore(ScriptedLlm()),
                             diner_factory=lambda s: Broken())
    [o] = result.outcomes
    assert o.check.passed is False
    assert any("diner down" in f for f in o.check.failures)
    assert result.passed == 0
