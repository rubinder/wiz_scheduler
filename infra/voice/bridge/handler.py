"""Lex V2 code hook for the voice channel (#259 step 6): one phone turn ->
POST {BACKEND_URL}/api/v1/voice/turn -> one Lex response with SSML.

The Lambda has no database; it only calls the backend, with the bridge
secret (Secrets Manager, SECRET_ARN, fetched once per cold start; boto3
comes from the Lambda runtime and is imported only there).

Session attributes (Lex V2 `sessionState.sessionAttributes`, strings):
- set by the contact flow: `contact_id`, `dialed`, `caller`;
- kept here: `seq` (the seq last sent), `pending_expect` (the confirm_*
  question a rendered `confirm_prompt` asked, else "text"), `transfer_to`
  (only on the Close that transfers; the contact flow transfers when it is
  set), `last_ms` (the last backend round trip, for the contact record),
  and the Lex DTMF attributes for a slot the backend accepts keypad on.

Seq: the seq sent is `seq + 1` from the incoming attributes. Lex only
changes attributes through a response, so a retried invocation carries the
attributes the first attempt saw and resends the same seq; the backend
answers it with the stored reply. (Comparing transcripts to spot retries
would treat a caller who says "two" twice as a retry.)

Confirmation: a `confirm_prompt` becomes ConfirmIntent; on the next turn
Lex's Confirmed sends the pending confirm_* action and Denied sends
`decline_hold`, both with no text. Any other turn sends the transcript.

Failures (timeout, non-200, malformed reply, no secret, bad event) never
raise: the caller hears an apology and is transferred when a number is
known, else told to call back. Logs carry the contact id and the error
class only, never a number or a transcript.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

try:
    from . import ssml
except ImportError:  # the Lambda zip root, and tests
    import ssml  # type: ignore[no-redef]

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BACKEND_TIMEOUT_SECONDS = 6
MAX_TEXT_CHARS = 1000  # settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS
DEFAULT_INTENT = "HostTurn"

TROUBLE_TEXT = "Sorry, I'm having trouble right now."
TRANSFER_SUFFIX = " Let me transfer you."
CALL_BACK_SUFFIX = " Please call back in a few minutes. Goodbye."

_E164 = re.compile(r"^\+[1-9][0-9]{1,14}$")
_CONFIRM_EXPECTS = ("confirm_hold", "confirm_cancel", "confirm_modify")
_EXPECTS = ("text", *_CONFIRM_EXPECTS, "phone_number", "party_size", "confirmation_code")
_ELICITS = ("party_size", "phone_number", "confirmation_code", "date", "time")
_DTMF_PREFIX = "x-amz-lex:dtmf:"
_DTMF_MAX_LENGTH = {"party_size": "2", "phone_number": "15"}
_RUNTIME_HINTS = {
    "party_size": ["one", "two", "three", "four", "five", "six", "seven", "eight",
                   "nine", "ten", "eleven", "twelve"],
}

Http = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


class BridgeError(Exception):
    """A turn the backend could not answer."""


def urllib_post(url: str, headers: dict[str, str], body: bytes,
                timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def secrets_manager_loader(secret_arn: str | None) -> Callable[[], str | None]:
    def load() -> str | None:
        if not secret_arn:
            return None
        import boto3  # provided by the Lambda runtime

        value = boto3.client("secretsmanager").get_secret_value(
            SecretId=secret_arn)["SecretString"]
        return value.strip() or None
    return load


def _valid_reply(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise BridgeError("reply is not an object")
    hints = data.get("hints")
    ok = (isinstance(data.get("text"), str)
          and data.get("expect") in _EXPECTS
          and isinstance(data.get("end_call"), bool)
          and isinstance(hints, dict)
          and hints.get("elicit") in (None, *_ELICITS)
          and isinstance(hints.get("dtmf", False), bool)
          and (data.get("confirm_prompt") is None
               or isinstance(data.get("confirm_prompt"), str))
          and (data.get("transfer_to") is None
               or (isinstance(data.get("transfer_to"), str)
                   and _E164.fullmatch(data["transfer_to"]))))
    if not ok:
        raise BridgeError("malformed reply")
    return data


class Bridge:
    def __init__(self, backend_url: str | None, load_secret: Callable[[], str | None],
                 http: Http = urllib_post) -> None:
        self.backend_url = (backend_url or "").rstrip("/")
        self._load_secret = load_secret
        self._http = http
        self._secret: str | None = None

    def _get_secret(self) -> str:
        if self._secret is None:
            self._secret = self._load_secret()  # not cached when it fails
        if not self._secret:
            raise BridgeError("no bridge secret")
        return self._secret

    # -- request -----------------------------------------------------------

    def _turn_body(self, event: dict[str, Any], attrs: dict[str, str],
                   intent: dict[str, Any], seq: int) -> dict[str, Any]:
        contact_id, dialed = attrs.get("contact_id"), attrs.get("dialed")
        if not contact_id or not dialed or not _E164.fullmatch(dialed):
            raise BridgeError("contact attributes missing")
        caller = attrs.get("caller") or ""
        pending = attrs.get("pending_expect")
        confirmation = intent.get("confirmationState")
        action: str | None = None
        text: str | None = None
        if pending in _CONFIRM_EXPECTS and confirmation == "Confirmed":
            action = pending
        elif pending in _CONFIRM_EXPECTS and confirmation == "Denied":
            action = "decline_hold"
        else:
            text = (event.get("inputTranscript") or "").strip()[:MAX_TEXT_CHARS] or None
        return {"contact_id": contact_id, "dialed": dialed,
                "caller": caller if _E164.fullmatch(caller) else None,
                "seq": seq, "text": text, "action": action,
                # Lex reports no recognition or synthesis time, and transport
                # can't be split from the backend's own time without a server
                # timing in the reply.
                "timings": {"asr_ms": None, "tts_ms": None, "transport_ms": None}}

    def _call(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if not self.backend_url:
            raise BridgeError("no backend url")
        headers = {"Content-Type": "application/json",
                   "X-Voice-Bridge-Secret": self._get_secret()}
        started = time.monotonic()
        status, raw = self._http(self.backend_url + "/api/v1/voice/turn", headers,
                                 json.dumps(body).encode(), BACKEND_TIMEOUT_SECONDS)
        elapsed = int((time.monotonic() - started) * 1000)
        if status != 200:
            raise BridgeError(f"status {status}")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise BridgeError("reply is not json") from e
        return _valid_reply(data), elapsed

    # -- response ----------------------------------------------------------

    @staticmethod
    def _response(dialog: dict[str, Any], attrs: dict[str, str], text: str,
                  intent: dict[str, Any] | None = None,
                  extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {"dialogAction": dialog, "sessionAttributes": attrs}
        if intent is not None:
            state["intent"] = intent
        state.update(extra or {})
        return {"sessionState": state,
                "messages": [{"contentType": "SSML", "content": ssml.render(text)}]}

    def _render(self, reply: dict[str, Any], attrs: dict[str, str],
                intent_name: str) -> dict[str, Any]:
        attrs = {k: v for k, v in attrs.items()
                 if k != "transfer_to" and not k.startswith(_DTMF_PREFIX)}
        attrs["pending_expect"] = "text"
        text = reply["text"]
        if reply.get("transfer_to"):
            attrs["transfer_to"] = reply["transfer_to"]
            return self._response({"type": "Close"}, attrs, text,
                                  {"name": intent_name, "state": "Fulfilled"})
        if reply["end_call"]:
            return self._response({"type": "Close"}, attrs, text,
                                  {"name": intent_name, "state": "Fulfilled"})
        prompt = reply.get("confirm_prompt")
        if prompt and reply["expect"] in _CONFIRM_EXPECTS:
            attrs["pending_expect"] = reply["expect"]
            return self._response({"type": "ConfirmIntent"}, attrs,
                                  f"{text} {prompt}".strip(),
                                  {"name": intent_name, "state": "InProgress",
                                   "confirmationState": "None"})
        hints = reply["hints"]
        slot = hints.get("elicit")
        if slot:
            extra: dict[str, Any] = {}
            if hints.get("dtmf"):
                attrs[f"{_DTMF_PREFIX}end-character:{intent_name}:{slot}"] = "#"
                attrs[f"{_DTMF_PREFIX}max-length:{intent_name}:{slot}"] = (
                    _DTMF_MAX_LENGTH.get(slot, "15"))
            if slot in _RUNTIME_HINTS:
                extra["runtimeHints"] = {"slotHints": {intent_name: {slot: {
                    "runtimeHintValues": [{"phrase": p} for p in _RUNTIME_HINTS[slot]]}}}}
            return self._response({"type": "ElicitSlot", "slotToElicit": slot}, attrs,
                                  text, {"name": intent_name, "state": "InProgress"},
                                  extra)
        return self._response({"type": "ElicitIntent"}, attrs, text)

    def _failure(self, attrs: dict[str, str], intent_name: str) -> dict[str, Any]:
        keep = {k: v for k, v in attrs.items() if not k.startswith(_DTMF_PREFIX)}
        keep["pending_expect"] = "text"
        transfer_to = keep.get("transfer_to") or ""
        if _E164.fullmatch(transfer_to):
            text = TROUBLE_TEXT + TRANSFER_SUFFIX
        else:
            keep.pop("transfer_to", None)
            text = TROUBLE_TEXT + CALL_BACK_SUFFIX
        return self._response({"type": "Close"}, keep, text,
                              {"name": intent_name, "state": "Failed"})

    # -- entry -------------------------------------------------------------

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        attrs: dict[str, str] = {}
        intent_name = DEFAULT_INTENT
        contact_id = "-"
        try:
            state = event.get("sessionState") or {}
            attrs = {str(k): str(v) for k, v in
                     (state.get("sessionAttributes") or {}).items() if v is not None}
            intent = state.get("intent") or {}
            intent_name = intent.get("name") or DEFAULT_INTENT
            contact_id = attrs.get("contact_id") or "-"
            try:
                seq = int(attrs.get("seq", "-1")) + 1
            except ValueError:
                seq = 0
            seq = max(seq, 0)
            body = self._turn_body(event, attrs, intent, seq)
            reply, elapsed = self._call(body)
            attrs["seq"] = str(seq)
            attrs["last_ms"] = str(elapsed)
            return self._render(reply, attrs, intent_name)
        except Exception as e:  # never raise out of a Lex code hook
            logger.warning("voice_bridge.turn_failed contact_id=%s error=%s",
                           contact_id, type(e).__name__)
            return self._failure(attrs, intent_name)


_BRIDGE: Bridge | None = None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = Bridge(os.environ.get("BACKEND_URL"),
                         secrets_manager_loader(os.environ.get("SECRET_ARN")))
    return _BRIDGE.handle(event)
