"""Voice scenarios through the real voice bridge, in process (#259 step 8).

`VoiceChannel` plays the Amazon Connect Lambda: every diner line goes
through a `Recognizer` (what speech recognition made of it) and then to
`reservations.agent.voice_turn.handle`, the body of POST /voice/turn, with
`{contact_id, dialed, caller, seq, text|action}`. So the eval runs the same
gate, seq dedupe, claim, `voice.action_matches` check and transfer
screening as a phone call. Seq starts at 0 (the call's opening turn, no
input: the greeting) and goes up by one per request, as the Lambda's does.

Confirmation: a reply with a `confirm_prompt` on a confirm_* expectation is
a Lex ConfirmIntent. The scenario policy (`runner.choose_action`) answers
it, and the answer is sent as the action the Lambda maps Lex's Confirmed
(the pending confirm_*) or Denied (`decline_hold`) to, with no text.

Every reply is rendered as the Lambda renders it
(`infra/voice/bridge/ssml.py`, `render_limited`, loaded from its file so the
Lambda zip stays stdlib-only) and parsed as XML; a document that does not
parse is recorded as an `ssml_invalid` scenario error.

Recognizers: `ExactRecognizer` hears every word; `NoisyRecognizer(seed,
rate)` makes the word-level confusions a phone line makes, deterministically
per seed, scenario and turn.
"""

import importlib.util
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol
from xml.etree import ElementTree

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import settings
from backend.database import RLS_COMPANY_IDS
from backend.models import Company, OwnershipGroup
from backend.reservations.agent import recording, voice, voice_turn
from backend.reservations.agent.core import Action, AgentCore, TurnInput, TurnOutput
from backend.reservations.evals.scenarios_io import Scenario
from backend.reservations.evals.seed import SeededScenario
from backend.reservations.subscription_models import ReservationSubscription
from backend.reservations.voice_number_models import VoiceNumber
from backend.schemas.voice import VoiceTurnIn
from backend.services.rate_limit import SlidingWindowLimiter

DEFAULT_SEED = 259
# Above the most requests one eval call makes: the greeting plus a line and a
# confirmation answer for each of runner.MAX_DINER_TURNS (20) diner turns.
CALL_REQUEST_LIMIT = 64
DEFAULT_RATE = 0.15
CONFIRM_EXPECTS = ("confirm_hold", "confirm_cancel", "confirm_modify")
# The Lambda's renderer, loaded by path (it is not in the backend package and
# the Lambda zip stays stdlib-only): evals must run from a repo checkout.
SSML_PATH = Path(__file__).resolve().parents[3] / "infra" / "voice" / "bridge" / "ssml.py"
_E164 = re.compile(r"^\+[1-9][0-9]{1,14}$")

DbFactory = async_sessionmaker[AsyncSession]


# -- recognizers -------------------------------------------------------------

@dataclass(frozen=True)
class Heard:
    """What recognition made of one diner line. `text` None: nothing was
    understood (`error` says why, when something failed)."""

    text: str | None
    asr_ms: int | None = None
    tts_ms: int | None = None
    error: str | None = None


class Recognizer(Protocol):
    name: str

    def for_scenario(self, scenario_id: str) -> "Recognizer":
        """A recognizer for one scenario run (its turn count starts at 0)."""
        ...

    async def hear(self, text: str) -> Heard: ...


class ExactRecognizer:
    """Hears exactly what the diner said."""

    name = "exact"

    def for_scenario(self, scenario_id: str) -> "ExactRecognizer":
        return self

    async def hear(self, text: str) -> Heard:
        return Heard(text=text.strip() or None)


