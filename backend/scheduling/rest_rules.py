"""Fair Workweek minimum-rest ("clopening") rule.

Shared by:
  - `backend.scheduling.local_scheduler` — proactive avoidance while the
    local (algorithmic) scheduler assigns shifts.
  - `backend.scheduling.nodes` — `validate_schedule`'s hard enforcement
    (including the cross-location path via `availability_draft`).
  - `backend.routers.public` — the unauthenticated compliance-check API,
    which reuses this helper rather than reimplementing the rule.

Moved here (unchanged in behaviour) from `local_scheduler.py` so it has one
home shared by product code and the public checker.
"""

from datetime import datetime
from typing import Dict, List


def _rest_gap_hours(a_start: str, a_end: str, b_start: str, b_end: str) -> float:
    """Hours of rest between two shifts given as ISO datetime strings.

    Returns the gap between the earlier shift's end and the later shift's
    start. Zero or negative when the two shifts overlap.
    """
    a0 = datetime.fromisoformat(a_start)
    a1 = datetime.fromisoformat(a_end)
    b0 = datetime.fromisoformat(b_start)
    b1 = datetime.fromisoformat(b_end)
    if a0 <= b0:
        return (b0 - a1).total_seconds() / 3600.0
    return (a0 - b1).total_seconds() / 3600.0


def _min_rest_violation(
    start_iso: str,
    end_iso: str,
    other_windows: List[Dict[str, str]],
    min_rest_hours: float | None,
) -> bool:
    """True if a shift would leave less than *min_rest_hours* of rest before
    or after any of *other_windows* on a different calendar day.

    Shifts on the same calendar day are treated as split shifts and are
    exempt — the clopening rule only concerns rest across a day boundary.
    NULL/0 *min_rest_hours* disables the check.
    """
    if not min_rest_hours or min_rest_hours <= 0:
        return False
    try:
        s0 = datetime.fromisoformat(start_iso)
    except (ValueError, TypeError):
        return False
    for w in other_windows:
        w_start = w.get("start")
        w_end = w.get("end")
        if not w_start or not w_end:
            continue
        try:
            w0 = datetime.fromisoformat(w_start)
        except (ValueError, TypeError):
            continue
        # Same-day split shift → exempt from the cross-day rest rule.
        if s0.date() == w0.date():
            continue
        if _rest_gap_hours(start_iso, end_iso, w_start, w_end) < float(min_rest_hours):
            return True
    return False
