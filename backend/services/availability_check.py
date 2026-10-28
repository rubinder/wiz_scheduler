"""Whether an employee can work a span: the one definition (#174).

Used by the approved-edit `no_availability` warning and the open-shift
candidate ranking, and matching the scheduler (graph._load_employee_availability
+ nodes.validate_schedule): an availability window, minus the employee's other
shifts, must cover the whole span, and the span must miss every blackout.

Every datetime here is a naive wall-clock face at the shift's location.
Availability and blackouts are always the location's local wall clock;
nothing converts them per employee (#171).
"""

from datetime import datetime, timezone
from typing import Any, Iterable

from backend.scheduling.nodes import _subtract_consumed, _wall_clock

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def window_dict(row: Any) -> dict[str, str]:
    """An availability row as the scheduler reads it.

    Availability is local wall-clock TAGGED UTC (#61): a naive value (SQLite)
    gets that tag back, never a conversion. An end at or before the start was
    stored as 00:00 meaning "end of day", so it becomes 23:59.
    """
    start, end = row.start_time, row.end_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        end = start.replace(hour=23, minute=59, second=0, microsecond=0)
    return {"start": start.isoformat(), "end": end.isoformat()}


def blackout_dicts(rows: Iterable[Any]) -> list[dict[str, str]]:
    """Blackout rows in the scheduler's {"day", "start", "end"} shape."""
    out: list[dict[str, str]] = []
    for row in rows:
        dow = int(row.day_of_week)
        if 0 <= dow <= 6:
            out.append({"day": DAY_NAMES[dow], "start": row.start_time, "end": row.end_time})
    return out


def _blocked_by_blackout(
    blackouts: list[dict[str, str]],
    span_start: datetime,
    span_end: datetime,
) -> bool:
    day = DAY_NAMES[span_start.weekday()]
    s_hm, e_hm = span_start.strftime("%H:%M"), span_end.strftime("%H:%M")
    for bo in blackouts:
        if bo.get("day") != day:
            continue
        bo_s, bo_e = bo.get("start", ""), bo.get("end", "")
        # Same strict HH:MM overlap as nodes.validate_schedule step 5b.
        if bo_s and bo_e and s_hm < bo_e and bo_s < e_hm:
            return True
    return False


def is_available(
    *,
    windows: list[dict[str, str]],
    other_spans: list[tuple[datetime, datetime]],
    blackouts: list[dict[str, str]],
    span_start: datetime,
    span_end: datetime,
) -> bool:
    """True when the employee can work [span_start, span_end)."""
    if _blocked_by_blackout(blackouts, span_start, span_end):
        return False
    for w in windows:
        w_start, w_end = _wall_clock(w["start"]), _wall_clock(w["end"])
        for free_start, free_end in _subtract_consumed(w_start, w_end, other_spans):
            if free_start <= span_start and free_end >= span_end:
                return True
    return False
