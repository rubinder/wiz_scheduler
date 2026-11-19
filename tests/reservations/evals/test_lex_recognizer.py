import base64
import gzip
import io

import pytest

from backend.reservations.evals.lex_recognizer import (
    LexRecognizer, decode_transcript, session_id)


class FakePolly:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def synthesize_speech(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("polly down")
        return {"AudioStream": io.BytesIO(b"PCM")}


class FakeLex:
    def __init__(self, transcript: str | None, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.transcript, self.fail = transcript, fail

    def recognize_utterance(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise ConnectionError("x")
        return {"inputTranscript": self.transcript}


def _gz(text: str) -> str:
    return base64.b64encode(gzip.compress(text.encode())).decode()


def _rec(polly, lex) -> LexRecognizer:
    return LexRecognizer("BOT", "ALIAS", "en_US", "Joanna", run_id="evals_1",
                         client_factory=lambda s: polly if s == "polly" else lex
                         ).for_scenario("book:four/fri")


@pytest.mark.asyncio
async def test_request_parameters() -> None:
    polly, lex = FakePolly(), FakeLex(_gz("table for four"))
    heard = await _rec(polly, lex).hear("a table for four")
    assert polly.calls == [dict(Text="a table for four", OutputFormat="pcm",
                                SampleRate="8000", VoiceId="Joanna", Engine="neural")]
    c = lex.calls[0]
    assert (c["botId"], c["botAliasId"], c["localeId"]) == ("BOT", "ALIAS", "en_US")
    assert c["sessionId"] == "evals_1-book:four_fri"
    assert c["requestContentType"] == "audio/l16; rate=8000; channels=1"
    assert c["responseContentType"] == "text/plain; charset=utf-8"
    assert c["inputStream"] == b"PCM"
    state = gzip.decompress(base64.b64decode(c["sessionState"])).decode()
    assert '"ElicitIntent"' in state
    assert heard.text == "table for four"
    assert heard.asr_ms is not None and heard.tts_ms is not None


def test_transcript_decoding() -> None:
    assert decode_transcript(_gz("hello there")) == "hello there"
    assert decode_transcript("hello plain") == "hello plain"
    assert decode_transcript(None) is None and decode_transcript("") is None


def test_session_id_is_sanitised_and_bounded() -> None:
    s = session_id("run", "a b/c" + "x" * 200)
    assert len(s) == 100 and " " not in s and "/" not in s


@pytest.mark.asyncio
@pytest.mark.parametrize("polly_fail,lex_fail", [(True, False), (False, True)])
async def test_errors_are_not_understood(polly_fail, lex_fail) -> None:
    rec = _rec(FakePolly(polly_fail), FakeLex("x", lex_fail))
    heard = await rec.hear("hi")
    assert heard.text is None and heard.error in ("RuntimeError", "ConnectionError")
    # the next utterance still works
    ok = await _rec(FakePolly(), FakeLex(_gz("hi"))).hear("hi")
    assert ok.text == "hi"
