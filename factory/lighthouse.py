"""Lighthouse no-regress gate: 3 mobile runs, median, baseline minus 2 points."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

from factory.config import FACTORY_HOME

# Read-only here: a gate never edits the baselines it is judged against. A run
# that beats them proposes the raise to the owner instead (PROPOSED).
BASELINES = Path(__file__).resolve().parent / "baselines" / "lighthouse.json"
PROPOSED = FACTORY_HOME / "baselines-proposed.json"
LIGHTHOUSE_BIN = Path(__file__).resolve().parent.parent / "frontend" / "node_modules" / ".bin" / "lighthouse"
RUN_TIMEOUT = 300


class GateError(Exception):
    """The gate itself could not run (exit 2), as opposed to a regression (exit 1)."""
CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]
FLOORS = {"performance": 80}
DEFAULT_FLOOR = 90
TOLERANCE = 2


def median(runs: list[dict[str, float]]) -> dict[str, float]:
    return {k: statistics.median(r[k] for r in runs) for k in runs[0]}


def compare(scores: dict[str, dict[str, float]], baselines: dict[str, dict[str, float]]) -> tuple[list[str], dict]:
    fails: list[str] = []
    new = {k: dict(v) for k, v in baselines.items()}
    for url, cats in scores.items():
        base = baselines.get(url)
        if base is None:
            page_fails = [f"{url} {c} {s:g} < floor {FLOORS.get(c, DEFAULT_FLOOR)}"
                          for c, s in cats.items() if s < FLOORS.get(c, DEFAULT_FLOOR)]
            fails += page_fails
            if not page_fails:
                new[url] = dict(cats)
            continue
        for c, s in cats.items():
            b = base.get(c)
            if b is None:
                new[url][c] = s
            elif s < b - TOLERANCE:
                fails.append(f"{url} {c} {s:g} < baseline {b:g} - {TOLERANCE}")
            elif s > b:
                new[url][c] = s
    return fails, new


def run_once(url: str) -> dict[str, float]:
    if not LIGHTHOUSE_BIN.exists():
        raise GateError(f"{LIGHTHOUSE_BIN} not found: run npm ci in frontend")
    try:
        proc = subprocess.run([str(LIGHTHOUSE_BIN), url, "--form-factor=mobile", "--screenEmulation.mobile",
                               "--output=json", "--quiet", "--chrome-flags=--headless=new",
                               f"--only-categories={','.join(CATEGORIES)}"],
                              capture_output=True, text=True, check=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise GateError(f"lighthouse timed out after {RUN_TIMEOUT}s on {url}") from e
    except subprocess.CalledProcessError as e:
        raise GateError(f"lighthouse failed on {url}: {(e.stderr or '')[-500:]}") from e
    cats = json.loads(proc.stdout)["categories"]
    return {c: round(cats[c]["score"] * 100) for c in CATEGORIES}


def write_atomic(path: Path, data: dict) -> None:
    """Write JSON to a temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def propose_baselines(scores: dict[str, dict[str, float]]) -> tuple[list[str], dict]:
    """(fails, raised). Never writes BASELINES; raises are merged into PROPOSED for the owner."""
    baselines = _read(BASELINES)
    fails, new = compare(scores, baselines)
    raised = {u: {c: s for c, s in cats.items() if (baselines.get(u) or {}).get(c) != s} for u, cats in new.items()}
    raised = {u: c for u, c in raised.items() if c}
    if fails or not raised:
        return fails, {}
    PROPOSED.parent.mkdir(parents=True, exist_ok=True)
    with open(PROPOSED.with_name(PROPOSED.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = _read(PROPOSED)
        for u, cats in raised.items():
            cur = merged.setdefault(u, {})
            for c, s in cats.items():
                cur[c] = max(s, cur.get(c, s))
        write_atomic(PROPOSED, merged)
    return fails, raised


def _notify_owner(raised: dict) -> None:
    try:
        from datetime import datetime, timezone
        from factory.notify import notify
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        notify("Lighthouse baselines to review", f"{len(raised)} page(s) beat their baseline; proposed raises in {PROPOSED}.",
               f"lighthouse-baselines-{today}")
    except Exception as e:  # noqa: BLE001 - the gate result does not depend on the notification
        print(f"could not notify the owner: {e}", file=sys.stderr)


def _run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--repo", choices=["app", "marketing"], default="app")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    scores = {f"{a.repo}:{p}": median([run_once(a.base + p) for _ in range(3)]) for p in a.paths}
    fails, raised = propose_baselines(scores)
    print(json.dumps(scores, indent=2))
    for f in fails:
        print(f"FAIL {f}")
    if raised:
        print(f"scores beat the baselines; proposed raises recorded for the owner in {PROPOSED}")
        _notify_owner(raised)
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    """0 pass, 1 regression, 2 the gate itself crashed (never 0 on a crash)."""
    try:
        return _run(argv)
    except SystemExit as e:  # argparse errors
        return 2 if e.code else 0
    except Exception as e:  # noqa: BLE001 - any crash is a gate error, not a pass
        print(f"lighthouse gate error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
