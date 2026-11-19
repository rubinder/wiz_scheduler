"""`run --compare A B`: two run files side by side, offline.

Reads `evals-runs/<id>.json` files only (no database, no schema). Old files
without the voice fields still read; their voice rows show "n/a"."""

import json
import sys
from pathlib import Path
from typing import Any

from backend.reservations.evals.stats import (
    cost_per_booking, first_try_understanding, pct_text, stage_label, stage_percentiles)


def load_run(ref: str, runs_dir: Path) -> dict[str, Any]:
    """`ref` is a path to a run file, or a run id in `runs_dir`."""
    if "/" in ref or "\\" in ref or ref.endswith(".json") or Path(ref).is_file():
        path = Path(ref)
        if not path.is_file():
            raise FileNotFoundError(f"no such run file: {ref}")
    else:
        path = (runs_dir / f"{ref}.json").resolve()
        if runs_dir.resolve() not in path.parents:
            raise ValueError(f"run id {ref!r} is outside {runs_dir}")
    return json.loads(path.read_text())


def _metrics(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [r.get("metrics") or {} for r in doc.get("results") or []]


def pass_rate(doc: dict[str, Any]) -> float | None:
    rs = doc.get("results") or []
    return sum(1 for r in rs if r.get("passed")) / len(rs) if rs else None


def scenario_ids(doc: dict[str, Any]) -> set[str]:
    return {r["scenario_id"] for r in doc.get("results") or []}


def _scenario_pass(doc: dict[str, Any]) -> dict[str, bool]:
    """Pass when every attempt of the scenario passed."""
    out: dict[str, bool] = {}
    for r in doc.get("results") or []:
        out[r["scenario_id"]] = out.get(r["scenario_id"], True) and bool(r.get("passed"))
    return out


def _ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0f} ms"


def _delta(a: float | None, b: float | None, fmt: Any) -> str:
    return "n/a" if a is None or b is None else fmt(b - a)


META_FIELDS = ("mode", "recognizer", "seed", "voice_knobs", "prompt_version")


def meta_lines(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """One line per run setting, A then B, flagged where they differ."""
    ma, mb = a.get("meta") or {}, b.get("meta") or {}
    out = []
    for f in META_FIELDS:
        va, vb = ma.get(f), mb.get(f)
        flag = "  <- DIFFERS" if va != vb else ""
        out.append(f"meta {f}: A={va!r} B={vb!r}{flag}")
    return out


def compare_docs(a: dict[str, Any], b: dict[str, Any]) -> str:
    ma, mb = _metrics(a), _metrics(b)
    rows: list[tuple[str, str, str, str]] = []

    def row(name: str, va: float | None, vb: float | None, show: Any, dshow: Any) -> None:
        rows.append((name, show(va), show(vb), _delta(va, vb, dshow)))

    row("pass rate", pass_rate(a), pass_rate(b), pct_text,
        lambda d: f"{100 * d:+.1f} pts")
    row("first-try understanding", first_try_understanding(ma),
        first_try_understanding(mb), pct_text, lambda d: f"{100 * d:+.1f} pts")
    sa, sb = stage_percentiles(ma)["turn_total"], stage_percentiles(mb)["turn_total"]
    for i, label in ((0, "p50"), (1, "p95")):
        row(f"{label} {stage_label('turn_total')}", sa[i] if sa else None, sb[i] if sb else None,
            _ms, lambda d: f"{d:+.0f} ms")
    row("cost per booking", cost_per_booking(a.get("results") or []),
        cost_per_booking(b.get("results") or []),
        lambda v: "n/a" if v is None else f"${v:.4f}", lambda d: f"{d:+.4f}")
    w = max(len(r[0]) for r in rows)
    ida, idb = a.get("id", "A"), b.get("id", "B")
    lines = [*meta_lines(a, b), "",
             "first-try understanding and the latency rows cover voice attempts only",
             f"{'':<{w}}  {ida:>14}  {idb:>14}  delta",
             "-" * (w + 40)]
    lines += [f"{n:<{w}}  {x:>14}  {y:>14}  {d}" for n, x, y, d in rows]
    pa, pb = _scenario_pass(a), _scenario_pass(b)
    flips = [f"  {sid}: {'pass' if pa[sid] else 'fail'} -> {'pass' if pb[sid] else 'fail'}"
             for sid in sorted(set(pa) & set(pb)) if pa[sid] != pb[sid]]
    lines.append("")
    lines.append("scenario flips:" + ("" if flips else " none"))
    lines += flips
    return "\n".join(lines)


def compare_main(ref_a: str, ref_b: str, *, allow_mismatch: bool,
                 runs_dir: Path) -> int:
    try:
        a, b = load_run(ref_a, runs_dir), load_run(ref_b, runs_dir)
    except (OSError, ValueError) as exc:
        print(f"cannot read run file: {exc}", file=sys.stderr)
        return 2
    try:
        ia, ib = scenario_ids(a), scenario_ids(b)
        text = compare_docs(a, b)
    except (KeyError, AttributeError, TypeError) as exc:
        print(f"malformed run file: {exc!r}", file=sys.stderr)
        return 2
    if ia != ib and not allow_mismatch:
        print("refusing to compare: scenario sets differ "
              f"(only in A: {sorted(ia - ib)}; only in B: {sorted(ib - ia)}); "
              "pass --allow-mismatch", file=sys.stderr)
        return 2
    print(text)
    return 0
