"""Run storage and the report. The report math needs no database."""

import pytest
from sqlalchemy import select

from backend.reservations.evals.checker import CheckResult
from backend.reservations.evals.models import EvalResult, EvalRun
from backend.reservations.evals.runner import ScenarioOutcome, SuiteResult
from backend.reservations.evals.store import (
    RunMeta,
    format_report,
    load_results,
    prompt_hashes,
    store_run,
    write_run_file,
)
from tests.conftest import get_test_session_factory


def _metrics(turns: int, calls: int, cost: float, p50: float, p95: float,
             outcome: str = "booked", book_goal: bool = True,
             turn_ms: list[float] | None = None) -> dict:
    return {"diner_turns": turns, "model_calls": calls, "total_cost_usd": cost,
            "llm_p50_ms": p50, "llm_p95_ms": p95, "outcome": outcome,
            "book_goal": book_goal, "turn_ms": turn_ms or []}


def _result(sid: str, attempt: int, passed: bool, **m) -> EvalResult:
    return EvalResult(scenario_id=sid, attempt=attempt, passed=passed,
                      failures=[], metrics=_metrics(**m), conversation_snapshot={})


def _run() -> EvalRun:
    return EvalRun(id="run00001", git_sha="abcdef1234567890", agent_model="m-a",
                   diner_model="m-d", prompt_hash_web="w", prompt_hash_voice="v",
                   mode="live", repeat=2)


def test_report_math_with_repeats() -> None:
    results = [
        _result("alpha", 1, True, turns=2, calls=4, cost=0.01, p50=100, p95=200,
                turn_ms=[1000, 3000]),
        _result("alpha", 2, False, turns=4, calls=6, cost=0.03, p50=300, p95=500,
                outcome="abandoned", turn_ms=[2000, 4000]),
        _result("beta", 1, True, turns=3, calls=3, cost=0.02, p50=200, p95=250,
                outcome="abandoned", book_goal=False, turn_ms=[500]),
        _result("beta", 2, False, turns=5, calls=5, cost=0.04, p50=400, p95=900,
                outcome="failed", book_goal=False, turn_ms=[9000]),
    ]
    text = format_report(_run(), results)
    lines = text.splitlines()
    assert "llm_p50" in lines[1] and "turn_p95" in lines[1]
    alpha = next(ln for ln in lines if ln.startswith("alpha"))
    beta = next(ln for ln in lines if ln.startswith("beta"))
    # turn p50 / p95 pool every turn of every attempt (nearest rank).
    assert alpha.split() == ["alpha", "1/2", "3.0", "5.0", "0.0400", "200", "500",
                             "2000", "4000"]
    assert beta.split() == ["beta", "1/2", "4.0", "4.0", "0.0600", "300", "900",
                            "500", "9000"]
    assert "pass rate 2/4 (50%)" in text
    # Six turns: 500 1000 2000 3000 4000 9000.
    assert "turn p50 2000 ms, p95 9000 ms" in text
    assert "llm call p50 250 ms" in text
    # Both alpha attempts (book goal) cost 0.04; one passed.
    assert "cost per booking $0.0400 (1 passing of 2 book-goal attempts)" in text
    assert "cost per conversation $0.0250" in text


def test_cost_per_booking_is_na_without_a_passing_booking() -> None:
    results = [_result("a", 1, False, turns=1, calls=1, cost=0.01, p50=1, p95=1)]
    text = format_report(_run(), results)
    assert "cost per booking n/a" in text
    assert "cost per conversation $0.0100" in text
    assert "pass rate 0/1 (0%)" in text


def test_report_of_nothing_does_not_divide_by_zero() -> None:
    text = format_report(_run(), [])
    assert "pass rate 0/0 (0%)" in text
    assert "cost per conversation n/a" in text
    assert "turn p50 - ms, p95 - ms" in text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_run_persists_metadata_and_results() -> None:
    web, voice = prompt_hashes()
    meta = RunMeta(git_sha="deadbeef", agent_model="claude-x", diner_model="claude-y",
                   prompt_hash_web=web, prompt_hash_voice=voice, prompt_version=voice[:12], mode="replay",
                   repeat=2, notes="unit")
    ok = CheckResult(passed=True, metrics=_metrics(2, 3, 0.01, 10, 20))
    bad = CheckResult(passed=False, failures=["no booking"], metrics={})
    suite = SuiteResult(outcomes=[
        ScenarioOutcome("s1", "conv0001", ok, [("diner", "hi"), ("host", "hello")],
                        diner_turns=1, stop="action", presses=[("confirm_hold", "action")]),
        ScenarioOutcome("s1", "conv0002", bad, [], stop="error"),
    ])
    factory = get_test_session_factory()
    async with factory() as db:
        run = await store_run(db, suite, meta)
        run_id = run.id
        await db.commit()
    async with factory() as db:
        run = (await db.execute(select(EvalRun).where(EvalRun.id == run_id))).scalar_one()
        assert (run.git_sha, run.agent_model, run.diner_model) == (
            "deadbeef", "claude-x", "claude-y")
        assert (run.prompt_hash_web, run.prompt_hash_voice) == (web, voice)
        assert (run.mode, run.repeat, run.notes) == ("replay", 2, "unit")
        assert run.created_at is not None
        rows = await load_results(db, run_id)
        assert [(r.scenario_id, r.attempt, r.passed) for r in rows] == [
            ("s1", 1, True), ("s1", 2, False)]
        assert rows[1].failures == ["no booking"]
        assert rows[0].metrics["model_calls"] == 3
        assert rows[0].conversation_snapshot["transcript"] == [["diner", "hi"], ["host", "hello"]]
        assert rows[0].conversation_snapshot["presses"] == [["confirm_hold", "action"]]
        assert rows[0].conversation_snapshot["conversation_id"] == "conv0001"
        assert "s1" in format_report(run, rows)
        await db.delete(run)  # results cascade
        await db.commit()
    async with factory() as db:
        assert await load_results(db, run_id) == []


def test_prompt_hashes_are_stable_and_per_channel() -> None:
    web, voice = prompt_hashes()
    assert (web, voice) == prompt_hashes()
    assert web != voice
    assert len(web) == len(voice) == 64


def test_write_run_file(tmp_path) -> None:
    import json
    from datetime import datetime, timezone

    run = _run()
    run.created_at = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    results = [_result("alpha", 1, True, turns=2, calls=4, cost=0.01, p50=100, p95=200)]
    meta = RunMeta(git_sha="abc", agent_model="m-a", diner_model="m-d",
                   prompt_hash_web="w", prompt_hash_voice="v", prompt_version="v", mode="live", notes="n")
    path = write_run_file(run, results, meta, "the report", tmp_path / "evals-runs")
    assert path == tmp_path / "evals-runs" / "run00001.json"
    doc = json.loads(path.read_text())
    assert doc["id"] == "run00001"
    assert doc["created_at"] == "2026-11-18T12:00:00+00:00"
    assert doc["meta"]["notes"] == "n" and doc["meta"]["mode"] == "live"
    assert doc["results"][0]["scenario_id"] == "alpha"
    assert doc["results"][0]["metrics"]["model_calls"] == 4
    assert doc["report"] == "the report"


def test_no_migration_creates_eval_tables() -> None:
    """Decision 36 (Ruling R): eval tables are metadata only."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions"
    for f in versions.glob("*.py"):
        assert "eval_runs" not in f.read_text(), f.name
