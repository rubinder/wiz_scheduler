"""Weighted scheduling preferences: overlap arithmetic and scoring.

Kept separate from local_scheduler.py so both scheduling paths can import it
without pulling in the deterministic strategies.
"""

from typing import Any, Dict, List

from backend.config import settings


def _minutes(hhmm: str) -> int:
    """'HH:MM' (or 'HH:MM:SS') to minutes since midnight."""
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def overlap_fraction(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> float:
    """Fraction of the SHIFT that falls inside the range, 0.0 to 1.0.

    The denominator is deliberately the shift, not the range: a one-hour shift
    sitting entirely inside an eight-hour preferred range has fully given the
    employee what they asked for, and should score 1.0 rather than 0.125.
    """
    s_start, s_end = _minutes(shift_start), _minutes(shift_end)
    duration = s_end - s_start
    if duration <= 0:
        return 0.0
    r_start, r_end = _minutes(range_start), _minutes(range_end)
    overlap = min(s_end, r_end) - max(s_start, r_start)
    if overlap <= 0:
        return 0.0
    return overlap / duration


def matches_range(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> bool:
    """Whether a shift counts as being 'in' a configured hour range."""
    return (
        overlap_fraction(shift_start, shift_end, range_start, range_end)
        >= settings.SCHEDULING_RANGE_MATCH_THRESHOLD
    )
