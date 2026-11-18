"""The Lex V2 code-hook Lambda (#259 step 6): Lex event -> backend turn ->
Lex response, and SSML rendering. Pure: the HTTP call and the secret
loader are injected, so nothing here touches AWS or the network."""

import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

_BRIDGE_DIR = Path(__file__).resolve().parents[2] / "infra" / "voice" / "bridge"
sys.path.insert(0, str(_BRIDGE_DIR))

import handler as bridge  # noqa: E402
import ssml  # noqa: E402

URL = "https://api.example.test"
SECRET = "s3cret"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def reply(**over: Any) -> dict[str, Any]:
    base = {"text": "How many people?", "expect": "text",
            "hints": {"elicit": None, "dtmf": False}, "confirm_prompt": None,
            "end_call": False, "transfer_to": None, "reason": None}
    base.update(over)
    return base


class FakeHttp:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes,
                 timeout: float) -> tuple[int, bytes]:
        self.calls.append({"url": url, "headers": headers,
                           "body": json.loads(body), "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, tuple):
            return r
        return 200, json.dumps(r).encode()


def event(transcript: str = "a table for two", attrs: dict[str, str] | None = None,
          confirmation: str = "None", intent: str = "HostTurn") -> dict[str, Any]:
    session = {"contact_id": "c-123", "dialed": "+12125550100",
               "caller": "+13475550199"}
    session.update(attrs or {})
    return {"inputTranscript": transcript, "invocationSource": "DialogCodeHook",
            "inputMode": "Speech", "sessionId": "sess-1",
            "sessionState": {"sessionAttributes": session,
                             "intent": {"name": intent, "slots": {},
                                        "state": "InProgress",
                                        "confirmationState": confirmation}}}


def make(http: FakeHttp, secret: str | None = SECRET) -> bridge.Bridge:
    def load() -> str | None:
        return secret
    return bridge.Bridge(backend_url=URL, load_secret=load, http=http)


def ssml_of(resp: dict[str, Any]) -> str:
    [msg] = resp["messages"]
    assert msg["contentType"] == "SSML"
    return msg["content"]


# ---------------------------------------------------------------------------
# request to the backend
# ---------------------------------------------------------------------------

def test_posts_turn_with_secret_and_session_fields() -> None:
    http = FakeHttp(reply())
    resp = make(http).handle(event())
    [call] = http.calls
    assert call["url"] == URL + "/api/v1/voice/turn"
    assert call["headers"]["X-Voice-Bridge-Secret"] == SECRET
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] == 6
    body = call["body"]
    assert body["contact_id"] == "c-123"
    assert body["dialed"] == "+12125550100"
    assert body["caller"] == "+13475550199"
    assert body["seq"] == 0
    assert body["text"] == "a table for two"
    assert body["action"] is None
    assert set(body["timings"]) == {"asr_ms", "tts_ms", "transport_ms"}
    assert resp["sessionState"]["sessionAttributes"]["seq"] == "0"


def test_seq_advances_from_session_attributes() -> None:
    http = FakeHttp(reply())
    resp = make(http).handle(event(attrs={"seq": "4"}))
    assert http.calls[0]["body"]["seq"] == 5
    assert resp["sessionState"]["sessionAttributes"]["seq"] == "5"


def test_retry_with_same_session_attributes_resends_same_seq() -> None:
    """Lex only updates attributes from a response, so a retried invocation
    carries the attributes the first attempt saw and resends its seq."""
    http = FakeHttp(reply(), reply())
    b = make(http)
    ev = event(attrs={"seq": "2"})
    b.handle(ev)
    b.handle(ev)
    assert [c["body"]["seq"] for c in http.calls] == [3, 3]


def test_withheld_or_invalid_caller_is_sent_as_null() -> None:
    http = FakeHttp(reply(), reply())
    make(http).handle(event(attrs={"caller": "anonymous"}))
    make(http).handle(event(attrs={"caller": ""}))
    assert [c["body"]["caller"] for c in http.calls] == [None, None]


def test_empty_transcript_is_sent_as_null_text() -> None:
    http = FakeHttp(reply(text="Hello, thanks for calling."))
    make(http).handle(event(transcript=""))
    assert http.calls[0]["body"]["text"] is None


def test_long_transcript_is_truncated_to_backend_limit() -> None:
    http = FakeHttp(reply())
    make(http).handle(event(transcript="x" * 5000))
    assert len(http.calls[0]["body"]["text"]) == 1000


def test_secret_is_loaded_once_per_cold_start() -> None:
    loads: list[int] = []

    def load() -> str:
        loads.append(1)
        return SECRET
    http = FakeHttp(reply(), reply())
    b = bridge.Bridge(backend_url=URL, load_secret=load, http=http)
    b.handle(event())
    b.handle(event())
    assert len(loads) == 1


# ---------------------------------------------------------------------------
# dialog action mapping
# ---------------------------------------------------------------------------

def test_plain_reply_elicits_intent() -> None:
    resp = make(FakeHttp(reply(text="What day?"))).handle(event())
    assert resp["sessionState"]["dialogAction"] == {"type": "ElicitIntent"}
    assert ssml_of(resp) == "<speak>What day?</speak>"
    assert resp["sessionState"]["sessionAttributes"]["pending_expect"] == "text"


def test_elicit_hint_elicits_matching_slot_on_event_intent() -> None:
    resp = make(FakeHttp(reply(expect="party_size",
                               hints={"elicit": "party_size", "dtmf": True}))
                ).handle(event(intent="HostTurn"))
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "ElicitSlot", "slotToElicit": "party_size"}
    assert state["intent"]["name"] == "HostTurn"
    assert state["intent"]["state"] == "InProgress"
    attrs = state["sessionAttributes"]
    assert attrs["x-amz-lex:dtmf:end-character:HostTurn:party_size"] == "#"
    assert "x-amz-lex:dtmf:max-length:HostTurn:party_size" in attrs
    hints = state["runtimeHints"]["slotHints"]["HostTurn"]["party_size"]
    assert {"phrase": "two"} in hints["runtimeHintValues"]


