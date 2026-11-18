"""Call screening for the voice channel (#259 step 7): one inbound Connect
contact -> POST {BACKEND_URL}/api/v1/voice/screen -> {"action": ...}.

The contact flow invokes this before the Lex bot (InvokeLambdaFunction,
STRING_MAP response) and disconnects only on `{"action": "block"}`.

Event fields (the Connect Lambda event):
- `Details.ContactData.SystemEndpoint.Address` -> `dialed`;
- `Details.ContactData.CustomerEndpoint.Address` -> `caller`. A missing
  endpoint (withheld caller ID) is sent as "" and the backend answers it
  (reason `withheld`), so the screened_calls row still records the call;
- `Details.ContactData.ContactId`, for the log line only.

Fails open: no dialed number, no backend URL, no secret, a timeout, any
non-200 (a caller over 64 characters is a 422) or a malformed reply all
return `answer`. It never raises. A 404 (wrong secret) drops the cached
secret so a rotation recovers, as in handler.py. Logs carry the contact id
and the decision, or the contact id and the error class: never a number.

Same zip, environment (BACKEND_URL, SECRET_ARN) and secret as the bridge.
"""

import json
import logging
import os
from typing import Any, Callable

try:
    from .handler import Http, secrets_manager_loader, urllib_post
except ImportError:  # the Lambda zip root, and tests
    from handler import Http, secrets_manager_loader, urllib_post  # type: ignore[no-redef]

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BACKEND_TIMEOUT_SECONDS = 1.0
ANSWER = {"action": "answer"}
BLOCK = {"action": "block"}


class ScreenError(Exception):
    """A screen the backend could not decide."""


def _address(contact: dict[str, Any], endpoint: str) -> str:
    value = (contact.get(endpoint) or {}).get("Address")
    return value if isinstance(value, str) else ""


class Screener:
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
            raise ScreenError("no bridge secret")
        return self._secret

    def _decide(self, dialed: str, caller: str) -> dict[str, str]:
        if not dialed:
            raise ScreenError("no dialed number")
        if not self.backend_url:
            raise ScreenError("no backend url")
        headers = {"Content-Type": "application/json",
                   "X-Voice-Bridge-Secret": self._get_secret()}
        body = json.dumps({"dialed": dialed, "caller": caller}).encode()
        status, raw = self._http(self.backend_url + "/api/v1/voice/screen", headers,
                                 body, BACKEND_TIMEOUT_SECONDS)
        if status == 404:
            self._secret = None  # maybe rotated: fetch again next call
        if status != 200:
            raise ScreenError(f"status {status}")
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise ScreenError("reply is not json") from e
        action = data.get("action") if isinstance(data, dict) else None
        if action == "block":
            return dict(BLOCK)
        if action == "answer":
            return dict(ANSWER)
        raise ScreenError("malformed reply")

    def handle(self, event: Any) -> dict[str, str]:
        contact_id = "-"
        try:
            contact = (event.get("Details") or {}).get("ContactData") or {}
            contact_id = str(contact.get("ContactId") or "-")
            result = self._decide(_address(contact, "SystemEndpoint"),
                                  _address(contact, "CustomerEndpoint"))
            logger.info("voice_screen.decision contact_id=%s action=%s",
                        contact_id, result["action"])
            return result
        except Exception as e:  # fail open: never block or drop a call on error
            logger.warning("voice_screen.failed contact_id=%s error=%s",
                           contact_id, type(e).__name__)
            return dict(ANSWER)


_SCREENER: Screener | None = None


def handler(event: Any, context: Any) -> dict[str, str]:
    global _SCREENER
    try:
        if _SCREENER is None:
            _SCREENER = Screener(os.environ.get("BACKEND_URL"),
                                 secrets_manager_loader(os.environ.get("SECRET_ARN")))
        return _SCREENER.handle(event)
    except Exception as e:  # pragma: no cover - belt and braces
        logger.warning("voice_screen.failed contact_id=- error=%s", type(e).__name__)
        return dict(ANSWER)
