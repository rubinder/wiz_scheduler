"""The eval scenarios on the mock external provider (#259 step 9): booked
through it, one injected failure retried, two injected failures handed off.
The recordings replay in test_replay; these assert the end state."""

import random
from typing import Any

import pytest
from sqlalchemy import func, select

from backend.reservations.agent.core import RETRY_TEXT, AgentCore
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.evals.diner import ScriptedDiner
from backend.reservations.evals.replay import load_recordings, replay_core, replay_diner
from backend.reservations.evals.runner import run_scenario
from backend.reservations.evals.scenarios_io import Scenario, load_scenarios
from backend.reservations.mock_external_models import MockExternalBooking
from backend.reservations.models import Reservation
from tests.conftest import get_test_session_factory
from tests.reservations.agent.fakes import ScriptedLlm, reply, text, tool_use
from tests.reservations.evals.test_runner import ASK

pytestmark = [pytest.mark.integration]

RECORDINGS = {r["scenario_id"]: (n, r) for n, r in load_recordings()}


def test_recordings_are_hand_authored_and_exact() -> None:
    for sid in ("book_via_mock_external", "mock_external_transient_retry"):
        assert RECORDINGS[sid][1]["source"] == "hand-authored"


def test_scenarios_are_instant() -> None:
    for s in load_scenarios(ids=["book_via_mock_external", "mock_external_transient_retry"]):
        assert s.rules["provider"] == "mock_external"
        assert s.rules["mock_external"]["latency_ms"] == 0


async def _replay(sid: str) -> tuple[Any, Scenario]:
    [scenario] = load_scenarios(ids=[sid])
    name, rec = RECORDINGS[sid]
    out = await run_scenario(get_test_session_factory(), scenario,
                             core=replay_core(rec, name), diner=replay_diner(rec))
    return out, scenario


async def _counts(conv_id: str) -> tuple[list[MockExternalBooking], int, AgentConversation]:
    async with get_test_session_factory()() as db:
        conv = (await db.execute(select(AgentConversation)
                                 .where(AgentConversation.id == conv_id))).scalar_one()
        mock = list((await db.execute(select(MockExternalBooking).where(
            MockExternalBooking.location_id == conv.location_id))).scalars().all())
        res = (await db.execute(select(func.count()).select_from(Reservation).where(
            Reservation.location_id == conv.location_id))).scalar_one()
        return mock, res, conv


@pytest.mark.asyncio
async def test_book_via_mock_external_books_externally_only() -> None:
    out, _ = await _replay("book_via_mock_external")
    assert out.check.failures == []
    mock, reservations, conv = await _counts(out.conversation_id)
    assert [m.status for m in mock] == ["booked"]
    assert mock[0].party_size == 4
    assert reservations == 0
    assert conv.provider == "mock_external" and conv.outcome == "booked"


@pytest.mark.asyncio
async def test_transient_scenario_retries_once_and_books_once() -> None:
    out, _ = await _replay("mock_external_transient_retry")
    assert out.check.failures == []
    mock, reservations, conv = await _counts(out.conversation_id)
    assert [m.status for m in mock] == ["booked"]
    assert reservations == 0
    assert conv.outcome == "booked" and conv.outcome != "handed_off"
    # The diner was told about the retry.
    assert any(RETRY_TEXT in line for who, line in out.transcript if who == "host")


def _double_failure_seed(rate: float) -> str:
    """A failure seed whose find_slots draws (the first call of turn 1) fail
    twice at `rate`."""
    for n in range(10_000):
        seed = f"double-{n}"
        rng = random.Random(f"{seed}:1:find_slots")
        if rng.random() < rate and rng.random() < rate:
            return seed
    raise AssertionError("no seed")


@pytest.mark.asyncio
async def test_two_failures_hand_off_and_book_nothing() -> None:
    [scenario] = load_scenarios(ids=["mock_external_transient_retry"])
    assert scenario.rules["mock_external"]["failure_rate"] == 0.5
    llm = ScriptedLlm(
        reply(tool_use("tu_f", "find_slots", {
            "date": "2026-12-04", "earliest": "19:00", "latest": "19:00",
            "party_size": 4}), stop="tool_use"),
        reply(text("Let me get a colleague."), stop="end_turn"))
    out = await run_scenario(get_test_session_factory(), scenario, core=AgentCore(llm),
                             diner=ScriptedDiner([ASK]),
                             failure_seed=_double_failure_seed(0.5))
    mock, reservations, conv = await _counts(out.conversation_id)
    assert mock == [] and reservations == 0
    assert conv.outcome == "handed_off"
    async with get_test_session_factory()() as db:
        events = (await db.execute(select(AgentEvent).where(
            AgentEvent.conversation_id == conv.id, AgentEvent.type == "handoff"))
        ).scalars().all()
    assert [e.payload["reason"] for e in events] == ["provider_unavailable"]
    assert not out.check.passed