def test_elicit_without_dtmf_sets_no_dtmf_attributes_and_drops_old_ones() -> None:
    old = {"x-amz-lex:dtmf:end-character:HostTurn:party_size": "#",
           "x-amz-lex:dtmf:max-length:HostTurn:party_size": "2"}
    resp = make(FakeHttp(reply(expect="confirmation_code",
                               hints={"elicit": "confirmation_code", "dtmf": False}))
                ).handle(event(attrs=old))
    attrs = resp["sessionState"]["sessionAttributes"]
    assert not any(k.startswith("x-amz-lex:dtmf") for k in attrs)
    assert resp["sessionState"]["dialogAction"]["slotToElicit"] == "confirmation_code"


def test_confirm_prompt_confirms_intent() -> None:
    resp = make(FakeHttp(reply(text="I found a table.", expect="confirm_hold",
                               confirm_prompt="Shall I book a table for 2?"))
                ).handle(event())
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "ConfirmIntent"}
    assert state["intent"]["confirmationState"] == "None"
    assert state["intent"]["name"] == "HostTurn"
    assert state["sessionAttributes"]["pending_expect"] == "confirm_hold"
    assert ssml_of(resp) == ("<speak>I found a table. "
                             "Shall I book a table for 2?</speak>")


def test_confirm_expect_without_prompt_is_not_a_pending_confirmation() -> None:
    resp = make(FakeHttp(reply(expect="confirm_hold", confirm_prompt=None))
                ).handle(event())
    assert resp["sessionState"]["dialogAction"] == {"type": "ElicitIntent"}
    assert resp["sessionState"]["sessionAttributes"]["pending_expect"] == "text"


