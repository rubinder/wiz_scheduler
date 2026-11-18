"""Voice scenarios through the voice bridge in process (#259 step 8): the
recognizers, repeat detection, SSML and the runner's voice channel. No
network: the model is a recording or ScriptedLlm."""

import dataclasses
import random
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select

from backend.reservations.agent import voice
from backend.reservations.agent.core import AgentCore, TurnInput, TurnOutput
from backend.reservations.evals import voice as evals_voice
from backend.reservations.evals.diner import DONE, ScriptedDiner
from backend.reservations.evals.replay import load_recordings, replay_core, replay_diner
from backend.reservations.evals.runner import run_scenario
from backend.reservations.evals.seed import seed_scenario
from backend.reservations.evals.voice import (
    ExactRecognizer,
    Heard,
    NoisyRecognizer,
    is_repeat,
    open_call,
    ssml_error,
)
from backend.reservations.models import BOOKED, SEATED, Reservation
from tests.conftest import get_test_session_factory
from tests.reservations.agent.fakes import ScriptedLlm, reply, text
from tests.reservations.evals.test_runner import ASK, _hold_script, _scenario

RECORDINGS = load_recordings()
integration = [pytest.mark.integration, pytest.mark.asyncio]


# -- the noisy recognizer ------------------------------------------------------

LINE = ("Hi, a table for 4 this Friday at 7pm, two of us are fourteen. "
        "Code EVCHG2, phone +12125550142.")


async def _hear_all(r: NoisyRecognizer, lines: list[str]) -> list[str | None]:
    return [(await r.hear(line)).text for line in lines]


@pytest.mark.asyncio
async def test_noisy_is_deterministic_per_seed() -> None:
    lines = [LINE] * 6
    a = await _hear_all(NoisyRecognizer(259).for_scenario("s1"), lines)
    b = await _hear_all(NoisyRecognizer(259).for_scenario("s1"), lines)
    assert a == b
    # Scoped per turn: the same line is not garbled the same way every turn.
    assert len(set(a)) > 1
    # Another seed, or another scenario, draws differently.
    assert a != await _hear_all(NoisyRecognizer(260).for_scenario("s1"), lines)
    assert a != await _hear_all(NoisyRecognizer(259).for_scenario("s2"), lines)
    # for_scenario starts the turn count again.
    r = NoisyRecognizer(259)
    first = await _hear_all(r.for_scenario("s1"), lines)
    assert first == await _hear_all(r.for_scenario("s1"), lines)


def test_noisy_confusions() -> None:
    r = NoisyRecognizer(259, rate=1.0)
    out = r.garble("Four for eight Friday seven", random.Random(1)).split()
    assert out == ["For", "four", "ate", "Fried", "heaven"]
    assert r.garble("two", random.Random(1)) in ("to", "too")
    assert r.garble("thirteen ninety 14 4", random.Random(1)) == "thirty nineteen 40 for"
    # A long digit run loses exactly one digit; a short one is kept.
    phone = r.garble("+12125550142", random.Random(1))
    assert phone.startswith("+") and len(phone) == len("+12125550142") - 1
    assert r.garble("123", random.Random(1)) == "123"
    # Code letters: B/D/E/P/V, M/N, S/F; other characters stay.
    code = r.garble("EVCHG2", random.Random(1))
    assert code[0] in "BDPV" and code[1] in "BDEP" and code[2:] == "CHG2"
    assert r.garble("MS", random.Random(1)) == "MS"  # not a 6-character code
    assert r.garble("MSHJK2", random.Random(1))[:2] == "NF"
    # Rate 0 hears every word.
    assert NoisyRecognizer(259, rate=0.0).garble(LINE, random.Random(1)) == LINE


@pytest.mark.asyncio
async def test_exact_recognizer() -> None:
    assert await ExactRecognizer().hear(" four ") == Heard(text="four")
    assert (await ExactRecognizer().hear("  ")).text is None


# -- repeats and SSML ----------------------------------------------------------

def _reply(elicit: str | None = None, text_: str = "Okay.") -> dict[str, Any]:
    return {"text": text_, "expect": elicit or "text",
            "hints": {"elicit": elicit, "dtmf": False}, "confirm_prompt": None,
            "end_call": False, "transfer_to": None, "reason": None}


