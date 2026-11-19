"""Run eval scenarios: seed, converse through the real AgentCore, grade.

A run seeds the scenario, starts a conversation on its channel, then loops:
the diner's text goes to `AgentCore.handle_turn` (each turn claimed with
`recording.claim_turn` and committed first, on its own session, exactly as
the public route does), and the scenario's policy (never the model) decides
whether the diner presses a button. `now` starts at the scenario's fixed
instant and advances 30 seconds per diner turn; a button press runs
ACTION_STEP after the text turn that raised it.

A run ends when the conversation ends, the diner says `[[DONE]]` (any text
before it is sent first), a confirm button the policy pressed succeeds, the
diner replies empty twice, or after MAX_DINER_TURNS diner turns. A confirm
that fails (the hold expired, the change could not be applied) does not end
the run: the diner may pick again. A
conversation still open then (e.g. a submitted request, which stays open)
is closed the way the turn cap closes it (`AgentCore.close_conversation`),
and the database end state is graded by `checker.check`. A run that stopped
because the diner went silent, a turn was not claimed or the runner errored
(FAILED_STOPS) fails whatever the end state.

Voice: with a recognizer set, a `voice` scenario runs through
`evals.voice.VoiceChannel` instead: the real voice bridge turn
(`agent.voice_turn.handle`) in process, every diner line first heard by the
recognizer, a confirm prompt answered by the same policy as a press (the
action the Lambda maps Lex's yes / no to). The call opens with the bridge's
greeting turn. Every reply's SSML must parse (`ssml_invalid` otherwise),
and `metrics["voice"]["repeats"]` lists, per bridge request after the
greeting, its kind (`text`, a diner line; `action`, a confirmation answer)
and whether the agent asked again (`voice.is_repeat`). Without a recognizer, or on the web channel, nothing
changes.

Turn latency: each diner turn (a typed line or a button press) times
`core.handle_turn` on the wall clock, the wait a diner sees, as `turn_ms`;
the metrics carry the list and its p50 / p95.
"""

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import RLS_COMPANY_IDS
from backend.reservations.agent import recording, tools
from backend.reservations.agent.core import Action, AgentCore, TurnInput, TurnOutput
from backend.reservations.agent.models import AgentConversation
from backend.reservations.evals.checker import CheckResult, check, percentile
from backend.reservations.evals.diner import DONE, Diner, Transcript
from backend.reservations.evals.scenarios_io import Scenario
from backend.reservations.evals.seed import SeededScenario, seed_scenario
from backend.reservations.evals.voice import Recognizer, VoiceChannel, is_repeat, open_call

logger = logging.getLogger(__name__)

MAX_DINER_TURNS = 20
TURN_STEP = timedelta(seconds=30)
ACTION_STEP = timedelta(seconds=5)
EMPTY_STAND_IN = "Sorry, could you repeat that?"

_BUTTONS = {tools.EXPECT_CONFIRM_HOLD: "Confirm / No",
            tools.EXPECT_CONFIRM_MODIFY: "Confirm change / No",
            tools.EXPECT_CONFIRM_CANCEL: "Confirm cancellation / No"}
_PRESSED = {"confirm_hold": "Confirm", "confirm_modify": "Confirm change",
            "confirm_cancel": "Confirm cancellation", "decline_hold": "No"}
_CONFIRMS = ("confirm_hold", "confirm_modify", "confirm_cancel")
# Stops that mean the run did not play out, whatever the end state: a run
# that stopped this way is a failure even if the checker found nothing.
FAILED_STOPS = ("diner_empty", "not_claimed", "error")

DbFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class ScenarioOutcome:
    scenario_id: str
    conversation_id: str | None
    check: CheckResult
    transcript: Transcript
    diner_turns: int = 0
    # ended | done | action | turn_cap | diner_empty | not_claimed | error
    stop: str = ""
    # Every button press: (action, result), result "action" when it
    # succeeded, "action_failed" when it did not, "declined" for a No.
    presses: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SuiteResult:
    outcomes: list[ScenarioOutcome]
    by_scenario: dict[str, list[bool]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.check.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.outcomes else 0.0


# -- what the diner sees, and what the diner presses ------------------------

def _slot_text(slot: dict[str, Any] | None) -> str:
    if not isinstance(slot, dict):
        return ""
    return (f"{slot.get('party_size')} people on {slot.get('local_date')} "
            f"at {slot.get('local_time')}")


def visible_text(out: TurnOutput) -> str:
    """The reply as a diner sees it: the text, plus a note of any buttons and
    the card they act on. Never tool calls."""
    buttons = _BUTTONS.get(out.expect)
    if buttons is None:
        return out.reply_text
    detail = ""
    if out.expect == tools.EXPECT_CONFIRM_HOLD:
        detail = _slot_text(out.hold)
    elif isinstance(out.proposal, dict):
        if out.proposal.get("kind") == "modify":
            detail = (f"change {_slot_text(out.proposal.get('current'))} to "
                      f"{_slot_text(out.proposal.get('proposed'))}")
        else:
            detail = f"cancel {_slot_text(out.proposal.get('current'))}"
    note = f"[buttons shown: {buttons}{' for ' + detail if detail else ''}]"
    return f"{out.reply_text}\n{note}"


@dataclass
class PolicyState:
    declined: bool = False


def _goal(scenario: Scenario) -> str:
    """The scenario's `goal_kind`; without one, inferred: `change` / `cancel`
    when the diner holds a booking code (with / without an expected end
    booking), else `book`."""
    if scenario.goal_kind is not None:
        return scenario.goal_kind
    if "confirmation_code" not in scenario.diner.facts:
        return "book"
    return "change" if scenario.expect.booking is not None else "cancel"


def _matches(slot: Any, scenario: Scenario) -> bool:
    """The slot is the goal's party size, date and time (any slot when the
    scenario expects no booking)."""
    exp = scenario.expect.booking
    if exp is None:
        return True
    return isinstance(slot, dict) and (
        slot.get("party_size") == exp.party_size
        and slot.get("local_date") == exp.local_date
        and slot.get("local_time") == exp.local_time)


def choose_action(scenario: Scenario, out: TurnOutput, state: PolicyState
                  ) -> Action | None:
    """The button the scenario's policy presses for this reply, if any."""
    if scenario.policy == "never_confirm":
        return None
    if out.expect == tools.EXPECT_CONFIRM_HOLD and out.hold is not None:
        if scenario.policy == "decline_first_offer" and not state.declined:
            state.declined = True
            return "decline_hold"
        return "confirm_hold" if _matches(out.hold, scenario) else None
    goal = _goal(scenario)
    if out.expect == tools.EXPECT_CONFIRM_MODIFY and goal == "change":
        proposed = out.proposal.get("proposed") if isinstance(out.proposal, dict) else None
        return "confirm_modify" if _matches(proposed, scenario) else None
    if out.expect == tools.EXPECT_CONFIRM_CANCEL and goal == "cancel":
        return "confirm_cancel"
    return None


# -- running -----------------------------------------------------------------

@dataclass(frozen=True)
class _Turn:
    out: TurnOutput
    reservation_before: str | None
    reservation_after: str | None
    open_after: bool
    turn_ms: float  # core.handle_turn, wall clock


async def _turn(db_factory: DbFactory, seeded: SeededScenario, conv_id: str,
                core: AgentCore, turn: TurnInput, now: datetime) -> _Turn | None:
    """One turn on its own session, claimed as the public route claims it,
    with the conversation's booking link before and after. None when the
    claim is refused (the conversation is closed)."""
    async with db_factory() as db:
        db.info[RLS_COMPANY_IDS] = [seeded.company_id]
        conv = (await db.execute(
            select(AgentConversation)
            .where(AgentConversation.id == conv_id,
                   AgentConversation.company_id == seeded.company_id)
        )).scalar_one()
        claim = await recording.claim_turn(db, conv)
        await db.commit()
        if claim != "claimed":
            return None
        before = conv.reservation_id
        started = time.perf_counter()
        out = await core.handle_turn(db, conv, turn, now=now)
        turn_ms = (time.perf_counter() - started) * 1000
        after = (await db.execute(
            select(AgentConversation.reservation_id, AgentConversation.status)
            .where(AgentConversation.id == conv_id,
                   AgentConversation.company_id == seeded.company_id)
        )).one()
        return _Turn(out, before, after.reservation_id, after.status == "open",
                     turn_ms)


def _press_succeeded(t: _Turn) -> bool:
    """A confirm worked: the conversation ended (booked, modified,
    cancelled) or the press linked a new booking (a submitted request)."""
    return t.out.ended or not t.open_after or (
        t.reservation_after is not None and t.reservation_after != t.reservation_before)


@dataclass(frozen=True)
class _Step:
    """One request on either channel, as the loop needs it."""

    host: str                # what the diner sees or hears
    out: TurnOutput | None   # for the policy
    ended: bool
    confirmable: bool        # the policy may answer (web: a button; voice: a prompt)
    press_ok: bool
    turn_ms: float
    repeat: bool = False
    ssml_error: str | None = None
    heard: str | None = None
    heard_error: str | None = None
    asr_ms: int | None = None
    tts_ms: int | None = None


class _WebChannel:
    def __init__(self, db_factory: DbFactory, seeded: SeededScenario, conv_id: str,
                 core: AgentCore) -> None:
        self.db_factory, self.seeded, self.conv_id, self.core = (
            db_factory, seeded, conv_id, core)

    async def send(self, turn: TurnInput, now: datetime) -> _Step | None:
        t = await _turn(self.db_factory, self.seeded, self.conv_id, self.core, turn, now)
        if t is None:
            return None
        return _Step(host=visible_text(t.out), out=t.out, ended=t.out.ended,
                     confirmable=True, press_ok=_press_succeeded(t), turn_ms=t.turn_ms)


class _VoiceAdapter:
    def __init__(self, channel: VoiceChannel) -> None:
        self.channel = channel
        self.previous: dict[str, Any] | None = None

    async def open(self, now: datetime) -> _Step:
        return self._step(await self.channel.turn(now=now))

    async def send(self, turn: TurnInput, now: datetime) -> _Step:
        return self._step(await self.channel.turn(now=now, text=turn.text,
                                                  action=turn.action))

    def _step(self, v: Any) -> _Step:
        repeat = is_repeat(self.previous, v.reply, v.heard)
        self.previous = v.reply
        ok = v.ended or not v.open_after or (
            v.reservation_after is not None and v.reservation_after != v.reservation_before)
        return _Step(host=v.spoken(), out=v.out, ended=v.ended,
                     confirmable=v.confirm_asked, press_ok=ok, turn_ms=v.turn_ms,
                     repeat=repeat, ssml_error=v.ssml_error,
                     heard=v.heard.text if v.heard else None,
                     heard_error=v.heard.error if v.heard else None,
                     asr_ms=v.heard.asr_ms if v.heard else None,
                     tts_ms=v.heard.tts_ms if v.heard else None)


async def run_scenario(db_factory: DbFactory, scenario: Scenario, *, core: AgentCore,
                       diner: Diner, now_override: datetime | None = None,
                       recognizer: Recognizer | None = None,
                       voice_knobs: dict[str, Any] | None = None) -> ScenarioOutcome:
    if now_override is not None:
        scenario = replace(scenario, now=now_override)
    async with db_factory() as db:
        seeded = await seed_scenario(db, scenario, voice_knobs)

    transcript: Transcript = []
    presses: list[tuple[str, str]] = []
    turn_ms: list[float] = []
    repeats: list[dict[str, Any]] = []
    heard: list[str | None] = []
    asr_ms: list[int] = []
    tts_ms: list[int] = []
    ssml_errors: list[str] = []
    rec_errors: list[str] = []
    state = PolicyState()
    diner_turns = empties = 0
    now = scenario.now
    stop = "turn_cap"

    channel: _WebChannel | _VoiceAdapter
    if recognizer is not None and scenario.channel == "voice":
        call = await open_call(db_factory, seeded, scenario, core, recognizer)
        channel = _VoiceAdapter(call)
        opening = await channel.open(now)
        if opening.ssml_error:
            ssml_errors.append(opening.ssml_error)
        if call.conversation_id is None:
            raise RuntimeError(f"the voice line did not answer: {opening.host!r}")
        conv_id = call.conversation_id
        transcript.append(("host", opening.host))
    else:
        async with db_factory() as db:
            db.info[RLS_COMPANY_IDS] = [seeded.company_id]
            conv = await recording.start_conversation(
                db, seeded.company_id, seeded.location_id, scenario.channel)
            conv_id = conv.id
            await db.commit()
        channel = _WebChannel(db_factory, seeded, conv_id, core)

    def note(t: _Step, kind: str) -> None:
        turn_ms.append(round(t.turn_ms, 1))
        transcript.append(("host", t.host))
        repeats.append({"kind": kind, "repeat": t.repeat})
        if t.ssml_error:
            ssml_errors.append(t.ssml_error)
    while diner_turns < MAX_DINER_TURNS:
        said = (await diner.reply(scenario, list(transcript))).strip()
        last = DONE in said
        if last:
            said = said.replace(DONE, "").strip()
            if not said:
                stop = "done"
                break
        elif not said:
            empties += 1
            if empties > 1:
                stop = "diner_empty"
                break
            said = EMPTY_STAND_IN
        now = scenario.now + TURN_STEP * diner_turns
        diner_turns += 1
        transcript.append(("diner", said))
        t = await channel.send(TurnInput(text=said, action=None), now)
        if t is None:
            stop = "not_claimed"
            break
        heard.append(t.heard)
        if t.heard_error:
            rec_errors.append(t.heard_error)
        if t.asr_ms is not None:
            asr_ms.append(t.asr_ms)
        if t.tts_ms is not None:
            tts_ms.append(t.tts_ms)
        note(t, "text")
        if last:
            stop = "done"
            break
        if t.ended:
            stop = "ended"
            break
        action = (choose_action(scenario, t.out, state)
                  if t.confirmable and t.out is not None else None)
        if action is None:
            continue
        transcript.append(("diner", f"[pressed {_PRESSED[action]}]"))
        now = now + ACTION_STEP
        t = await channel.send(TurnInput(text=None, action=action), now)
        if t is None:
            stop = "not_claimed"
            break
        note(t, "action")
        if action in _CONFIRMS:
            ok = t.press_ok
            presses.append((action, "action" if ok else "action_failed"))
            if ok:
                stop = "action"
                break
        else:
            presses.append((action, "declined"))
        if t.ended:
            stop = "ended"
            break

    async with db_factory() as db:
        db.info[RLS_COMPANY_IDS] = [seeded.company_id]
        conv = (await db.execute(
            select(AgentConversation)
            .where(AgentConversation.id == conv_id,
                   AgentConversation.company_id == seeded.company_id)
        )).scalar_one()
        await core.close_conversation(db, conv, now=now)
        result = await check(db, seeded, conv, scenario)
    metrics = {
        **result.metrics, "book_goal": scenario.book_goal, "turn_ms": turn_ms,
        "turn_p50_ms": percentile(turn_ms, 50), "turn_p95_ms": percentile(turn_ms, 95)}
    if isinstance(channel, _VoiceAdapter):
        metrics["voice"] = {"recognizer": channel.channel.recognizer.name,
                            "repeats": repeats, "heard": heard,
                            "asr_ms": asr_ms, "tts_ms": tts_ms,
                            "ssml_errors": ssml_errors,
                            "errors": rec_errors}
    failures = [*result.failures, *(f"ssml_invalid: {e}" for e in ssml_errors)]
    result = CheckResult(passed=not failures, failures=failures, metrics=metrics)
    if stop in FAILED_STOPS:
        result = CheckResult(passed=False,
                             failures=[*result.failures, f"run stopped: {stop}"],
                             metrics=result.metrics)
    return ScenarioOutcome(scenario_id=scenario.id, conversation_id=conv_id,
                           check=result, transcript=transcript,
                           diner_turns=diner_turns, stop=stop, presses=presses)


async def run_suite(db_factory: DbFactory, scenarios: list[Scenario], *,
                    core: AgentCore, diner_factory: Callable[[Scenario], Diner],
                    repeat: int = 1, recognizer: Recognizer | None = None,
                    voice_knobs: dict[str, Any] | None = None) -> SuiteResult:
    """Every scenario `repeat` times, serially. A run that raises is recorded
    as a failed outcome and the suite goes on."""
    outcomes: list[ScenarioOutcome] = []
    by_scenario: dict[str, list[bool]] = {}
    for scenario in scenarios:
        for _ in range(repeat):
            try:
                outcome = await run_scenario(db_factory, scenario, core=core,
                                             diner=diner_factory(scenario),
                                             recognizer=recognizer,
                                             voice_knobs=voice_knobs)
            except Exception as exc:
                logger.exception("eval run failed (scenario %s)", scenario.id)
                outcome = ScenarioOutcome(
                    scenario_id=scenario.id, conversation_id=None,
                    check=CheckResult(passed=False,
                                      failures=[f"runner error: {exc!r}"],
                                      metrics={"book_goal": scenario.book_goal}),
                    transcript=[], stop="error")
            outcomes.append(outcome)
            by_scenario.setdefault(scenario.id, []).append(outcome.check.passed)
    return SuiteResult(outcomes=outcomes, by_scenario=by_scenario)