def test_end_call_closes_fulfilled() -> None:
    resp = make(FakeHttp(reply(text="Goodbye.", end_call=True))).handle(event())
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "Close"}
    assert state["intent"]["state"] == "Fulfilled"
    assert "transfer_to" not in state["sessionAttributes"]


def test_transfer_closes_with_transfer_attribute() -> None:
    resp = make(FakeHttp(reply(text="Let me get someone.",
                               transfer_to="+12125550111"))).handle(event())
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "Close"}
    assert state["intent"]["state"] == "Fulfilled"
    assert state["sessionAttributes"]["transfer_to"] == "+12125550111"


def test_non_transfer_reply_clears_stale_transfer_attribute() -> None:
    resp = make(FakeHttp(reply(end_call=True))).handle(
        event(attrs={"transfer_to": "+12125550111"}))
    assert "transfer_to" not in resp["sessionState"]["sessionAttributes"]


# ---------------------------------------------------------------------------
# confirmation flow
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pending", ["confirm_hold", "confirm_cancel", "confirm_modify"])
def test_confirmed_sends_the_pending_confirm_action(pending: str) -> None:
    http = FakeHttp(reply(text="Booked."))
    make(http).handle(event(transcript="yes", confirmation="Confirmed",
                            attrs={"pending_expect": pending}))
    body = http.calls[0]["body"]
    assert body["action"] == pending
    assert body["text"] is None


@pytest.mark.parametrize("pending", ["confirm_hold", "confirm_cancel", "confirm_modify"])
def test_denied_sends_decline_hold(pending: str) -> None:
    http = FakeHttp(reply(text="No problem."))
    make(http).handle(event(transcript="no", confirmation="Denied",
                            attrs={"pending_expect": pending}))
    body = http.calls[0]["body"]
    assert body["action"] == "decline_hold"
    assert body["text"] is None


def test_confirmation_state_without_pending_question_sends_text() -> None:
    http = FakeHttp(reply())
    make(http).handle(event(transcript="yes", confirmation="Confirmed",
                            attrs={"pending_expect": "text"}))
    body = http.calls[0]["body"]
    assert body["action"] is None
    assert body["text"] == "yes"


def test_unanswered_confirmation_sends_transcript() -> None:
    http = FakeHttp(reply())
    make(http).handle(event(transcript="actually make it eight",
                            confirmation="None",
                            attrs={"pending_expect": "confirm_hold"}))
    body = http.calls[0]["body"]
    assert body["action"] is None
    assert body["text"] == "actually make it eight"


def test_full_confirmation_round_trip() -> None:
    http = FakeHttp(reply(text="Found one.", expect="confirm_hold",
                          confirm_prompt="Shall I book it?"),
                    reply(text="You're booked."))
    b = make(http)
    first = b.handle(event())
    attrs = first["sessionState"]["sessionAttributes"]
    second = b.handle(event(transcript="yes", confirmation="Confirmed", attrs=attrs))
    assert http.calls[1]["body"]["action"] == "confirm_hold"
    assert http.calls[1]["body"]["seq"] == http.calls[0]["body"]["seq"] + 1
    assert second["sessionState"]["sessionAttributes"]["pending_expect"] == "text"


# ---------------------------------------------------------------------------
# failures
# ---------------------------------------------------------------------------

FAILURES = [socket.timeout("timed out"), TimeoutError(), OSError("refused"),
            (500, b"oops"), (502, b""), (404, b'{"detail":"Not found"}'),
            (200, b"not json"), (200, b'{"text": 5}'),
            (200, json.dumps(reply(expect="bogus")).encode()),
            (200, json.dumps({"text": "hi"}).encode())]


@pytest.mark.parametrize("failure", FAILURES)
def test_failure_closes_politely_without_transfer(failure: Any) -> None:
    resp = make(FakeHttp(failure)).handle(event())
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "Close"}
    assert state["intent"]["state"] == "Failed"
    assert ssml_of(resp) == ("<speak>Sorry, I&apos;m having trouble right now. "
                             "Please call back in a few minutes. Goodbye.</speak>")
    assert "transfer_to" not in state["sessionAttributes"]


