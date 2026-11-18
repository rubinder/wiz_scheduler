"""The call-screening Lambda (#259 step 7): Connect event -> backend screen ->
a flat {"action": ...} map. Pure: HTTP and the secret loader are injected,
so nothing here touches AWS or the network."""

import json
import logging
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

_BRIDGE_DIR = Path(__file__).resolve().parents[2] / "infra" / "voice" / "bridge"
sys.path.insert(0, str(_BRIDGE_DIR))

import screen  # noqa: E402

URL = "https://api.example.test"
SECRET = "s3cret"
DIALED = "+12125550100"
CALLER = "+13475550199"


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


def event(caller: str | None = CALLER, dialed: str | None = DIALED) -> dict[str, Any]:
    contact: dict[str, Any] = {"ContactId": "c-123", "Channel": "VOICE"}
    if dialed is not None:
        contact["SystemEndpoint"] = {"Type": "TELEPHONE_NUMBER", "Address": dialed}
    if caller is not None:
        contact["CustomerEndpoint"] = {"Type": "TELEPHONE_NUMBER", "Address": caller}
    return {"Name": "ContactFlowEvent", "Details": {"ContactData": contact,
                                                    "Parameters": {}}}


def make(http: FakeHttp, secret: str | None = SECRET,
         url: str | None = URL) -> screen.Screener:
    def load() -> str | None:
        return secret
    return screen.Screener(backend_url=url, load_secret=load, http=http)


@pytest.mark.parametrize(("action", "expected"), [("block", "block"),
                                                  ("answer", "answer")])
def test_maps_backend_action(action: str, expected: str) -> None:
    http = FakeHttp({"action": action, "reason": "block_list"})
    assert make(http).handle(event()) == {"action": expected}
    [call] = http.calls
    assert call["url"] == URL + "/api/v1/voice/screen"
    assert call["headers"]["X-Voice-Bridge-Secret"] == SECRET
    assert call["body"] == {"dialed": DIALED, "caller": CALLER}
    assert call["timeout"] == 1.0


def test_withheld_caller_is_sent_as_empty_string() -> None:
    http = FakeHttp({"action": "answer", "reason": "withheld"})
    assert make(http).handle(event(caller=None)) == {"action": "answer"}
    assert http.calls[0]["body"]["caller"] == ""


@pytest.mark.parametrize("response", [
    TimeoutError("timed out"),
    socket.timeout("timed out"),
    OSError("connection refused"),
    (500, b""),
    (502, b"bad gateway"),
    (404, b""),
    (422, b""),
    (200, b"not json"),
    (200, b"[]"),
    (200, b'{"action": "maybe"}'),
    (200, b"{}"),
])
def test_fails_open(response: Any) -> None:
    assert make(FakeHttp(response)).handle(event()) == {"action": "answer"}


@pytest.mark.parametrize("bad", [None, {}, {"Details": None}, "nonsense", 7])
def test_bad_event_fails_open_without_calling(bad: Any) -> None:
    http = FakeHttp()
    assert make(http).handle(bad) == {"action": "answer"}
    assert http.calls == []


def test_no_secret_or_url_fails_open() -> None:
    assert make(FakeHttp(), secret=None).handle(event()) == {"action": "answer"}
    assert make(FakeHttp(), url="").handle(event()) == {"action": "answer"}


def test_404_drops_cached_secret() -> None:
    loads: list[int] = []

    def load() -> str:
        loads.append(1)
        return SECRET

    http = FakeHttp((404, b""), {"action": "block", "reason": "ftc"})
    s = screen.Screener(URL, load, http)
    assert s.handle(event()) == {"action": "answer"}
    assert s.handle(event()) == {"action": "block"}
    assert len(loads) == 2


def test_logs_contact_id_and_decision_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    make(FakeHttp({"action": "block", "reason": "ftc"})).handle(event())
    make(FakeHttp((500, b""))).handle(event())
    text = caplog.text
    assert "contact_id=c-123 action=block" in text
    assert "error=ScreenError" in text
    assert CALLER not in text and DIALED not in text
    assert "ftc" not in text


def test_module_handler_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen, "_SCREENER", None)
    monkeypatch.delenv("BACKEND_URL", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    assert screen.handler(event(), None) == {"action": "answer"}


def test_ci_zip_includes_screen() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
                / "deploy.yml").read_text()
    zips = [line for line in workflow.splitlines() if "voice_bridge.zip\" " in line]
    assert zips, "voice bridge zip step missing"
    assert all("screen.py" in line for line in zips)
