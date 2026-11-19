"""Persist an eval run and render its report.

`prompt_hashes` is the sha256 of `build_system_prompt` for a fixed reference
location and rules, per channel. It changes whenever the prompt template
changes, which is its purpose: two runs with different hashes ran different
prompts.
"""

import hashlib
import json
import os
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent.prompt import build_system_prompt
from backend.reservations.evals.checker import percentile
from backend.reservations.evals.models import EvalResult, EvalRun
from backend.reservations.evals.runner import SuiteResult
from backend.reservations.evals.stats import (
    book_goal_attempts, cost_per_booking, voice_section)
from backend.reservations.rules import RulesSchema

REFERENCE_LOCATION = "Reference Bistro"
REFERENCE_TIMEZONE = "America/New_York"
# Every run's file lands here (gitignored); the scratch schema is dropped.
RUNS_DIR = Path(__file__).resolve().parents[3] / "evals-runs"


@dataclass(frozen=True)
class RunMeta:
    git_sha: str
    agent_model: str
    diner_model: str
    prompt_hash_web: str
    prompt_hash_voice: str
    mode: str  # "live" | "replay"
    prompt_version: str  # sha256[:12] of the rendered reference voice prompt
    repeat: int = 1
    notes: str | None = None
    recognizer: str = "exact"
    seed: int | None = None
    lex: dict[str, str] | None = None  # {bot_id, alias_id, locale}; lex runs only
    voice_knobs: dict[str, Any] = field(default_factory=dict)


def prompt_hash(channel: str, voice_knobs: dict[str, Any] | None = None) -> str:
    rules = RulesSchema.model_validate({"voice": voice_knobs or {}})
    prompt = build_system_prompt(REFERENCE_LOCATION, REFERENCE_TIMEZONE, rules, channel)
    return hashlib.sha256(prompt.encode()).hexdigest()


def prompt_hashes(voice_knobs: dict[str, Any] | None = None) -> tuple[str, str]:
    """(web, voice)."""
    return prompt_hash("web", voice_knobs), prompt_hash("voice", voice_knobs)


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
    run = EvalRun(created_at=datetime.now(timezone.utc), git_sha=meta.git_sha, agent_model=meta.agent_model,
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


def _pooled(results: Sequence[EvalResult], key: str) -> list[float]:
    """Every attempt's list under `key`, joined."""
    return [float(v) for r in results for v in (r.metrics.get(key) or [])
            if isinstance(v, (int, float))]


def _med(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _num(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_report(run: EvalRun, results: Sequence[EvalResult]) -> str:
    """A fixed-width table per scenario, then totals.

    Per scenario: pass fraction, median diner turns, median model calls,
    total cost; llm call latency (llm_p50: median of the attempts' p50,
    llm_p95: the worst attempt's p95); and turn latency, the wall-clock time
    of each diner turn (turn_p50 / turn_p95 over every turn of every
    attempt). Totals: pass rate, turn p50 / p95 over every turn, llm call
    p50 (median of every attempt's p50), total cost, cost per booking (the
    cost of every attempt of a book-goal scenario over the passing ones;
    "n/a" when none passed) and cost per conversation (total cost over all
    attempts)."""
    by_scenario: dict[str, list[EvalResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario_id, []).append(r)
    width = max([len("scenario")] + [len(s) for s in by_scenario])
    head = (f"{'scenario':<{width}}  {'pass':>5}  {'turns':>5}  {'calls':>5}  "
            f"{'cost_usd':>9}  {'llm_p50':>7}  {'llm_p95':>7}  "
            f"{'turn_p50':>8}  {'turn_p95':>8}")
    lines = [f"eval run {run.id}  {run.mode}  git {run.git_sha[:10]}  "
             f"agent {run.agent_model}  diner {run.diner_model}  repeat {run.repeat}",
             head, "-" * len(head)]
    for sid in sorted(by_scenario):
        rs = by_scenario[sid]
        k = sum(1 for r in rs if r.passed)
        p95 = _nums(rs, "llm_p95_ms")
        turns = _pooled(rs, "turn_ms")
        lines.append(
            f"{sid:<{width}}  {f'{k}/{len(rs)}':>5}  "
            f"{_num(_med(_nums(rs, 'diner_turns')), 1):>5}  "
            f"{_num(_med(_nums(rs, 'model_calls')), 1):>5}  "
            f"{sum(_nums(rs, 'total_cost_usd')):>9.4f}  "
            f"{_num(_med(_nums(rs, 'llm_p50_ms'))):>7}  "
            f"{_num(max(p95) if p95 else None):>7}  "
            f"{_num(percentile(turns, 50)):>8}  "
            f"{_num(percentile(turns, 95)):>8}")
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pct = round(100 * passed / total) if total else 0
    cost = sum(_nums(results, "total_cost_usd"))
    book = book_goal_attempts(results)
    booked = sum(1 for r in book if r.passed)
    cpb = cost_per_booking(results)
    per_booking = f"${cpb:.4f}" if cpb is not None else "n/a"
    per_conversation = f"${cost / total:.4f}" if total else "n/a"
    turns = _pooled(results, "turn_ms")
    lines += ["",
              f"pass rate {passed}/{total} ({pct}%)",
              f"turn p50 {_num(percentile(turns, 50))} ms, "
              f"p95 {_num(percentile(turns, 95))} ms",
              f"llm call p50 {_num(_med(_nums(results, 'llm_p50_ms')))} ms",
              f"total cost ${cost:.4f}",
              f"cost per booking {per_booking} "
              f"({booked} passing of {len(book)} book-goal attempts)",
              f"cost per conversation {per_conversation}"]
    lines += voice_section([dict(r.metrics) for r in results],
                           [r.scenario_id for r in results])
    return "\n".join(lines)


def run_summary(run: EvalRun, results: Sequence[EvalResult]) -> dict[str, Any]:
    return {"id": run.id, "passed": sum(1 for r in results if r.passed),
            "total": len(results)}


def write_run_file(run: EvalRun, results: Sequence[EvalResult], meta: RunMeta,
                   report: str, directory: Path = RUNS_DIR) -> Path:
    """Write the run (meta, every result, the report text) to
    `<directory>/<run-id>.json`. The scratch schema the run was stored in is
    dropped afterwards, so this file is the run's only lasting record."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.id}.json"
    doc = {
        "id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "meta": {k: v for k, v in asdict(meta).items()
                 if not (k == "lex" and v is None)},
        "results": [{"scenario_id": r.scenario_id, "attempt": r.attempt,
                     "passed": r.passed, "failures": list(r.failures),
                     "metrics": dict(r.metrics),
                     "conversation_snapshot": dict(r.conversation_snapshot)}
                    for r in results],
        "report": report,
    }
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n")
    return path
