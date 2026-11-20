"""Voice call cost (#259): Connect + inbound telephony minutes, Lex speech
requests and Polly characters, billed with the AI host usage."""

from decimal import Decimal

from backend.reservations.agent import voice
from backend.reservations.agent.voice_cost import spoken_chars, voice_cost


def test_zero_inputs_cost_nothing():
    assert voice_cost(0, 0, 0) == Decimal("0.000000")


def test_one_lex_speech_request():
    assert voice_cost(1, 0, 0) == Decimal("0.004000")
    assert voice_cost(3, 0, 0) == Decimal("0.012000")


def test_polly_neural_characters():
    assert voice_cost(0, 1_000_000, 0) == Decimal("16.000000")
    assert voice_cost(0, 100, 0) == Decimal("0.001600")


def test_call_seconds_are_connect_plus_inbound_per_second():
    # (0.018 + 0.0022) per minute
    assert voice_cost(0, 0, 60) == Decimal("0.020200")
    assert voice_cost(0, 0, 30) == Decimal("0.010100")
    assert voice_cost(0, 0, 1.5) == Decimal("0.000505")


def test_call_seconds_are_capped_per_turn():
    assert voice_cost(0, 0, 3600) == voice_cost(0, 0, 120) == Decimal("0.040400")


def test_negative_seconds_cost_nothing():
    assert voice_cost(0, 0, -5) == Decimal("0.000000")


def test_components_add_up():
    assert voice_cost(1, 100, 60) == Decimal("0.025800")


def test_spoken_chars_count_the_text_and_the_question():
    assert spoken_chars(voice.close("Goodbye.", "ended")) == 8
    reply = {**voice.greeting("Hello there."), "confirm_prompt": "Shall I book it?"}
    # The Lambda speaks "text question" (one joining space).
    assert spoken_chars(reply) == len("Hello there. Shall I book it?")


def test_spoken_chars_stop_at_the_lex_message_limit():
    assert spoken_chars(voice.greeting("x" * 5000)) == 1000
