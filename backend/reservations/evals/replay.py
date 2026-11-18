"""Record a scripted run's model responses, and replay them offline (CI).

A recording is JSON: {"version": 1, "scenario_id", "diner_lines", "responses"}
where `responses` are the agent model's `LlmResponse`s in call order and
`diner_lines` the diner's typed lines (button presses are the scenario
policy's, not recorded). Replaying runs the real core, tools and database
against those responses, so the checker grades the same end state; it also
catches a recording gone stale against `tools.TOOLS`.

Generate recordings with `tests/reservations/evals/test_replay.py`
(`RECORD_EVALS=1 pytest tests/reservations/evals/test_replay.py -k generate`),
which scripts the model with `ScriptedLlm` and writes them through
`record_transcript`. A live run can be captured the same way by wrapping its
client in `RecordingLlm`.
"""

import inspect
import json
from pathlib import Path
from typing import Any

from backend.reservations.agent import tools
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.llm import LlmClient, LlmResponse
from backend.reservations.evals.diner import ScriptedDiner
from backend.reservations.evals.runner import ScenarioOutcome

RECORDINGS_DIR = Path(__file__).parent / "recordings"
VERSION = 1


class ReplayInvalid(ValueError):
    """A recording that cannot be replayed (stale against the current tools)."""


class RecordingLlm:
    """Wraps an `LlmClient`, keeping every response for `record_transcript`."""

    def __init__(self, inner: LlmClient) -> None:
        self.inner = inner
        self.responses: list[LlmResponse] = []

    async def create(self, **kwargs: Any) -> LlmResponse:
        response = self.inner.create(**kwargs)
        if inspect.isawaitable(response):
            response = await response
        self.responses.append(response)
        return response


class ReplayLlm:
    """Returns the recorded responses in order; fails loudly when the agent
    asks for more than were recorded."""

    def __init__(self, name: str, responses: list[LlmResponse]) -> None:
        self.name = name
        self.responses = list(responses)

    async def create(self, **_: Any) -> LlmResponse:
        if not self.responses:
            raise AssertionError(f"recording {self.name}: the agent made more "
                                 "model calls than were recorded")
        return self.responses.pop(0)


def record_transcript(outcome: ScenarioOutcome, responses: list[LlmResponse]
                      ) -> dict[str, Any]:
    """The recording of a finished run: the agent's responses (from the
    `RecordingLlm` / `ScriptedLlm` that served it) and the diner's lines."""
    return {
        "version": VERSION,
        "scenario_id": outcome.scenario_id,
        "diner_lines": [text for who, text in outcome.transcript
                        if who == "diner" and not text.startswith("[pressed ")],
        "responses": [{"content": r.content, "stop_reason": r.stop_reason,
                       "usage": dict(r.usage)} for r in responses],
    }


def save_recording(recording: dict[str, Any], directory: Path = RECORDINGS_DIR) -> Path:
    path = directory / f"{recording['scenario_id']}.json"
    path.write_text(json.dumps(recording, indent=2, sort_keys=True) + "\n")
    return path


def load_recordings(directory: Path = RECORDINGS_DIR) -> list[tuple[str, dict[str, Any]]]:
    return [(p.name, json.loads(p.read_text())) for p in sorted(directory.glob("*.json"))]


# -- validation against tools.TOOLS -----------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "null": type(None)}


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def schema_errors(value: Any, schema: dict[str, Any], path: str = "input") -> list[str]:
    """A minimal JSON-schema check for the subset the tools use: type (a name
    or a list of names), enum, object properties / required /
    additionalProperties false, array items."""
    errors: list[str] = []
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_is_type(value, n) for n in names):
            return [f"{path}: expected {'/'.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required {key!r}")
        for key, sub in value.items():
            if key in props:
                errors.extend(schema_errors(sub, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], f"{path}[{i}]"))
    return errors


def validate_recording(recording: dict[str, Any], name: str) -> None:
    """Raise `ReplayInvalid` naming the recording and tool when a recorded
    tool_use is not in `TOOLS` or its input does not fit the input_schema."""
    schemas = {t["name"]: t["input_schema"] for t in tools.TOOLS}
    for i, response in enumerate(recording.get("responses", [])):
        for block in response.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool = block.get("name")
            if tool not in schemas:
                raise ReplayInvalid(
                    f"recording {name}: response {i} calls unknown tool {tool!r} "
                    f"(tools: {', '.join(sorted(schemas))}); re-record it")
            errors = schema_errors(block.get("input"), schemas[tool])
            if errors:
                raise ReplayInvalid(
                    f"recording {name}: response {i} tool {tool!r} input does "
                    f"not match its schema: {'; '.join(errors)}; re-record it")


def replay_core(recording: dict[str, Any], name: str | None = None) -> AgentCore:
    """An `AgentCore` that answers with the recorded responses. Validates
    the recording first."""
    label = name or recording.get("scenario_id", "?")
    validate_recording(recording, label)
    responses = [LlmResponse(content=r["content"], stop_reason=r["stop_reason"],
                             usage=dict(r.get("usage", {})))
                 for r in recording["responses"]]
    return AgentCore(ReplayLlm(label, responses))


def replay_diner(recording: dict[str, Any]) -> ScriptedDiner:
    return ScriptedDiner(list(recording["diner_lines"]))
