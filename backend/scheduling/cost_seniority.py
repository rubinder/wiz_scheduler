"""Cost and seniority scoring for scheduling (#134).

Both scheduling paths — local_scheduler.py's deterministic picker and
prompts.py's AI-path candidate ordering/rendering — consume these functions
so the two paths score identically. Mirrors preferences.py's conventions:
plain Dict[str, Any] employee representations, lower score = more preferred,
and 0.0 for "no data configured", which is what keeps this feature additive
(a company that sets nothing gets unchanged scheduling behavior).
"""
from typing import Any, Dict, List

DEFAULT_OVERTIME_THRESHOLD_HOURS = 40.0
DEFAULT_OVERTIME_PREMIUM_MULTIPLIER = 1.5

# Same points-scale convention as preferences.PREFERENCE_PENALTY (50.0) and
# _affinity_score's +/-50: cost and seniority are tie-break nudges here, so
# their weights stay well under that scale rather than dominating it.
COST_WEIGHT = 20.0
SENIORITY_WEIGHT = 10.0
OVERTIME_PENALTY_PER_HOUR = 15.0


def resolve_overtime_threshold(
    company_threshold: float | None, location_threshold: float | None
) -> float:
    """Location override beats company default beats the 40h constant."""
    if location_threshold is not None:
        return float(location_threshold)
    if company_threshold is not None:
        return float(company_threshold)
    return DEFAULT_OVERTIME_THRESHOLD_HOURS


def resolve_overtime_multiplier(
    company_multiplier: float | None, location_multiplier: float | None
) -> float:
    """Location override beats company default beats the 1.5x constant."""
    if location_multiplier is not None:
        return float(location_multiplier)
    if company_multiplier is not None:
        return float(company_multiplier)
    return DEFAULT_OVERTIME_PREMIUM_MULTIPLIER


def cost_score(emp: Dict[str, Any], pool: List[Dict[str, Any]]) -> float:
    """0-1 min-max normalized pay_rate among *pool* members with pay_rate set.

    0.0 = cheapest rated candidate, 1.0 = most expensive, 0.5 when every
    rated candidate shares the same rate (no discriminating information).
    Returns 0.0 when this employee has no pay_rate configured — opt-in per
    employee, not just per company.
    """
    rate = emp.get("pay_rate")
    if rate is None:
        return 0.0
    rates = [e["pay_rate"] for e in pool if e.get("pay_rate") is not None]
    if len(rates) < 2:
        return 0.5
    lo, hi = min(rates), max(rates)
    if hi == lo:
        return 0.5
    return (float(rate) - lo) / (float(hi) - float(lo))


def resolve_seniority_ranks(pool: List[Dict[str, Any]]) -> Dict[str, float]:
    """Resolved seniority rank per employee id in *pool*.

    A manual `seniority_rank` wins when set; otherwise rank is derived from
    `hire_date` (1 = earliest hire_date) among the remaining employees.
    Employees with neither field set are omitted from the result.
    Manual and derived ranks are combined into a single consistent ranking
    where lower numbers are more senior.
    """
    resolved: Dict[str, float] = {}
    derive_from: List[Dict[str, Any]] = []
    max_manual_rank = 0

    # First pass: collect manual ranks and candidates for derivation
    for e in pool:
        eid = str(e["id"])
        if e.get("seniority_rank") is not None:
            manual_rank = float(e["seniority_rank"])
            resolved[eid] = manual_rank
            max_manual_rank = max(max_manual_rank, manual_rank)
        elif e.get("hire_date") is not None:
            derive_from.append(e)

    # Second pass: assign derived ranks starting after the highest manual rank
    for i, e in enumerate(sorted(derive_from, key=lambda x: x["hire_date"]), start=int(max_manual_rank) + 1):
        resolved[str(e["id"])] = float(i)

    return resolved


def seniority_score(emp: Dict[str, Any], resolved_ranks: Dict[str, float]) -> float:
    """0-1 min-max normalized resolved rank among *resolved_ranks*.

    0.0 = most senior present, 0.5 when every ranked candidate ties, 0.0 when
    this employee has no resolved rank.
    """
    eid = str(emp["id"])
    if eid not in resolved_ranks:
        return 0.0
    ranks = list(resolved_ranks.values())
    if len(ranks) < 2:
        return 0.5
    lo, hi = min(ranks), max(ranks)
    if hi == lo:
        return 0.5
    return (resolved_ranks[eid] - lo) / (hi - lo)


def overtime_score(
    hours_committed: float, shift_duration_hrs: float, threshold: float
) -> float:
    """Penalty proportional to how far past *threshold* this shift would push
    the employee. 0.0 when the projected total stays at or under threshold —
    this is a soft nudge, never a filter."""
    projected = hours_committed + shift_duration_hrs
    over = projected - threshold
    if over <= 0:
        return 0.0
    return over * OVERTIME_PENALTY_PER_HOUR
