"""Test doubles for the agent's model client. No network."""

import copy
import inspect
import json
from typing import Any, Callable, Union

from backend.reservations.agent.llm import LlmResponse

Script = Union[LlmResponse, Exception, Callable[[dict[str, Any]], LlmResponse]]


class ScriptedLlm:
    """Returns queued responses in order and records every request.

    A queued item may be an `LlmResponse`, an exception to raise, or a
    callable (sync or async) that receives the request (system/messages/tools)
    and returns the response, for scripts that depend on tool results."""

    def __init__(self, *script: Script) -> None:
        self.script: list[Script] = list(script)
        self.requests: list[dict[str, Any]] = []

    def push(self, *script: Script) -> None:
        self.script.extend(script)

    async def create(self, *, system: str, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]], timeout: float = 20.0) -> LlmResponse:
        request = {"system": system, "messages": copy.deepcopy(messages),
                   "tools": tools, "timeout": timeout}
        self.requests.append(request)
        if not self.script:
            raise AssertionError("ScriptedLlm: no response queued")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            result = item(request)
            return await result if inspect.isawaitable(result) else result
        return item


USAGE = {"input_tokens": 1200, "output_tokens": 80,
         "cache_read_tokens": 900, "cache_write_tokens": 300}


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def thinking(t: str = "", signature: str = "sig-abc") -> dict[str, Any]:
    return {"type": "thinking", "thinking": t, "signature": signature}


def tool_use(id_: str, name: str, inp: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def reply(*blocks: dict[str, Any], stop: str = "end_turn",
          usage: dict[str, int] | None = None) -> LlmResponse:
    return LlmResponse(content=list(blocks), stop_reason=stop,
                       usage=dict(USAGE if usage is None else usage))


def last_tool_result(request: dict[str, Any]) -> dict[str, Any]:
    """The JSON result of the first tool_result in the request's last message."""
    content = request["messages"][-1]["content"]
    block = next(b for b in content if b.get("type") == "tool_result")
    return json.loads(block["content"])


class FakeClock:
    """A monotonic clock the test advances by hand (AgentCore(clock=...))."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
