"""What a phone turn costs besides the model (#259): Amazon Connect voice
usage and inbound DID minutes, Lex V2 speech requests and Polly neural
characters, priced by the VOICE_COST_* settings. recording.add_voice_cost
adds it to the turn's recorded cost, so it is billed with the AI host usage
like model cost.

Not counted: staff transfer outbound minutes (AWS bills them; bounded by
VOICE_TRANSFERS_PER_DAY and the voice budget alarm) and the per-number daily
fee (covered by the subscription's base price)."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

from backend.config import settings

# Lex V2's message limit: the Lambda never speaks more of a reply
# (infra/voice/bridge/ssml.MAX_SSML_CHARS).
MAX_SPOKEN_CHARS = 1000

_MILLION = Decimal(1_000_000)
_SIXTY = Decimal(60)
_CENT_MILLIONTH = Decimal("0.000001")


def _price(value: float) -> Decimal:
    return Decimal(str(value))


def voice_cost(lex_requests: int, polly_chars: int, call_seconds: float) -> Decimal:
    """USD cost of one phone turn's speech and minutes, rounded to 6 places
    (the cost_usd column scale). Seconds are billed per second, clamped to
    0..VOICE_COST_MAX_SECONDS_PER_TURN."""
    cap = _price(settings.VOICE_COST_MAX_SECONDS_PER_TURN)
    seconds = min(max(Decimal(str(call_seconds)), Decimal(0)), cap)
    per_minute = (_price(settings.VOICE_COST_CONNECT_PER_MIN_USD)
                  + _price(settings.VOICE_COST_TELEPHONY_INBOUND_PER_MIN_USD))
    total = (Decimal(max(lex_requests, 0))
             * _price(settings.VOICE_COST_LEX_PER_SPEECH_REQUEST_USD)
             + Decimal(max(polly_chars, 0))
             * _price(settings.VOICE_COST_POLLY_NEURAL_PER_MILLION_CHARS_USD) / _MILLION
             + seconds * per_minute / _SIXTY)
    return total.quantize(_CENT_MILLIONTH, rounding=ROUND_HALF_UP)


def spoken_chars(reply: Mapping[str, Any]) -> int:
    """Plain-text characters Polly speaks for a bridge reply: its text and,
    when there is one, the question the Lambda appends after a space (SSML
    tags are not billed). At most MAX_SPOKEN_CHARS."""
    text = str(reply.get("text") or "").strip()
    question = str(reply.get("confirm_prompt") or "").strip()
    return min(len(f"{text} {question}".strip()), MAX_SPOKEN_CHARS)
