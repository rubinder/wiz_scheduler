"""The host agent's model client (#259). `AnthropicLlm` is the only code that
touches the Anthropic SDK; the core talks to the `LlmClient` protocol so
tests can script responses without a network."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.config import settings


@dataclass(frozen=True)
class LlmResponse:
    """One model response. `content` holds JSON-serializable block dicts,
    exactly as they must be replayed to the model on the next call."""

    content: list[dict[str, Any]]
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)


class LlmClient(Protocol):
    async def create(
        self, *, system: str, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmResponse: ...


def _usage(usage: Any) -> dict[str, int]:
    """SDK usage -> the recording columns' names."""
    def get(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    return {
        "input_tokens": get("input_tokens"),
        "output_tokens": get("output_tokens"),
        "cache_read_tokens": get("cache_read_input_tokens"),
        "cache_write_tokens": get("cache_creation_input_tokens"),
    }


class AnthropicLlm:
    """Messages API client. SDK errors (`anthropic.APIStatusError`,
    `anthropic.APIConnectionError`) propagate; the core catches them."""

    def __init__(self) -> None:
        self._client: Any = None

    def _sdk(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    async def create(
        self, *, system: str, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmResponse:
        # Thinking is left at the model default (adaptive); effort keeps it short.
        response = await self._sdk().messages.create(
            model=settings.RESERVATION_AGENT_MODEL,
            max_tokens=settings.RESERVATION_AGENT_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
            output_config={"effort": "low"},
            cache_control={"type": "ephemeral"},
        )
        return LlmResponse(
            content=[b.model_dump(mode="json", exclude_none=True) for b in response.content],
            stop_reason=str(response.stop_reason),
            usage=_usage(response.usage),
        )
