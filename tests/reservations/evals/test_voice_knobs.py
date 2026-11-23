"""Voice tuning knobs (#259): defaults change nothing, each knob does what the
spec says, and a run records the overrides it was given."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.reservations.agent import voice
from backend.reservations.agent.prompt import build_system_prompt
from backend.reservations.agent.core import TurnOutput
from backend.reservations.evals import run, store
from backend.reservations.rules import RulesSchema, VoiceKnobs
from tests.reservations.evals.test_run import _url

# sha256 of the default prompts (no knobs set) for the fixture
# location below. A change here means CI replay recordings may go stale.
GOLDEN = {
    "web": "4d811bbc9e562b6116984ded92613e0ebf0b2070a2708c13f55b9ad97d3e5e64",
    "voice": "17b60e3603b19abd475ec53e86b41c058e9a842382e9927b353dffbefb21fff6",
}


def _rules(**voice_knobs) -> RulesSchema:
    return RulesSchema(persona={"greeting": "Hi, automated host here", "facts": ["Open late"]},
                       max_self_book_party=6, voice=voice_knobs)


def _prompt(channel: str, **knobs) -> str:
    return build_system_prompt("Fixture", "America/New_York", _rules(**knobs), channel)


@pytest.mark.parametrize("channel", ["web", "voice"])
def test_defaults_leave_the_prompt_byte_identical(channel: str) -> None:
    assert hashlib.sha256(_prompt(channel).encode()).hexdigest() == GOLDEN[channel]


def test_stored_rules_without_voice_load_with_defaults() -> None:
    assert RulesSchema.model_validate({}).voice == VoiceKnobs()


def test_reply_max_sentences_adds_its_line_on_voice_only() -> None:
    text = _prompt("voice", voice_reply_max_sentences=2)
    assert "On the phone, answer in at most 2 short sentences." in text
    assert "answer in at most 1 short sentence." in _prompt("voice", voice_reply_max_sentences=1)
    assert "On the phone, answer in at most" not in _prompt("web", voice_reply_max_sentences=2)


def test_max_sentences_replaces_the_two_sentence_line() -> None:
    text = _prompt("voice", voice_reply_max_sentences=3)
    assert "under two short sentences" not in text
    assert "under two short sentences" in _prompt("voice")


def test_read_back_adds_its_line_on_voice_only() -> None:
    line = ("Before you hold a table, read back the party size, date, time and "
            "the caller's phone number in one sentence.")
    assert line in _prompt("voice", voice_read_back=True)
    assert line not in _prompt("web", voice_read_back=True)


def test_knob_ranges() -> None:
    for bad in ({"voice_reply_max_sentences": 6}, {"voice_reply_max_sentences": -1},
                {"voice_confirm_style": "shout"}):
        with pytest.raises(ValidationError):
            VoiceKnobs(**bad)


def test_statement_confirm_prompts() -> None:
    slot = {"party_size": 4, "local_date": "2026-12-04", "local_time": "19:00"}
    hold = TurnOutput("x", "confirm_hold", {**slot, "status": "held"}, False, None)
    assert voice.confirm_prompt(hold, "statement") == (
        "I can book a table for 4 on Friday, December 4th at 7 pm — say yes "
        "to confirm or no to change it.")
    assert voice.confirm_prompt(hold) == (
        "Shall I book a table for 4 on Friday, December 4th at 7 pm?")
    modify = TurnOutput("x", "confirm_modify", None, False, None,
                        {"kind": "modify", "current": slot,
                         "proposed": {**slot, "local_time": "20:30"}})
    assert voice.confirm_prompt(modify, "statement").startswith(
        "I can change your booking to a table for 4")
    cancel = TurnOutput("x", "confirm_cancel", None, False, None,
                        {"kind": "cancel", "current": slot, "proposed": None})
    assert voice.confirm_prompt(cancel, "statement") == (
        "I can cancel your booking for 4 on Friday, December 4th at 7 pm — say "
        "yes to cancel it or no to keep it.")
    reply = voice.spoken_reply(hold, "statement")
    assert reply["confirm_prompt"].startswith("I can book")


def test_parse_knobs() -> None:
    assert run.parse_knobs(["voice_reply_max_sentences=2", "voice_read_back=true",
                            "voice_confirm_style=statement"]) == {
        "voice_reply_max_sentences": 2, "voice_read_back": True,
        "voice_confirm_style": "statement"}
    assert run.parse_knobs([]) == {}
    for bad in ("nope=1", "voice_reply_max_sentences=9", "voice_reply_max_sentences=x",
                "voice_confirm_style=shout", "voice_read_back"):
        with pytest.raises(ValueError):
            run.parse_knobs([bad])


def test_prompt_version_changes_only_when_the_prompt_does() -> None:
    base = store.prompt_hashes()[1]
    assert store.prompt_hashes({})[1] == base
    assert store.prompt_hashes({"voice_read_back": True})[1] != base
    assert store.prompt_hashes({"voice_reply_max_sentences": 2})[1] != base
    # Confirm style only changes the server-built prompt, not the system prompt.
    assert store.prompt_hashes({"voice_confirm_style": "statement"})[1] == base


@pytest.mark.asyncio
@pytest.mark.parametrize("arg", ["nope=1", "voice_reply_max_sentences=9"])
async def test_bad_knob_exits_2(arg: str, capsys) -> None:
    assert await run.amain(["--knob", arg], database_url=_url()) == 2
    assert capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.asyncio
async def test_knobs_are_recorded_in_run_meta(tmp_path: Path, capsys) -> None:
    code = await run.amain(["--knob", "voice_read_back=true", "--knob",
                            "voice_confirm_style=statement"],
                           database_url=_url(), runs_dir=tmp_path)
    assert code in (0, 1), capsys.readouterr().out
    [saved] = list(tmp_path.glob("*.json"))
    meta = json.loads(saved.read_text())["meta"]
    assert meta["voice_knobs"] == {"voice_read_back": True,
                                   "voice_confirm_style": "statement"}
    assert meta["prompt_version"] == store.prompt_hashes(meta["voice_knobs"])[1][:12]
    assert meta["prompt_version"] != store.prompt_hashes()[1][:12]
