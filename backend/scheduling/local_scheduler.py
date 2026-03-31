"""Local (non-LLM) schedule generator.

Iterates through shift requirements and matches employees to shifts using
a scoring system.  Supports pluggable strategies:

  - "random"   : purely random selection among eligible employees
  - "rotation" : penalises employees who already filled the same role this
                 week so coverage rotates across the team

Both strategies respect employee affinities:
  - level  1.0  → hard constraint: MUST schedule together
  - level -1.0  → hard constraint: MUST NOT share a shift
  - |level| < 1 → soft preference that adjusts the candidate score
"""

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Set, Tuple

from backend.scheduling.prompts import _build_date_map, _parse_avail_by_day, _time_covers
from backend.scheduling.state import SchedulingState, ShiftAssignment

logger = logging.getLogger(__name__)

Strategy = Literal["random", "rotation"]


def _tz_offset_for_location(timezone_str: str) -> str:
    """Return a representative UTC offset string (e.g. '-04:00')."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_str)
        offset = datetime(2026, 3, 30, 12, 0, tzinfo=tz).strftime("%z")
        return f"{offset[:3]}:{offset[3:]}"
    except Exception:
        return "+00:00"


def _build_eligible_map(
    employees: List[Dict[str, Any]],
    weekly_schedule: Dict[str, List[dict]],
    date_to_day: Dict[str, str],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Pre-compute eligible employees for each (day, role_name) slot."""
    required_roles: set[str] = set()
    for slots in weekly_schedule.values():
        for slot in slots:
            rname = slot.get("role_name", "")
            if rname:
                required_roles.add(rname)

    emp_prepared: List[Dict[str, Any]] = []
    for emp in employees:
        role_names = {r.get("role_name", "") for r in emp.get("roles", [])}
        if not role_names & required_roles:
            continue
        day_windows = _parse_avail_by_day(emp.get("available_windows", []), date_to_day)
        emp_prepared.append({
            **emp,
            "_role_names": role_names,
            "_day_windows": day_windows,
            "_num_required_roles": len(role_names & required_roles),
        })

    eligible_map: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for day, slots in weekly_schedule.items():
        for slot in slots:
            role_name = slot.get("role_name", "Unknown")
            start = slot.get("start_time", "00:00")
            end = slot.get("end_time", "23:59")

            eligible: List[Dict[str, Any]] = []
            for e in emp_prepared:
                if role_name not in e["_role_names"]:
                    continue
                day_ranges = e["_day_windows"].get(day, [])
                if not day_ranges:
                    continue
                if _time_covers(day_ranges, start, end):
                    skill = next(
                        (r.get("skill_level", 0) for r in e.get("roles", [])
                         if r.get("role_name") == role_name),
                        0,
                    )
                    eligible.append({**e, "_skill": skill})
            eligible_map[(day, role_name)] = eligible

    return eligible_map


