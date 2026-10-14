"""Employee-zone -> location-zone conversion for availability and blackouts.

By default an employee has no timezone and none of this runs: availability is
the local wall clock of whichever location is being scheduled (#61/#85). Only
for an employee whose timezone a manager has set are their availability and
blackouts read as THAT zone's wall clock and converted, once, into the
location's wall clock at the scheduling boundary (nodes.load_location_context).

Everything here works on wall-clock faces between two EXPLICIT zones. It never
calls .astimezone() on a stored value as if its +00:00 tag were real.
Preferences and caps are deliberately never converted: they describe the kind
of shift, which is location-local.
"""

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_END_OF_DAY = time(23, 59)


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
        return None


def needs_conversion(emp_tz: str | None, loc_tz: str | None) -> bool:
    """True only for a valid employee zone that differs from a valid location zone."""
    if not emp_tz or emp_tz == loc_tz:
        return False
    return _zone(emp_tz) is not None and _zone(loc_tz) is not None


def _convert_face(face: datetime, src: ZoneInfo, dst: ZoneInfo) -> datetime:
    # fold=0: a nonexistent (spring-forward) or ambiguous (fall-back) face
    # resolves the way zoneinfo does by default.
    return face.replace(tzinfo=src, fold=0).astimezone(dst).replace(tzinfo=None)


def _as_exclusive_end(start: datetime, end: datetime) -> datetime:
    # The codebase writes "until end of day" as 23:59; convert it as midnight
    # so the converted window keeps its full length.
    if end.time() == _END_OF_DAY:
        return datetime.combine(end.date() + timedelta(days=1), time())
    return end


def _pieces(start: datetime, end: datetime) -> List[Tuple[datetime, datetime]]:
    """Split [start, end) at each midnight; a piece ending at midnight is
    written as 23:59 of its own day (the codebase's end-of-day convention)."""
    out: List[Tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        next_midnight = datetime.combine(cur.date() + timedelta(days=1), time())
        piece_end = min(end, next_midnight)
        shown_end = (
            datetime.combine(cur.date(), _END_OF_DAY)
            if piece_end == next_midnight else piece_end
        )
        if shown_end > cur:
            out.append((cur, shown_end))
        cur = piece_end
    return out


def availability_to_location(
    windows: List[Dict[str, str]],
    emp_tz: str | None,
    loc_tz: str | None,
) -> List[Dict[str, str]]:
    """Convert employee-local availability windows to the location's wall clock.

    Returns *windows* itself (same object) when no conversion applies. Each
    output window keeps its input's offset tag, so the shape downstream code
    reads is unchanged.
    """
    if not needs_conversion(emp_tz, loc_tz):
        return windows
    src, dst = _zone(emp_tz), _zone(loc_tz)
    out: List[Dict[str, str]] = []
    for window in windows:
        try:
            start_aware = datetime.fromisoformat(window["start"])
            end_aware = datetime.fromisoformat(window["end"])
        except (KeyError, ValueError, TypeError):
            out.append(window)
            continue
        tag = start_aware.tzinfo
        start = start_aware.replace(tzinfo=None)
        end = _as_exclusive_end(start, end_aware.replace(tzinfo=None))
        if end <= start:
            out.append(window)
            continue
        for p_start, p_end in _pieces(_convert_face(start, src, dst), _convert_face(end, src, dst)):
            out.append({
                "start": p_start.replace(tzinfo=tag).isoformat(),
                "end": p_end.replace(tzinfo=tag).isoformat(),
            })
    return out


def blackouts_to_location(
    blackouts: List[Dict[str, str]],
    week_start_date: str,
    num_days: int,
    emp_tz: str | None,
    loc_tz: str | None,
) -> List[Dict[str, str]]:
    """Convert recurring employee-local blackouts for the week being scheduled.

    Each blackout is placed on the real dates of its weekday -- including the
    day before and after the week, since a conversion can carry an instance
    across the week's edge -- converted per date (DST-correct), and kept only
    where it lands inside the week. Output keeps the {"day","start","end"}
    shape; within one scheduled week a weekday names exactly one date.
    """
    if not blackouts or not needs_conversion(emp_tz, loc_tz):
        return blackouts
    src, dst = _zone(emp_tz), _zone(loc_tz)
    try:
        week_start = date.fromisoformat(week_start_date)
    except (ValueError, TypeError):
        return blackouts
    week_dates = {week_start + timedelta(days=i) for i in range(num_days)}
    candidate_dates = [week_start + timedelta(days=i) for i in range(-1, num_days + 1)]

    out: List[Dict[str, str]] = []
    for bo in blackouts:
        try:
            start_t = time.fromisoformat(bo["start"])
            end_t = time.fromisoformat(bo["end"])
        except (KeyError, ValueError, TypeError):
            out.append(bo)
            continue
        for d in candidate_dates:
            if DAY_NAMES[d.weekday()] != bo.get("day"):
                continue
            start = datetime.combine(d, start_t)
            end = _as_exclusive_end(start, datetime.combine(d, end_t))
            if end <= start:
                continue
            for p_start, p_end in _pieces(_convert_face(start, src, dst), _convert_face(end, src, dst)):
                if p_start.date() not in week_dates:
                    continue
                out.append({
                    "day": DAY_NAMES[p_start.weekday()],
                    "start": p_start.strftime("%H:%M"),
                    "end": p_end.strftime("%H:%M"),
                })
    return out


def instant_to_employee_face(dt: datetime, emp_tz: str) -> datetime:
    """A real instant (a shift time) as a naive wall-clock face in *emp_tz*.

    A naive value has no instant to convert and is returned unchanged.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(ZoneInfo(emp_tz)).replace(tzinfo=None)