@pytest.mark.parametrize("failure", FAILURES[:4])
def test_failure_transfers_when_a_number_is_known(failure: Any) -> None:
    resp = make(FakeHttp(failure)).handle(
        event(attrs={"fallback_transfer_to": "+12125550111"}))
    state = resp["sessionState"]
    assert state["dialogAction"] == {"type": "Close"}
    assert state["sessionAttributes"]["transfer_to"] == "+12125550111"
    assert state["sessionAttributes"]["fallback_transfer_to"] == "+12125550111"
    assert ssml_of(resp) == ("<speak>Sorry, I&apos;m having trouble right now. "
                             "Let me transfer you.</speak>")


def test_missing_secret_fails_without_calling_backend() -> None:
    http = FakeHttp()
    resp = make(http, secret=None).handle(event())
    assert http.calls == []
    assert resp["sessionState"]["dialogAction"] == {"type": "Close"}


def test_secret_loader_raising_is_handled_and_retried_next_time() -> None:
    attempts: list[int] = []

    def load() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("secrets manager down")
        return SECRET
    http = FakeHttp(reply())
    b = bridge.Bridge(backend_url=URL, load_secret=load, http=http)
    assert b.handle(event())["sessionState"]["dialogAction"]["type"] == "Close"
    assert b.handle(event())["sessionState"]["dialogAction"]["type"] == "ElicitIntent"


def test_missing_contact_attributes_fail_without_calling_backend() -> None:
    http = FakeHttp()
    ev = event()
    del ev["sessionState"]["sessionAttributes"]["dialed"]
    resp = make(http).handle(ev)
    assert http.calls == []
    assert resp["sessionState"]["intent"]["state"] == "Failed"


def test_garbage_event_never_raises() -> None:
    resp = make(FakeHttp()).handle({})  # type: ignore[arg-type]
    assert resp["sessionState"]["dialogAction"] == {"type": "Close"}


