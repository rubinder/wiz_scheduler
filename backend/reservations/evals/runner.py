"""Run eval scenarios: seed, converse through the real AgentCore, grade.

A run seeds the scenario, starts a conversation on its channel, then loops:
the diner's text goes to `AgentCore.handle_turn` (each turn claimed with
`recording.claim_turn` and committed first, on its own session, exactly as
the public route does), and the scenario's policy (never the model) decides
whether the diner presses a button. `now` starts at the scenario's fixed
instant and advances 30 seconds per diner turn.

A run ends when the conversation ends, the diner says `[[DONE]]`, the
policy presses a confirm button, or after MAX_DINER_TURNS diner turns. A
conversation still open then (e.g. a submitted request, which stays open)
is closed the way the turn cap closes it (`AgentCore.close_conversation`),
and the database end state is graded by `checker.check`.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import RLS_COMPANY_IDS
from backend.reservations.agent import recording, tools
from backend.reservations.agent.core import Action, AgentCore, TurnInput, TurnOutput
from backend.reservations.agent.models import AgentConversation
from backend.reservations.evals.checker import CheckResult, check
from backend.reservations.evals.diner import DONE, Diner, Transcript
from backend.reservations.evals.scenarios_io import Scenario
from backend.reservations.evals.seed import SeededScenario, seed_scenario

logger = logging.getLogger(__name__)

MAX_DINER_TURNS = 20
TURN_STEP = timedelta(seconds=30)

_BUTTONS = {tools.EXPECT_CONFIRM_HOLD: "Confirm / No",
            tools.EXPECT_CONFIRM_MODIFY: "Confirm change / No",
            tools.EXPECT_CONFIRM_CANCEL: "Confirm cancellation / No"}
_PRESSED = {"confirm_hold": "Confirm", "confirm_modify": "Confirm change",
            "confirm_cancel": "Confirm cancellation", "decline_hold": "No"}
_CONFIRMS = ("confirm_hold", "confirm_modify", "confirm_cancel")

DbFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class ScenarioOutcome:
    scenario_id: str
    conversation_id: str | None
    check: CheckResult
    transcript: Transcript
    diner_turns: int = 0
    # ended | done | action | turn_cap | not_claimed | error
    stop: str = ""


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
    """`modify` / `cancel` when the diner holds a booking code (with / without
    an expected end booking), else `book`."""
    if "confirmation_code" not in scenario.diner.facts:
        return "book"
    return "modify" if scenario.expect.booking is not None else "cancel"


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
    if out.expect == tools.EXPECT_CONFIRM_MODIFY and goal == "modify":
        proposed = out.proposal.get("proposed") if isinstance(out.proposal, dict) else None
        return "confirm_modify" if _matches(proposed, scenario) else None
    if out.expect == tools.EXPECT_CONFIRM_CANCEL and goal == "cancel":
        return "confirm_cancel"
    return None


# -- running -----------------------------------------------------------------

async def _turn(db_factory: DbFactory, seeded: SeededScenario, conv_id: str,
                core: AgentCore, turn: TurnInput, now: datetime) -> TurnOutput | None:
    """One turn on its own session, claimed as the public route claims it.
    None when the claim is refused (the conversation is closed)."""
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
        return await core.handle_turn(db, conv, turn, now=now)


async def run_scenario(db_factory: DbFactory, scenario: Scenario, *, core: AgentCore,
                       diner: Diner, now_override: datetime | None = None
                       ) -> ScenarioOutcome:
    if now_override is not None:
        scenario = replace(scenario, now=now_override)
    async with db_factory() as db:
        seeded = await seed_scenario(db, scenario)
    async with db_factory() as db:
        db.info[RLS_COMPANY_IDS] = [seeded.company_id]
        conv = await recording.start_conversation(
            db, seeded.company_id, seeded.location_id, scenario.channel)
        conv_id = conv.id
        await db.commit()

    transcript: Transcript = []
    state = PolicyState()
    diner_turns = 0
    now = scenario.now
    stop = "turn_cap"
    while diner_turns < MAX_DINER_TURNS:
        said = await diner.reply(scenario, list(transcript))
        if DONE in said:
            stop = "done"
            break
        now = scenario.now + TURN_STEP * diner_turns
        diner_turns += 1
        transcript.append(("diner", said))
        out = await _turn(db_factory, seeded, conv_id, core,
                          TurnInput(text=said, action=None), now)
        if out is None:
            stop = "not_claimed"
            break
        transcript.append(("host", visible_text(out)))
        if out.ended:
            stop = "ended"
            break
        action = choose_action(scenario, out, state)
        if action is None:
            continue
        transcript.append(("diner", f"[pressed {_PRESSED[action]}]"))
        out = await _turn(db_factory, seeded, conv_id, core,
                          TurnInput(text=None, action=action), now)
        if out is None:
            stop = "not_claimed"
            break
        transcript.append(("host", visible_text(out)))
        if action in _CONFIRMS:
            stop = "action"
            break
        if out.ended:
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
    return ScenarioOutcome(scenario_id=scenario.id, conversation_id=conv_id,
                           check=result, transcript=transcript,
                           diner_turns=diner_turns, stop=stop)


async def run_suite(db_factory: DbFactory, scenarios: list[Scenario], *,
                    core: AgentCore, diner_factory: Callable[[Scenario], Diner],
                    repeat: int = 1) -> SuiteResult:
    """Every scenario `repeat` times, serially. A run that raises is recorded
    as a failed outcome and the suite goes on."""
    outcomes: list[ScenarioOutcome] = []
    by_scenario: dict[str, list[bool]] = {}
    for scenario in scenarios:
        for _ in range(repeat):
            try:
                outcome = await run_scenario(db_factory, scenario, core=core,
                                             diner=diner_factory(scenario))
            except Exception as exc:
                logger.exception("eval run failed (scenario %s)", scenario.id)
                outcome = ScenarioOutcome(
                    scenario_id=scenario.id, conversation_id=None,
                    check=CheckResult(passed=False,
                                      failures=[f"runner error: {exc!r}"]),
                    transcript=[], stop="error")
            outcomes.append(outcome)
            by_scenario.setdefault(scenario.id, []).append(outcome.check.passed)
    return SuiteResult(outcomes=outcomes, by_scenario=by_scenario)
