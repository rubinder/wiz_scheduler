"""Live speech recognition for voice eval runs (#259 step 8): each diner
line is spoken by Amazon Polly and heard by Amazon Lex V2, as a phone call's
audio would be. Used only with `--recognizer lex --live`; it costs money.

Lex keeps dialog state per session. Only the transcript is wanted, so every
utterance is sent with `ElicitIntent` as the dialog action and Lex never
drives the dialog (the agent does).
"""

import asyncio
import base64
import gzip
import json
import re
import time
from collections.abc import Callable
from typing import Any

from backend.reservations.evals.voice import Heard

PCM_RATE = "8000"
LEX_CONTENT_TYPE = "audio/l16; rate=8000; channels=1"
_SESSION_BAD = re.compile(r"[^0-9a-zA-Z._:-]")
SESSION_MAX = 100


def session_id(run_id: str, scenario_id: str) -> str:
    """The eval run id plus the scenario id, in Lex's sessionId pattern."""
    return _SESSION_BAD.sub("_", f"{run_id}-{scenario_id}")[:SESSION_MAX]


def default_client_factory(service: str) -> Any:
    import boto3  # imported here: a refused run never touches boto3

    return boto3.client(service)


def encode_header(value: dict[str, Any]) -> str:
    """Lex header payloads: JSON, gzip, base64."""
    return base64.b64encode(gzip.compress(json.dumps(value).encode())).decode()


def decode_transcript(raw: str | None) -> str | None:
    """`inputTranscript` is gzip+base64 in RecognizeUtterance responses;
    accept plain text as well."""
    if not raw:
        return None
    try:
        text = gzip.decompress(base64.b64decode(raw, validate=True)).decode()
    except Exception:
        text = raw
    return text.strip() or None


class LexRecognizer:
    name = "lex"

    def __init__(self, bot_id: str, alias_id: str, locale: str,
                 voice_id: str = "Joanna", *, run_id: str = "eval",
                 scenario_id: str = "",
                 client_factory: Callable[[str], Any] = default_client_factory) -> None:
        self.bot_id, self.alias_id, self.locale = bot_id, alias_id, locale
        self.voice_id, self.run_id, self.scenario_id = voice_id, run_id, scenario_id
        self.client_factory = client_factory
        self._clients: dict[str, Any] = {}

    def for_scenario(self, scenario_id: str) -> "LexRecognizer":
        r = LexRecognizer(self.bot_id, self.alias_id, self.locale, self.voice_id,
                          run_id=self.run_id, scenario_id=scenario_id,
                          client_factory=self.client_factory)
        r._clients = self._clients
        return r

    def _client(self, service: str) -> Any:
        if service not in self._clients:
            self._clients[service] = self.client_factory(service)
        return self._clients[service]

    def _hear_sync(self, line: str) -> Heard:
        tts_ms: int | None = None
        try:
            t0 = time.perf_counter()
            speech = self._client("polly").synthesize_speech(
                Text=line, OutputFormat="pcm", SampleRate=PCM_RATE,
                VoiceId=self.voice_id, Engine="neural")
            pcm = speech["AudioStream"].read()
            tts_ms = round((time.perf_counter() - t0) * 1000)
            t1 = time.perf_counter()
            resp = self._client("lexv2-runtime").recognize_utterance(
                botId=self.bot_id, botAliasId=self.alias_id, localeId=self.locale,
                sessionId=session_id(self.run_id, self.scenario_id),
                sessionState=encode_header(
                    {"dialogAction": {"type": "ElicitIntent"}}),
                requestContentType=LEX_CONTENT_TYPE,
                responseContentType="text/plain; charset=utf-8",
                inputStream=pcm)
            asr_ms = round((time.perf_counter() - t1) * 1000)
            return Heard(text=decode_transcript(resp.get("inputTranscript")),
                         asr_ms=asr_ms, tts_ms=tts_ms)
        except Exception as exc:  # a failed call is "not understood"; the run goes on
            return Heard(text=None, tts_ms=tts_ms, error=type(exc).__name__)

    async def hear(self, text: str) -> Heard:
        return await asyncio.to_thread(self._hear_sync, text)