def test_failure_logs_no_caller_number_or_transcript(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    make(FakeHttp((500, b""))).handle(event(transcript="my number is 3475550199"))
    logged = caplog.text
    assert "c-123" in logged
    assert "3475550199" not in logged and "+13475550199" not in logged
    assert "my number" not in logged


def test_module_handler_reads_env_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    monkeypatch.setattr(bridge, "_BRIDGE", None)
    resp = bridge.handler(event(), None)
    assert resp["sessionState"]["dialogAction"] == {"type": "Close"}


# ---------------------------------------------------------------------------
# SSML
# ---------------------------------------------------------------------------

def test_render_wraps_in_speak() -> None:
    assert ssml.render("Hello.") == "<speak>Hello.</speak>"


def test_render_escapes_model_text_so_no_tags_can_be_injected() -> None:
    out = ssml.render('Hi <break time="10s"/> & "you" \'there\' </speak><speak>')
    assert out == ("<speak>Hi &lt;break time=&quot;10s&quot;/&gt; &amp; "
                   "&quot;you&quot; &apos;there&apos; &lt;/speak&gt;&lt;speak&gt;</speak>")


def test_render_escapes_injection_next_to_a_rule_match() -> None:
    out = ssml.render('code ABC234<say-as interpret-as="x">')
    assert "<say-as interpret-as=\"x\">" not in out
    assert "&lt;say-as" in out


@pytest.mark.parametrize("given,spoken", [
    ("See you at 7:00 PM.", "See you at 7 pm."),
    ("See you at 7:30 pm.", "See you at 7:30 pm."),
    ("at 12:00 AM", "at 12 am"),
    ("at 11:15 a.m. sharp", "at 11:15 am sharp"),
    ("at 9:00PM", "at 9 pm"),
])
def test_render_times(given: str, spoken: str) -> None:
    assert ssml.render(given) == f"<speak>{spoken}</speak>"


@pytest.mark.parametrize("given,spoken", [
    ("On 2026-11-21.", "On Saturday, November 21st."),
    ("2026-11-22", "Sunday, November 22nd"),
    ("2026-12-03", "Thursday, December 3rd"),
    ("2026-11-11", "Wednesday, November 11th"),
    ("not a date 2026-13-45", "not a date 2026-13-45"),
])
def test_render_dates(given: str, spoken: str) -> None:
    assert ssml.render(given) == f"<speak>{spoken}</speak>"


def test_render_us_phone_number_as_digit_groups() -> None:
    out = ssml.render("Call +12125550100 please.")
    assert out == ('<speak>Call <say-as interpret-as="digits">212</say-as>'
                   '<break strength="weak"/><say-as interpret-as="digits">555</say-as>'
                   '<break strength="weak"/><say-as interpret-as="digits">0100</say-as>'
                   ' please.</speak>')


def test_render_international_phone_number_keeps_country_code() -> None:
    out = ssml.render("+442071838750")
    assert out.startswith('<speak>plus <say-as interpret-as="digits">44</say-as>')
    digits = "".join(c for c in out if c.isdigit())
    assert digits == "442071838750"


@pytest.mark.parametrize("given", [
    "Your confirmation code is ABC234.",
    "Your code: ABC234.",
    "confirmation code ABC234",
])
def test_render_confirmation_code_letter_by_letter(given: str) -> None:
    out = ssml.render(given)
    assert '<say-as interpret-as="characters">ABC234</say-as>' in out


def test_render_does_not_spell_arbitrary_uppercase_words() -> None:
    out = ssml.render("We are OPEN TODAY until 10:00 PM. RSVPED.")
    assert "say-as" not in out


def test_render_strips_markdown_emphasis() -> None:
    assert ssml.render("Your code is **ABC234**.") == (
        '<speak>Your code is <say-as interpret-as="characters">ABC234</say-as>.</speak>')


def test_render_output_is_well_formed_xml() -> None:
    import xml.etree.ElementTree as ET
    text = ("Booked for 2026-11-21 at 7:00 PM, code is ABC234. Questions? "
            "Call +12125550100 <b>& thanks</b>")
    ET.fromstring(ssml.render(text))


# ---------------------------------------------------------------------------
# fix round 1
# ---------------------------------------------------------------------------

def test_stale_transfer_to_alone_does_not_transfer_on_failure() -> None:
    resp = make(FakeHttp((500, b""))).handle(event(attrs={"transfer_to": "+12125550111"}))
    assert "transfer_to" not in resp["sessionState"]["sessionAttributes"]


def test_invalid_fallback_number_does_not_transfer() -> None:
    resp = make(FakeHttp((500, b""))).handle(
        event(attrs={"fallback_transfer_to": "staff desk"}))
    assert "transfer_to" not in resp["sessionState"]["sessionAttributes"]
    assert "call back" in ssml_of(resp)


def test_failure_after_several_turns_still_transfers_to_fallback() -> None:
    http = FakeHttp(reply(), reply(expect="party_size",
                                   hints={"elicit": "party_size", "dtmf": True}),
                    reply(text="Found one.", expect="confirm_hold",
                          confirm_prompt="Shall I book it?"),
                    socket.timeout("timed out"))
    b = make(http)
    attrs = {"fallback_transfer_to": "+12125550111"}
    for _ in range(3):
        resp = b.handle(event(attrs=attrs))
        attrs = resp["sessionState"]["sessionAttributes"]
        assert attrs["fallback_transfer_to"] == "+12125550111"
        assert "transfer_to" not in attrs
    resp = b.handle(event(attrs=attrs))
    assert resp["sessionState"]["sessionAttributes"]["transfer_to"] == "+12125550111"
    assert "Let me transfer you." in ssml_of(resp)


def test_fallback_intent_event_still_elicits_and_confirms_on_host_turn() -> None:
    http = FakeHttp(reply(expect="party_size", hints={"elicit": "party_size", "dtmf": True}),
                    reply(expect="confirm_hold", confirm_prompt="Shall I book it?"),
                    reply(text="Bye.", end_call=True))
    b = make(http)
    elicit = b.handle(event(intent="FallbackIntent"))["sessionState"]
    assert elicit["intent"]["name"] == "HostTurn"
    assert "x-amz-lex:dtmf:end-character:HostTurn:party_size" in elicit["sessionAttributes"]
    assert "HostTurn" in elicit["runtimeHints"]["slotHints"]
    confirm = b.handle(event(intent="FallbackIntent"))["sessionState"]
    assert confirm["dialogAction"] == {"type": "ConfirmIntent"}
    assert confirm["intent"]["name"] == "HostTurn"
    close = b.handle(event(intent="FallbackIntent"))["sessionState"]
    assert close["intent"] == {"name": "FallbackIntent", "state": "Fulfilled"}


def test_long_reply_is_cut_at_a_sentence_and_keeps_confirm_prompt() -> None:
    body = " ".join(f"Sentence {i} mentions +12125550100 and 2026-11-21 at 7:00 PM."
                    for i in range(40))
    resp = make(FakeHttp(reply(text=body, expect="confirm_hold",
                               confirm_prompt="Shall I book a table for 2?"))
                ).handle(event())
    content = ssml_of(resp)
    assert len(content) <= 1000
    assert content.endswith("Shall I book a table for 2?</speak>")
    assert "Sentence 0 " in content
    # whole sentences only: the last kept body sentence ends with its period
    assert content.replace(" Shall I book a table for 2?</speak>", "").endswith(".")
    import xml.etree.ElementTree as ET
    ET.fromstring(content)


def test_long_single_sentence_is_cut_by_words() -> None:
    out = ssml.render_limited("word " * 400 + "end.")
    assert len(out) <= 1000
    assert out.endswith("...</speak>")


def test_short_text_is_unchanged_by_limit() -> None:
    assert ssml.render_limited("Hi.", "Shall I?") == "<speak>Hi. Shall I?</speak>"


def test_render_strips_xml_illegal_control_characters() -> None:
    import xml.etree.ElementTree as ET
    out = ssml.render("a\x00b\x07c\x1fd\ttab")
    assert out == "<speak>abcd\ttab</speak>"
    ET.fromstring(out)


def test_404_clears_cached_secret_so_rotation_recovers() -> None:
    secrets = ["old", "new"]
    loads: list[str] = []

    def load() -> str:
        loads.append(secrets[len(loads)])
        return loads[-1]
    http = FakeHttp((404, b""), reply())
    b = bridge.Bridge(backend_url=URL, load_secret=load, http=http)
    b.handle(event())
    resp = b.handle(event())
    assert [c["headers"]["X-Voice-Bridge-Secret"] for c in http.calls] == ["old", "new"]
    assert resp["sessionState"]["dialogAction"] == {"type": "ElicitIntent"}


def test_500_keeps_cached_secret() -> None:
    loads: list[int] = []

    def load() -> str:
        loads.append(1)
        return SECRET
    b = bridge.Bridge(backend_url=URL, load_secret=load,
                      http=FakeHttp((500, b""), reply()))
    b.handle(event())
    b.handle(event())
    assert len(loads) == 1


def test_confirm_prompt_with_non_confirm_expect_is_spoken_not_confirmed() -> None:
    resp = make(FakeHttp(reply(text="Got it.", expect="text",
                               confirm_prompt="Anything else?"))).handle(event())
    assert resp["sessionState"]["dialogAction"] == {"type": "ElicitIntent"}
    assert resp["sessionState"]["sessionAttributes"]["pending_expect"] == "text"
    assert ssml_of(resp) == "<speak>Got it. Anything else?</speak>"
