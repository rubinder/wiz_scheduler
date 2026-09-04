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

    Both the shift and the range may be overnight (e.g. "22:00"-"06:00"),
    following the same convention as local_scheduler._shift_duration_hours:
    an end at or before its start means the interval crosses midnight, so
    24h is added to the end. A genuinely zero-length shift (start == end) is
    handled before that normalisation so it is not mistaken for a full-day
    shift.

    After normalising, the shift and range may still sit on different
    24-hour "pages" of the clock (a 00:00-06:00 range and a 22:00-02:00
    shift genuinely overlap even though their raw minute numbers don't line
    up), so the range is also tried shifted by a full day earlier and later,
    and the best overlap wins.
    """
    s_start, s_end = _minutes(shift_start), _minutes(shift_end)
    if s_end == s_start:
        return 0.0
    if s_end <= s_start:
        s_end += 24 * 60
    duration = s_end - s_start
    if duration <= 0:
        return 0.0

    r_start, r_end = _minutes(range_start), _minutes(range_end)
    if r_end <= r_start:
        r_end += 24 * 60

    best_overlap = 0.0
    for page in (-24 * 60, 0, 24 * 60):
        overlap = min(s_end, r_end + page) - max(s_start, r_start + page)
        if overlap > best_overlap:
            best_overlap = overlap
    return best_overlap / duration


def matches_range(
    shift_start: str, shift_end: str, range_start: str, range_end: str
) -> bool:
    """Whether a shift counts as being 'in' a configured hour range."""
    return (
        overlap_fraction(shift_start, shift_end, range_start, range_end)
        >= settings.SCHEDULING_RANGE_MATCH_THRESHOLD
    )


# Same points scale as local_scheduler._affinity_score, so preference and
# affinity terms compose in _pick_employee without rescaling either.
PREFERENCE_PENALTY = 50.0

_HARD = 1.0


def _cap_count(range_counts: Dict[Any, int], emp_id: str, cap: Dict[str, Any]) -> int:
    return range_counts.get((emp_id, cap["start_time"], cap["end_time"]), 0)


def _day_violated(emp: Dict[str, Any], day_index: int) -> List[Dict[str, Any]]:
    """Day preferences this slot violates.

    A day preference only means anything as a set: if an employee prefers Mon,
    Tue and Wed, scheduling them on Thursday violates all three rows at once.
    Returning them individually would multiply the penalty by the number of
    preferred days, so the set is collapsed to at most one violation carrying
    the strongest weight.
    """
    prefs = emp.get("day_preferences") or []
    if not prefs:
        return []
    if any(int(p["day_of_week"]) == day_index for p in prefs):
        return []
    return [max(prefs, key=lambda p: float(p["weight"]))]


def _range_violated(emp: Dict[str, Any], start: str, end: str) -> List[Dict[str, Any]]:
    """Hour-range preferences this slot violates, by the same set logic."""
    prefs = emp.get("hour_range_preferences") or []
    if not prefs:
        return []
    if any(matches_range(start, end, p["start_time"], p["end_time"]) for p in prefs):
        return []
    return [max(prefs, key=lambda p: float(p["weight"]))]


def _caps_exceeded(
    emp: Dict[str, Any], start: str, end: str, range_counts: Dict[Any, int]
) -> List[Dict[str, Any]]:
    """Caps whose weekly allowance this slot would exceed."""
    hit: List[Dict[str, Any]] = []
    for cap in emp.get("hour_range_caps") or []:
        if not matches_range(start, end, cap["start_time"], cap["end_time"]):
            continue
        if _cap_count(range_counts, emp["id"], cap) >= int(cap["max_per_week"]):
            hit.append(cap)
    return hit


def violations_for_slot(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> List[Dict[str, Any]]:
    """Every preference row this (day, start, end) slot violates.

    THE single composition of the three checks. blocked_by_hard_preference
    keeps only the weight-1.0 rows; preference_score keeps only the rest;
    annotate_preference_violations reports the rest. All three see the same
    list, which is what keeps the asterisk's explanation from ever
    disagreeing with the scheduler's own reasoning.
    """
    return (
        _day_violated(emp, day_index)
        + _range_violated(emp, start, end)
        + _caps_exceeded(emp, start, end, range_counts)
    )


def blocked_by_hard_preference(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> bool:
    """Whether a weight-1.0 preference makes this employee ineligible.

    Called from eligible_for_slot, so a blocked employee is never offered to
    the sorting code or to the language model. A slot where this removes every
    candidate is emitted VACANT — that is the intended meaning of a hard
    preference, not a failure.
    """
    violations = violations_for_slot(emp, day_index, start, end, range_counts)
    return any(float(v["weight"]) >= _HARD for v in violations)


def preference_score(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> float:
    """Soft-preference penalty for assigning this employee to this slot.

    Lower is better, matching _affinity_score. Returns 0.0 for an employee
    with no preferences configured — the state of every employee until a
    manager opts them in, and what makes this feature additive.
    """
    violations = violations_for_slot(emp, day_index, start, end, range_counts)
    return sum(
        float(v["weight"]) * PREFERENCE_PENALTY
        for v in violations
        if float(v["weight"]) < _HARD
    )