def test_repeat_detection() -> None:
    assert is_repeat(_reply(), voice.not_caught(_reply()))
    assert is_repeat(None, voice.not_caught(None))
    assert is_repeat(_reply("party_size"), _reply("party_size"))
    assert not is_repeat(_reply("party_size"), _reply("phone_number"))
    assert not is_repeat(_reply(), _reply("party_size"))
    assert not is_repeat(None, _reply())


def test_ssml_of_a_reply() -> None:
    held = {**_reply(), "text": "I have 7:00 PM on 2026-12-04, code EVCHG2 & more <b>.",
            "expect": "confirm_hold", "confirm_prompt": "Shall I book it?"}
    assert ssml_error(held) is None
    assert evals_voice.spoken_question(held) == "Shall I book it?"
    assert evals_voice.spoken_question({**held, "end_call": True}) == ""


def test_ssml_parse_failure_is_reported(monkeypatch) -> None:
    broken = type("Broken", (), {"render_limited": staticmethod(
        lambda body, question="": "<speak><oops></speak>")})
    monkeypatch.setattr(evals_voice, "ssml_module", lambda: broken)
    assert "ParseError" in ssml_error(_reply())


# -- through the bridge --------------------------------------------------------

def _voice(scenario):
    return dataclasses.replace(scenario, channel="voice")


