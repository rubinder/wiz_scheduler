"""Voice run statistics over result metrics dicts (pure, no database).

Used by the report (`store.format_report`) and by `--compare`, which reads
run files, so every function takes plain `metrics` dicts and tolerates old
runs that lack the voice fields."""

from typing import Any, Iterable

from backend.reservations.evals.checker import percentile

# Stage -> the metrics key holding its per-event millisecond list.
STAGE_KEYS = {"asr": "asr_ms", "tts": "tts_ms", "llm": "llm_ms",
              "tools": "tools_ms", "turn_total": "turn_ms"}


def _voice(m: dict[str, Any]) -> dict[str, Any]:
    v = m.get("voice")
    return v if isinstance(v, dict) else {}


def is_voice(m: dict[str, Any]) -> bool:
    return bool(_voice(m))


def first_try_understanding(metrics: Iterable[dict[str, Any]]) -> float | None:
    """Pooled over every voice attempt: text turns after which the agent did
    not ask again, over all text turns. None when there are no voice text
    turns."""
    text = ok = 0
    for m in metrics:
        for r in _voice(m).get("repeats") or []:
            if isinstance(r, dict) and r.get("kind") == "text":
                text += 1
                ok += r.get("repeat") is False
    return ok / text if text else None


def stage_values(metrics: Iterable[dict[str, Any]], stage: str) -> list[float]:
    """The stage's latencies pooled over the voice attempts of a run. asr and
    tts come from the voice block (the exact and noisy recognizers have
    none); the others from the attempt's metrics."""
    key = STAGE_KEYS[stage]
    out: list[float] = []
    for m in metrics:
        if not is_voice(m):
            continue
        src = _voice(m) if stage in ("asr", "tts") else m
        out += [float(v) for v in src.get(key) or []
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return out


def stage_percentiles(metrics: Iterable[dict[str, Any]]
                      ) -> dict[str, tuple[float, float] | None]:
    """stage -> (p50, p95), nearest rank; None when the run has no values."""
    ms = list(metrics)
    res: dict[str, tuple[float, float] | None] = {}
    for stage in STAGE_KEYS:
        vals = stage_values(ms, stage)
        p50, p95 = percentile(vals, 50), percentile(vals, 95)
        res[stage] = (p50, p95) if p50 is not None and p95 is not None else None
    return res


def pct_text(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def voice_section(metrics: list[dict[str, Any]], scenario_ids: list[str]) -> list[str]:
    """Report lines for the voice attempts; empty without any."""
    pairs = [(sid, m) for sid, m in zip(scenario_ids, metrics) if is_voice(m)]
    if not pairs:
        return []
    vm = [m for _, m in pairs]
    lines = ["", "voice",
             f"first-try understanding {pct_text(first_try_understanding(vm))}"]
    for stage, pp in stage_percentiles(vm).items():
        lines.append(f"  {stage:<10} " + ("p50 - ms, p95 - ms" if pp is None else
                                          f"p50 {pp[0]:.0f} ms, p95 {pp[1]:.0f} ms"))
    per: dict[str, list[int]] = {}
    for sid, m in pairs:
        reps = [r for r in _voice(m).get("repeats") or []
                if isinstance(r, dict) and r.get("repeat") is True]
        per.setdefault(sid, []).append(len(reps))
    lines.append("repeats per scenario: " + ", ".join(
        f"{sid} {sum(v)}" for sid, v in sorted(per.items())))
    errs = sum(len(_voice(m).get("ssml_errors") or []) for m in vm)
    lines.append(f"ssml errors {errs}")
    return lines


def _get(r: Any, name: str) -> Any:
    return r.get(name) if isinstance(r, dict) else getattr(r, name)


def book_goal_attempts(results: Iterable[Any]) -> list[Any]:
    return [r for r in results if (_get(r, "metrics") or {}).get("book_goal") is True]


def cost_per_booking(results: Iterable[Any]) -> float | None:
    """The one definition: the cost of every book-goal attempt over the
    book-goal attempts that passed; None when none passed. `results` are
    EvalResult rows or run-file result dicts."""
    book = book_goal_attempts(results)
    booked = sum(1 for r in book if _get(r, "passed"))
    if not booked:
        return None
    return sum(float((_get(r, "metrics") or {}).get("total_cost_usd") or 0)
               for r in book) / booked
