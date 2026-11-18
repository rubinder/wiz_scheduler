"""Persist an eval run and render its report.

`prompt_hashes` is the sha256 of `build_system_prompt` for a fixed reference
location and rules, per channel. It changes whenever the prompt template
changes, which is its purpose: two runs with different hashes ran different
prompts.
"""

import hashlib
import os
import statistics
import subprocess
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent.prompt import build_system_prompt
from backend.reservations.evals.models import EvalResult, EvalRun
from backend.reservations.evals.runner import SuiteResult
from backend.reservations.rules import RulesSchema

REFERENCE_LOCATION = "Reference Bistro"
REFERENCE_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class RunMeta:
    git_sha: str
    agent_model: str
    diner_model: str
    prompt_hash_web: str
    prompt_hash_voice: str
    mode: str  # "live" | "replay"
    repeat: int = 1
    notes: str | None = None


def prompt_hash(channel: str) -> str:
    prompt = build_system_prompt(REFERENCE_LOCATION, REFERENCE_TIMEZONE,
                                 RulesSchema(), channel)
    return hashlib.sha256(prompt.encode()).hexdigest()


def prompt_hashes() -> tuple[str, str]:
    """(web, voice)."""
    return prompt_hash("web"), prompt_hash("voice")


def git_sha() -> str:
    """Env `GIT_SHA`, else `git rev-parse HEAD`, else "unknown"."""
    env = os.environ.get("GIT_SHA")
    if env:
        return env
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def store_run(db: AsyncSession, suite: SuiteResult, meta: RunMeta) -> EvalRun:
    """Insert the run and one result per outcome (attempt numbered per
    scenario in run order). Flushes; the caller commits."""
    run = EvalRun(git_sha=meta.git_sha, agent_model=meta.agent_model,
                  diner_model=meta.diner_model,
                  prompt_hash_web=meta.prompt_hash_web,
                  prompt_hash_voice=meta.prompt_hash_voice,
                  mode=meta.mode, repeat=meta.repeat, notes=meta.notes)
    db.add(run)
    await db.flush()
    attempts: dict[str, int] = {}
    for o in suite.outcomes:
        attempts[o.scenario_id] = attempts.get(o.scenario_id, 0) + 1
        db.add(EvalResult(
            run_id=run.id, scenario_id=o.scenario_id,
            attempt=attempts[o.scenario_id], passed=o.check.passed,
            failures=list(o.check.failures), metrics=dict(o.check.metrics),
            conversation_snapshot={
                "conversation_id": o.conversation_id, "stop": o.stop,
                "diner_turns": o.diner_turns,
                "transcript": [list(t) for t in o.transcript],
                "presses": [list(p) for p in o.presses]}))
    await db.flush()
    return run


async def load_results(db: AsyncSession, run_id: str) -> list[EvalResult]:
    return list((await db.execute(
        select(EvalResult).where(EvalResult.run_id == run_id)
        .order_by(EvalResult.scenario_id, EvalResult.attempt))).scalars().all())


def _nums(results: Sequence[EvalResult], key: str) -> list[float]:
    return [float(r.metrics[key]) for r in results
            if isinstance(r.metrics.get(key), (int, float))]


def _med(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _num(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_report(run: EvalRun, results: Sequence[EvalResult]) -> str:
    """A fixed-width table per scenario, then totals.

    Per scenario: pass fraction, median diner turns, median model calls,
    total cost, and llm latency (p50: median of the attempts' p50; p95: the
    worst attempt's p95). Totals: pass rate, median turn latency (median of
    every attempt's llm p50), and cost per booking (total cost over passing
    attempts whose outcome was "booked"; "n/a" when none)."""
    by_scenario: dict[str, list[EvalResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario_id, []).append(r)
    width = max([len("scenario")] + [len(s) for s in by_scenario])
    head = (f"{'scenario':<{width}}  {'pass':>5}  {'turns':>5}  {'calls':>5}  "
            f"{'cost_usd':>9}  {'p50_ms':>7}  {'p95_ms':>7}")
    lines = [f"eval run {run.id}  {run.mode}  git {run.git_sha[:10]}  "
             f"agent {run.agent_model}  diner {run.diner_model}  repeat {run.repeat}",
             head, "-" * len(head)]
    for sid in sorted(by_scenario):
        rs = by_scenario[sid]
        k = sum(1 for r in rs if r.passed)
        p95 = _nums(rs, "llm_p95_ms")
        lines.append(
            f"{sid:<{width}}  {f'{k}/{len(rs)}':>5}  "
            f"{_num(_med(_nums(rs, 'diner_turns')), 1):>5}  "
            f"{_num(_med(_nums(rs, 'model_calls')), 1):>5}  "
            f"{sum(_nums(rs, 'total_cost_usd')):>9.4f}  "
            f"{_num(_med(_nums(rs, 'llm_p50_ms'))):>7}  "
            f"{_num(max(p95) if p95 else None):>7}")
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pct = round(100 * passed / total) if total else 0
    cost = sum(sum(_nums([r], "total_cost_usd")) for r in results)
    bookings = sum(1 for r in results
                   if r.passed and r.metrics.get("outcome") == "booked")
    per_booking = f"${cost / bookings:.4f}" if bookings else "n/a"
    lines += ["",
              f"pass rate {passed}/{total} ({pct}%)",
              f"median turn latency {_num(_med(_nums(results, 'llm_p50_ms')))} ms",
              f"total cost ${cost:.4f}",
              f"cost per booking {per_booking}"]
    return "\n".join(lines)


def run_summary(run: EvalRun, results: Sequence[EvalResult]) -> dict[str, Any]:
    return {"id": run.id, "passed": sum(1 for r in results if r.passed),
            "total": len(results)}