def _pairs(*groups: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Each word of a group is confused with the group's other words."""
    out: dict[str, tuple[str, ...]] = {}
    for group in groups:
        for word in group:
            out[word] = tuple(w for w in group if w != word)
    return out


_TEENS = ("thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
          "nineteen")
_TYS = ("thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
WORD_CONFUSIONS: dict[str, tuple[str, ...]] = {
    **_pairs(("four", "for"), ("eight", "ate"), ("friday", "fried"),
             ("seven", "heaven"), *zip(_TEENS, _TYS)),
    "two": ("to", "too"), "to": ("two",), "too": ("two",),
    # A spoken number written as digits is heard as the word it sounds like.
    "4": ("for",), "2": ("to", "too"), "8": ("ate",), "7": ("heaven",),
    **_pairs(*((str(t), str(10 * (t - 10))) for t in range(13, 20))),
}
LETTER_CONFUSIONS: dict[str, tuple[str, ...]] = _pairs(
    ("B", "D", "E", "P", "V"), ("M", "N"), ("S", "F"))
LONG_DIGITS = 7  # a run this long (a phone number) may lose one digit
# Whole alphanumeric runs: "7pm" or "4th" is one token and never flips.
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_CODE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")  # reservations/codes.ALPHABET


def _cased(word: str, like: str) -> str:
    if like.isupper() and len(like) > 1:
        return word.upper()
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


class NoisyRecognizer:
    """Deterministic word-level recognition errors. Every eligible token
    (a confusable word, a digit run of LONG_DIGITS or more, each confusable
    letter of a confirmation code) flips with probability `rate`, drawn from
    `random.Random` seeded with (seed, scenario, turn)."""

    name = "noisy"

    def __init__(self, seed: int = DEFAULT_SEED, rate: float = DEFAULT_RATE,
                 scenario_id: str = "") -> None:
        self.seed, self.rate, self.scenario_id = seed, rate, scenario_id
        self.turn = 0

    def for_scenario(self, scenario_id: str) -> "NoisyRecognizer":
        return NoisyRecognizer(self.seed, self.rate, scenario_id)

    def _code_letters(self, token: str, rng: random.Random) -> str:
        return "".join(
            rng.choice(LETTER_CONFUSIONS[c])
            if c in LETTER_CONFUSIONS and rng.random() < self.rate else c
            for c in token)

    def _flip(self, token: str, after_code: bool, rng: random.Random) -> str:
        if after_code and _CODE.match(token):
            return self._code_letters(token, rng)
        if token.isdigit() and len(token) >= LONG_DIGITS:
            if rng.random() < self.rate:
                i = rng.randrange(len(token))
                return token[:i] + token[i + 1:]
            return token
        alternatives = WORD_CONFUSIONS.get(token.lower())
        if alternatives and rng.random() < self.rate:
            return _cased(rng.choice(alternatives), token)
        return token

    def garble(self, text: str, rng: random.Random) -> str:
        """Tokens are whole alphanumeric runs, so only a stand-alone number
        flips. A token is a confirmation code only right after "code" (or
        "code is"), as the SSML renderer spells one."""
        out: list[str] = []
        end = 0
        words: list[str] = []  # the lowercased tokens so far
        for m in _TOKEN.finditer(text):
            token = m.group(0)
            after_code = bool(words) and (
                words[-1] == "code" or (words[-1] == "is" and words[-2:-1] == ["code"]))
            out += [text[end:m.start()], self._flip(token, after_code, rng)]
            end = m.end()
            words.append(token.lower())
        out.append(text[end:])
        return "".join(out)

    async def hear(self, text: str) -> Heard:
        rng = random.Random(f"{self.seed}:{self.scenario_id}:{self.turn}")
        self.turn += 1
        return Heard(text=self.garble(text, rng).strip() or None)


def make_recognizer(name: str, seed: int = DEFAULT_SEED) -> Recognizer:
    if name == "exact":
        return ExactRecognizer()
    if name == "noisy":
        return NoisyRecognizer(seed)
    raise ValueError(f"unknown recognizer {name!r}")


# -- SSML --------------------------------------------------------------------

_ssml: ModuleType | None = None


def ssml_module() -> ModuleType:
    """infra/voice/bridge/ssml.py, the Lambda's renderer, loaded by path."""
    global _ssml
    if _ssml is None:
        spec = importlib.util.spec_from_file_location("_eval_voice_ssml", SSML_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load the SSML renderer at {SSML_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _ssml = module
    return _ssml


def spoken_question(reply: dict[str, Any]) -> str:
    """The question the Lambda appends to the reply: none on a Close (end
    of call or transfer), else the confirm prompt."""
    if reply.get("transfer_to") or reply.get("end_call"):
        return ""
    return (reply.get("confirm_prompt") or "").strip()


def ssml_error(reply: dict[str, Any]) -> str | None:
    """None when the reply renders as the Lambda renders it and parses as
    XML; else what went wrong."""
    try:
        doc = ssml_module().render_limited(reply.get("text") or "", spoken_question(reply))
        ElementTree.fromstring(doc)
    except Exception as exc:  # any failure to render or parse is the finding
        return f"{type(exc).__name__}: {exc}"
    return None


# -- repeat detection --------------------------------------------------------

def is_repeat(previous: dict[str, Any] | None, reply: dict[str, Any]) -> bool:
    """The agent asked the diner to repeat or clarify what they just said:
    the reply is `voice.not_caught()` or `voice.inflight()` ("say that
    again") text, or it elicits again the slot the
    previous reply elicited (the one the diner just answered). Structural,
    never a model's judgement."""
    if reply.get("text") in (voice.NOT_CAUGHT_TEXT, voice.INFLIGHT_TEXT):
        return True
    asked = ((previous or {}).get("hints") or {}).get("elicit")
    again = (reply.get("hints") or {}).get("elicit")
    return asked is not None and again == asked


# -- the line ----------------------------------------------------------------

def _dialed() -> str:
    """An unused-looking NANP number for the scenario's line (646 area: never
    a scenario's staff number)."""
    return "+1646" + "".join(secrets.choice("0123456789") for _ in range(7))


async def seed_voice_line(db: AsyncSession, seeded: SeededScenario) -> str:
    """Give the seeded location what a phone call needs: an ownership group
    with an active AI host subscription and an active voice number. Returns
    the number. Commits."""
    og = OwnershipGroup(name=f"Eval {seeded.slug}")
    db.add(og)
    await db.flush()
    await db.execute(update(Company).where(Company.id == seeded.company_id)
                     .values(ownership_group_id=og.id))
    db.add(ReservationSubscription(ownership_group_id=og.id, status="active"))
    dialed = _dialed()
    db.add(VoiceNumber(company_id=seeded.company_id, ownership_group_id=og.id,
                       location_id=seeded.location_id, e164=dialed, status="active",
                       connect_phone_number_id=f"eval-{seeded.slug}"))
    await db.commit()
    return dialed


class _SpyCore:
    """The core, keeping its last `TurnOutput` so the scenario policy can
    read the structured hold or proposal behind a confirm prompt (as a
    caller hears it in the prompt)."""

    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.last: TurnOutput | None = None

    async def handle_turn(self, db: AsyncSession, conv: Any, turn: TurnInput,
                          *, now: datetime | None = None) -> TurnOutput:
        out = await self.core.handle_turn(db, conv, turn, now=now)
        self.last = out
        return out

    async def close_conversation(self, db: AsyncSession, conv: Any, *,
                                 now: datetime) -> None:
        await self.core.close_conversation(db, conv, now=now)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.core, name)


@dataclass(frozen=True)
class VoiceTurn:
    reply: dict[str, Any]
    out: TurnOutput | None  # the core's output, when the core ran this turn
    heard: Heard | None     # what recognition made of a text turn
    reservation_before: str | None
    reservation_after: str | None
    open_after: bool
    turn_ms: float          # the bridge turn, wall clock
    ssml_error: str | None

    @property
    def ended(self) -> bool:
        return bool(self.reply.get("end_call") or self.reply.get("transfer_to"))

    @property
    def confirm_asked(self) -> bool:
        """A Lex ConfirmIntent: the Lambda only asks with a prompt on a
        confirm_* expectation."""
        return bool((self.reply.get("confirm_prompt") or "").strip()) and \
            self.reply.get("expect") in CONFIRM_EXPECTS

    def spoken(self) -> str:
        """What the caller hears."""
        return " ".join(p for p in (self.reply.get("text") or "",
                                    spoken_question(self.reply)) if p)


@dataclass
class VoiceChannel:
    """One phone call into the bridge for a seeded scenario."""

    db_factory: DbFactory
    seeded: SeededScenario
    scenario: Scenario
    core: AgentCore
    recognizer: Recognizer
    dialed: str
    contact_id: str = field(default_factory=lambda: f"eval-{secrets.token_hex(8)}")
    seq: int = 0
    # Per call, and above any call's request count: the eval's turns are not
    # the process-wide limiter's business, and a fast replay must not trip it.
    limiter: SlidingWindowLimiter = field(default_factory=lambda: SlidingWindowLimiter(
        max_requests=CALL_REQUEST_LIMIT, window_seconds=60))
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        self.spy = _SpyCore(self.core)
        self.recognizer = self.recognizer.for_scenario(self.scenario.id)
        phone = self.scenario.diner.facts.get("phone") or ""
        self.caller: str | None = phone if _E164.match(phone) else None

    async def _links(self) -> tuple[str | None, bool]:
        async with self.db_factory() as db:
            db.info[RLS_COMPANY_IDS] = [self.seeded.company_id]
            conv = await recording.find_voice_conversation(
                db, self.seeded.company_id, self.seeded.location_id, self.contact_id)
            if conv is None:
                return None, False
            self.conversation_id = conv.id
            return conv.reservation_id, conv.status == "open"

    async def turn(self, *, now: datetime, text: str | None = None,
                   action: Action | None = None) -> VoiceTurn:
        """One request to the bridge: the opening turn (neither), a diner
        line (heard through the recognizer first) or a confirmation
        answer. The next seq each time."""
        heard = await self.recognizer.hear(text) if text is not None else None
        # As the Lambda sends a transcript: stripped, cut to the bridge's
        # limit, empty as no input.
        sent = ((heard.text or "").strip()[:settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS]
                or None) if heard else None
        body = VoiceTurnIn.model_validate({
            "contact_id": self.contact_id, "dialed": self.dialed,
            "caller": self.caller, "seq": self.seq,
            "text": sent, "action": action,
            "timings": {"asr_ms": heard.asr_ms if heard else None,
                        "tts_ms": heard.tts_ms if heard else None}})
        self.seq += 1
        before, _ = await self._links()
        self.spy.last = None
        async with self.db_factory() as db:
            started = time.perf_counter()
            reply = await voice_turn.handle(db, self.spy, body, now=now,  # type: ignore[arg-type]
                                            limiter=self.limiter)
            turn_ms = (time.perf_counter() - started) * 1000
        after, open_after = await self._links()
        return VoiceTurn(reply=reply, out=self.spy.last, heard=heard,
                         reservation_before=before, reservation_after=after,
                         open_after=open_after, turn_ms=turn_ms,
                         ssml_error=ssml_error(reply))


async def open_call(db_factory: DbFactory, seeded: SeededScenario, scenario: Scenario,
                    core: AgentCore, recognizer: Recognizer) -> VoiceChannel:
    """Seed the scenario's line and return a channel for one call."""
    async with db_factory() as db:
        dialed = await seed_voice_line(db, seeded)
    return VoiceChannel(db_factory, seeded, scenario, core, recognizer, dialed)
