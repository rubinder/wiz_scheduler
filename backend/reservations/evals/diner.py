"""The simulated diner: a live model (`DinerSim`) or a replayed script
(`ScriptedDiner`).

The diner sees only what a real diner would: the host's reply text and a
short note of the buttons shown (`runner.visible_text`), never tool calls.
It answers only from the scenario's persona and facts, and says the literal
token `[[DONE]]` when its goal is met or abandoned.
"""

from typing import Any, Protocol

from backend.config import settings
from backend.reservations.evals.scenarios_io import Scenario

DINER_MODEL = "claude-haiku-5-5"
DINER_MAX_TOKENS = 300
DONE = "[[DONE]]"
OPENER = "[The restaurant's booking chat is open. Send your first message.]"

# (speaker, text): speaker is "diner" or "host".
Transcript = list[tuple[str, str]]


class Diner(Protocol):
    async def reply(self, scenario: Scenario, transcript: Transcript) -> str: ...


def diner_system_prompt(scenario: Scenario) -> str:
    d = scenario.diner
    facts = "\n".join(f"- {k}: {v}" for k, v in d.facts.items())
    if scenario.channel == "voice":
        setting = ("You are role-playing a diner on the phone with a restaurant's "
                   "booking host. Write only what the diner says: short, natural "
                   "spoken sentences, with no narration or stage directions.")
    else:
        setting = ("You are role-playing a diner chatting with a restaurant's online "
                   "booking host. Write only what the diner types: short, natural "
                   "messages, with no narration or stage directions.")
    return (
        f"{setting}\n\n"
        f"Who you are: {d.persona}\n\n"
        f"Your goal: {d.goal}\n\n"
        "Facts you know. Answer only from these facts; if asked something not in "
        f"them, say you don't know.\n{facts}\n\n"
        "When the host shows buttons, pressing them is handled for you; lines such "
        "as \"[pressed Confirm]\" are your button presses.\n\n"
        f"When your goal is met, or you give up on it, reply with exactly {DONE} "
        "and nothing else."
    )


def diner_messages(transcript: Transcript) -> list[dict[str, Any]]:
    """The transcript from the diner's side: host lines are `user`, the
    diner's own lines `assistant`. Consecutive lines of one speaker merge; the
    list starts and ends with a `user` message."""
    msgs: list[dict[str, Any]] = [{"role": "user", "content": OPENER}]
    for speaker, line in transcript:
        role = "user" if speaker == "host" else "assistant"
        if msgs[-1]["role"] == role:
            if msgs[-1]["content"] == OPENER:
                msgs[-1]["content"] = line
            else:
                msgs[-1]["content"] += f"\n\n{line}"
        else:
            msgs.append({"role": role, "content": line})
    if msgs[-1]["role"] != "user":
        msgs.append({"role": "user", "content": "[The host has not replied yet.]"})
    return msgs


class DinerSim:
    """The live diner. `client` is an `anthropic.AsyncAnthropic` (or a test
    double with `messages.create`); one is made from ANTHROPIC_API_KEY when
    omitted. Tool-free."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY or None)
        return self._client

    async def reply(self, scenario: Scenario, transcript: Transcript) -> str:
        response = await self._sdk().messages.create(
            model=DINER_MODEL,
            max_tokens=DINER_MAX_TOKENS,
            system=diner_system_prompt(scenario),
            messages=diner_messages(transcript),
            output_config={"effort": "low"},
        )
        return "".join(getattr(b, "text", "") for b in response.content
                       if getattr(b, "type", None) == "text").strip()


class ScriptedDiner:
    """Replays fixed lines in order, then says DONE."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)

    async def reply(self, scenario: Scenario, transcript: Transcript) -> str:
        return self.lines.pop(0) if self.lines else DONE