class _UserTexts:
    """Wraps an LLM client, keeping the diner text of every request."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.texts: list[str] = []

    @property
    def responses(self) -> list:
        return self.inner.responses

    async def create(self, **kwargs: Any) -> Any:
        for m in kwargs["messages"]:
            if m["role"] == "user" and isinstance(m["content"], str):
                self.texts.append(m["content"])
            elif m["role"] == "user":
                self.texts += [b["text"] for b in m["content"]
                               if isinstance(b, dict) and b.get("type") == "text"]
        return await self.inner.create(**kwargs)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("name,recording", RECORDINGS, ids=[n for n, _ in RECORDINGS])
async def test_exact_voice_reproduces_the_text_outcome(name: str, recording: dict) -> None:
    """Every recording, run on the web channel and again as a voice call
    with the exact recognizer: the same grade, the same diner text reaching
    the model, every recorded response used."""
    scenario = _scenario(recording["scenario_id"])
    outcomes, seen = [], []
    for sc, recognizer in ((scenario, None), (_voice(scenario), ExactRecognizer())):
        core = replay_core(recording, name)
        spy = _UserTexts(core.llm)
        core.llm = spy
        diner = replay_diner(recording)
        out = await run_scenario(get_test_session_factory(), sc, core=core,
                                 diner=diner, recognizer=recognizer)
        assert spy.responses == [] and diner.lines == [], name
        outcomes.append(out)
        seen.append(spy.texts)
    web, call = outcomes
    assert call.check.failures == web.check.failures == [], name
    assert call.check.passed and web.check.passed
    assert call.check.metrics["outcome"] == web.check.metrics["outcome"]
    assert call.presses == web.presses
    assert seen[0] == seen[1]
    v = call.check.metrics["voice"]
    assert v["recognizer"] == "exact" and v["ssml_errors"] == []
    assert v["heard"] == recording["diner_lines"][:len(v["heard"])]
    assert "voice" not in web.check.metrics
    assert call.transcript[0][0] == "host"  # the greeting opens the call


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_scenarios_ignore_the_recognizer() -> None:
    scenario = _scenario("book_four_friday_7pm")
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK]), recognizer=NoisyRecognizer(1, 1.0))
    assert out.check.passed and "voice" not in out.check.metrics
    assert out.transcript[0] == ("diner", ASK)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_prompt_is_answered_as_lex_would() -> None:
    scenario = _voice(_scenario("book_four_friday_7pm"))
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK]), recognizer=ExactRecognizer())
    assert out.check.failures == []
    assert out.stop == "action" and out.presses == [("confirm_hold", "action")]
    # The caller heard the structured question, then answered it.
    assert "Shall I book a table for 4" in out.transcript[2][1]
    assert out.transcript[3] == ("diner", "[pressed Confirm]")
    assert out.check.metrics["voice"]["repeats"] == [False, False]


class _ForFour:
    """A recognizer that hears "4" as "for"."""

    name = "for-four"

    def for_scenario(self, scenario_id: str) -> "_ForFour":
        return self

    async def hear(self, text_: str) -> Heard:
        return Heard(text=text_.replace(" 4 ", " for "))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_for_four_confusion_never_books_unconfirmed() -> None:
    """The caller says 4, the line hears "for", the agent guesses 2 and holds
    it. The confirm prompt states 2, the diner does not say yes, and no
    booking exists: nothing is confirmed on a guess."""
    scenario = _voice(_scenario("book_four_friday_7pm"))
    llm = ScriptedLlm(*_hold_script(scenario, 2, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK, DONE]), recognizer=_ForFour())
    assert "table for for this Friday" in out.check.metrics["voice"]["heard"][0]
    assert "for 2 on Friday" in out.transcript[2][1]
    assert out.presses == []
    assert out.check.passed is False
    assert any(f.startswith("expected booking not found") for f in out.check.failures)
    assert not any(f.startswith(("unexpected booking", "reservation confirmed"))
                   for f in out.check.failures)
    async with get_test_session_factory()() as db:
        rows = (await db.execute(select(Reservation).where(
            Reservation.conversation_id == out.conversation_id))).scalars().all()
    assert rows and all(r.status not in (BOOKED, SEATED) and r.confirmation_code is None
                        for r in rows)


class _Eliciting:
    """The real core, with every reply eliciting the party size (as a voice
    tuning that asks slot by slot would)."""

    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.llm = core.llm

    async def handle_turn(self, db, conv, turn: TurnInput, *,
                          now: datetime | None = None) -> TurnOutput:
        out = await self.core.handle_turn(db, conv, turn, now=now)
        return dataclasses.replace(out, expect="party_size")

    async def close_conversation(self, db, conv, *, now: datetime) -> None:
        await self.core.close_conversation(db, conv, now=now)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_re_elicited_slot_is_a_repeat() -> None:
    scenario = _voice(_scenario("book_four_friday_7pm"))
    llm = ScriptedLlm(reply(text("How many people?")), reply(text("Sorry, how many?")),
                      reply(text("Thanks.")))
    core = _Eliciting(AgentCore(llm))
    out = await run_scenario(get_test_session_factory(), scenario, core=core,  # type: ignore[arg-type]
                             diner=ScriptedDiner(["Hi", "four", "four", DONE]),
                             recognizer=ExactRecognizer())
    # The first ask is not a repeat; asking again for the same slot is.
    assert out.check.metrics["voice"]["repeats"] == [False, True, True]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_unmatched_answer_is_not_caught_and_a_repeat() -> None:
    """A yes with no open question goes through voice.action_matches and
    comes back as not_caught, which counts as a repeat."""
    factory = get_test_session_factory()
    scenario = _voice(_scenario("book_four_friday_7pm"))
    async with factory() as db:
        seeded = await seed_scenario(db, scenario)
    call = await open_call(factory, seeded, scenario, AgentCore(ScriptedLlm()),
                           ExactRecognizer())
    greeting = await call.turn(now=scenario.now)
    assert greeting.reply["reason"] is None and call.conversation_id
    t = await call.turn(now=scenario.now, action="confirm_hold")
    assert t.reply["text"] == voice.NOT_CAUGHT_TEXT and t.out is None
    assert is_repeat(greeting.reply, t.reply)
    # The seq moved on, as the Lambda's does; a replayed seq is deduped.
    assert call.seq == 2
    call.seq = 0
    replayed = await call.turn(now=scenario.now)
    assert replayed.reply == greeting.reply


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_ssml_failure_is_a_scenario_error(monkeypatch) -> None:
    broken = type("Broken", (), {"render_limited": staticmethod(
        lambda body, question="": "<speak>&</speak>")})
    monkeypatch.setattr(evals_voice, "ssml_module", lambda: broken)
    scenario = _voice(_scenario("book_four_friday_7pm"))
    llm = ScriptedLlm(*_hold_script(scenario, 4, "2026-12-04", "19:00"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK]), recognizer=ExactRecognizer())
    assert out.check.passed is False
    assert any(f.startswith("ssml_invalid") for f in out.check.failures)
    assert len(out.check.metrics["voice"]["ssml_errors"]) == 3  # the greeting, the reply, the confirmation