def _build_affinity_lookup(
    employees: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build {employee_id: [{target_id, level}, ...]} from employee data."""
    lookup: Dict[str, List[Dict[str, Any]]] = {}
    for emp in employees:
        eid = str(emp.get("id", ""))
        affs = emp.get("affinities", [])
        if affs:
            lookup[eid] = affs
    return lookup


def local_schedule(state: SchedulingState, strategy: Strategy = "random") -> Dict[str, Any]:
    """Generate shift assignments locally without calling an LLM."""
    location = state["current_location"]
    location_id = str(location.get("id", ""))
    timezone_str = location.get("timezone", "UTC")
    tz_offset = _tz_offset_for_location(timezone_str)
    shift_template = state["current_shift_template"]
    employees = state["current_employees"]
    week_start_date = state["week_start_date"]

    num_days = state.get("num_days", 7)
    date_map = _build_date_map(week_start_date, num_days)
    date_to_day = {date: day for day, date in date_map.items()}
    day_to_date = date_map

    weekly_schedule: Dict[str, List[dict]] = shift_template.get("weekly_schedule", {})

    role_name_to_id: Dict[str, str] = {}
    for slots in weekly_schedule.values():
        for slot in slots:
            rname = slot.get("role_name", "")
            rid = slot.get("role_id", "")
            if rname and rid:
                role_name_to_id[rname] = rid

    eligible_map = _build_eligible_map(employees, weekly_schedule, date_to_day)
    affinity_lookup = _build_affinity_lookup(employees)

    # Track assignments to prevent same employee overlapping on same day
    assigned_times: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}

    # Track per-role assignment counts for rotation strategy
    role_fill_counts: Dict[Tuple[str, str], int] = {}

    # Track which employees are assigned to each shift window
    # (date, start_hm, end_hm) -> set of employee_ids
    shift_coworkers: Dict[Tuple[str, str, str], Set[str]] = {}

    shifts: List[ShiftAssignment] = []

    for day, slots in weekly_schedule.items():
        slot_date = day_to_date.get(day, "")
        if not slot_date:
            continue

        for slot in slots:
            role_name = slot.get("role_name", "Unknown")
            role_id = role_name_to_id.get(role_name, "")
            headcount = slot.get("headcount", 1)
            start_hm = slot.get("start_time", "00:00")
            end_hm = slot.get("end_time", "23:59")
            start_iso = f"{slot_date}T{start_hm}:00{tz_offset}"
            end_iso = f"{slot_date}T{end_hm}:00{tz_offset}"

            coworker_key = (slot_date, start_hm, end_hm)
            candidates = list(eligible_map.get((day, role_name), []))

            for _ in range(headcount):
                # Filter out candidates who already have an overlapping shift
                available = []
                for c in candidates:
                    eid = c["id"]
                    existing = assigned_times.get((eid, slot_date), [])
                    has_overlap = any(
                        s < end_hm and start_hm < e
                        for s, e in existing
                    )
                    if not has_overlap:
                        available.append(c)

                if not available:
                    break

                # Apply hard affinity constraints: remove candidates with
                # -1.0 affinity against anyone already in this shift window
                current_coworkers = shift_coworkers.get(coworker_key, set())
                available = _filter_hard_negatives(
                    available, current_coworkers, affinity_lookup
                )

                if not available:
                    break

                chosen = _pick_employee(
                    available, role_name, role_fill_counts, strategy,
                    current_coworkers, affinity_lookup,
                )

                shift: ShiftAssignment = {
                    "employee_id": str(chosen["id"]),
                    "employee_name": str(chosen["full_name"]),
                    "role_id": role_id,
                    "role_name": role_name,
                    "location_id": location_id,
                    "date": slot_date,
                    "start_time": start_iso,
                    "end_time": end_iso,
                    "status": "ok",
                }
                shifts.append(shift)

                eid = str(chosen["id"])
                assigned_times.setdefault((eid, slot_date), []).append((start_hm, end_hm))
                role_fill_counts[(eid, role_name)] = role_fill_counts.get((eid, role_name), 0) + 1
                shift_coworkers.setdefault(coworker_key, set()).add(eid)

                candidates = [c for c in candidates if c["id"] != chosen["id"]]

    logger.warning(
        "[SCHED-TRACE] local_schedule: location=%s strategy=%s generated %d shifts",
        location.get("name", location_id), strategy, len(shifts),
    )

    return {
        "current_parsed_shifts": shifts,
        "current_raw_response": json.dumps([dict(s) for s in shifts]),
        "total_input_tokens": state["total_input_tokens"],
        "total_output_tokens": state["total_output_tokens"],
    }


def _filter_hard_negatives(
    available: List[Dict[str, Any]],
    current_coworkers: Set[str],
    affinity_lookup: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Remove candidates that have a hard-negative (-1.0) affinity with any
    employee already assigned to the same shift window."""
    if not current_coworkers:
        return available

    filtered: List[Dict[str, Any]] = []
    for candidate in available:
        eid = str(candidate["id"])
        affs = affinity_lookup.get(eid, [])
        has_hard_negative = False
        for aff in affs:
            if aff["level"] == -1.0 and aff["target_id"] in current_coworkers:
                has_hard_negative = True
                break
        # Also check the reverse direction: coworkers who have -1.0 toward this candidate
        if not has_hard_negative:
            for cw_id in current_coworkers:
                for aff in affinity_lookup.get(cw_id, []):
                    if aff["level"] == -1.0 and aff["target_id"] == eid:
                        has_hard_negative = True
                        break
                if has_hard_negative:
                    break
        if not has_hard_negative:
            filtered.append(candidate)

    return filtered


def _affinity_score(
    eid: str,
    current_coworkers: Set[str],
    affinity_lookup: Dict[str, List[Dict[str, Any]]],
) -> float:
    """Compute an affinity adjustment score for a candidate.

    Positive affinities with current coworkers lower the score (better).
    Negative affinities raise it (worse).  Hard constraints (|1.0|) are
    handled upstream by _filter_hard_negatives, so this only handles soft
    preferences and the must-schedule-together bonus.

    Returns a value where lower = more preferred.
    """
    if not current_coworkers:
        return 0.0

    total = 0.0
    affs = affinity_lookup.get(eid, [])
    for aff in affs:
        if aff["target_id"] in current_coworkers:
            # Positive level = prefer together → reduce score
            # Negative level = prefer apart → increase score
            total -= aff["level"] * 50  # weight: up to +/-50 points

    # Check reverse affinities (coworkers' affinity toward this candidate)
    for cw_id in current_coworkers:
        for aff in affinity_lookup.get(cw_id, []):
            if aff["target_id"] == eid:
                total -= aff["level"] * 50
                break

    return total


def _pick_employee(
    available: List[Dict[str, Any]],
    role_name: str,
    role_fill_counts: Dict[Tuple[str, str], int],
    strategy: Strategy,
    current_coworkers: Set[str],
    affinity_lookup: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Select one employee from *available* based on *strategy* and affinities.

    All strategies incorporate affinity scores.  Hard negative affinities are
    already filtered out upstream.

    Scoring (lower = better):
    - opportunity cost: _num_required_roles * 100
    - rotation penalty (rotation only): fills * 10
    - skill bonus: -skill_level
    - affinity adjustment: +/- up to 100 per coworker relationship
    """
    if strategy == "random":
        # Compute affinity-adjusted opportunity cost, pick from best tier
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp = e.get("_num_required_roles", 99)
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            score = opp * 100 + aff
            scored.append((score, e))

        scored.sort(key=lambda x: x[0])
        best_score = scored[0][0]
        tied = [e for s, e in scored if s == best_score]
        return random.choice(tied)

    elif strategy == "rotation":
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for e in available:
            eid = str(e["id"])
            opp_cost = e.get("_num_required_roles", 99)
            fills = role_fill_counts.get((eid, role_name), 0)
            aff = _affinity_score(eid, current_coworkers, affinity_lookup)
            score = opp_cost * 100 + fills * 10 - e.get("_skill", 0) + aff
            scored.append((score, e))

        scored.sort(key=lambda x: x[0])
        best_score = scored[0][0]
        tied = [e for s, e in scored if s == best_score]
        return random.choice(tied)

    return random.choice(available)
