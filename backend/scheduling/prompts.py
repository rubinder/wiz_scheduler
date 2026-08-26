from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple


def _build_date_map(week_start_date: str, num_days: int = 7) -> Dict[str, str]:
    """Build a mapping of day names to YYYY-MM-DD dates for the schedule range."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    start = datetime.strptime(week_start_date, "%Y-%m-%d")
    start_weekday = start.weekday()  # 0=Mon, 1=Tue, ...
    return {
        day_names[(start_weekday + i) % 7]: (start + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(num_days)
    }


def _parse_avail_by_day(
    windows: List[Dict[str, str]],
    date_to_day: Dict[str, str],
) -> Dict[str, List[Tuple[str, str]]]:
    """Convert ISO windows to {day_name: [(start_HH:MM, end_HH:MM), ...]}.

    Availability is persisted as *local wall-clock tagged UTC*: the write
    paths (employees.create_availability, import_7shifts, import_deputy)
    stamp the local minute-of-day with a ``+00:00`` offset WITHOUT converting
    from local to UTC. The ``year/month/day`` columns hold the local date.
    Template slot times are likewise plain ``HH:MM`` strings in the location's
    local wall-clock. Both sides therefore share one naive local frame, so the
    window's date and HH:MM are read directly from the stored value with no
    timezone conversion. Re-converting into the location timezone would shift
    availability off the (un-converted) template slots — see
    tests/test_avail_local_wallclock.py.
    """
    day_windows: Dict[str, List[Tuple[str, str]]] = {}
    for w in windows:
        try:
            start_dt = datetime.fromisoformat(w.get("start", ""))
            end_dt = datetime.fromisoformat(w.get("end", ""))
            date_str = start_dt.strftime("%Y-%m-%d")
            day_name = date_to_day.get(date_str)
            if day_name:
                day_windows.setdefault(day_name, []).append(
                    (start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"))
                )
        except (ValueError, TypeError):
            continue
    return day_windows


def _time_covers(
    avail_ranges: List[Tuple[str, str]],
    slot_start: str,
    slot_end: str,
) -> bool:
    """Check if any availability range fully contains the slot time range.

    Must match the validator's containment check: w_start <= shift_start AND
    w_end >= shift_end.  Using string comparison on HH:MM works because both
    are zero-padded 24-hour format.
    """
    for a_start, a_end in avail_ranges:
        if a_start <= slot_start and a_end >= slot_end:
            return True
    return False


def _blackout_blocks(
    blackouts: List[Dict[str, str]],
    day_name: str,
    slot_start: str,
    slot_end: str,
) -> bool:
    """Return True if any blackout for *day_name* overlaps [slot_start, slot_end).

    Blackouts are stored as {"day": "Monday", "start": "HH:MM", "end": "HH:MM"}.
    Half-open overlap: two ranges overlap iff a.start < b.end AND b.start < a.end.
    """
    for bo in blackouts or []:
        if bo.get("day") != day_name:
            continue
        bo_start = bo.get("start", "")
        bo_end = bo.get("end", "")
        if not bo_start or not bo_end:
            continue
        if slot_start < bo_end and bo_start < slot_end:
            return True
    return False


def eligible_for_slot(
    prepared_employees: List[Dict[str, Any]],
    day: str,
    role_name: str,
    start: str,
    end: str,
    day_index: int | None = None,
    range_counts: Dict[Any, int] | None = None,
) -> List[Dict[str, Any]]:
    """Employees eligible for one (day, role, time) slot.

    THE single eligibility gate. Both the deterministic scheduler and the
    prompt builder call this — they previously held independent copies of the
    same four filters, which is why the weight-1.0 hard preference filter
    lives here rather than in either caller individually: a candidate removed
    at this point is invisible to the deterministic scheduler's sorting code,
    so a hard constraint cannot be violated there.

    The prompt builder (build_schedule_prompt) also calls this function, but
    today it does so without `day_index`, so the hard preference filter is
    currently a no-op on that call — the language model still sees every
    otherwise-eligible candidate regardless of preferences. Enforcing hard
    preferences on the AI path is handled separately by a later task; do not
    read this docstring as a guarantee that preferences are honoured there
    yet.

    Callers must pass employees already prepared with `_role_names` and
    `_day_windows`. Each returned dict is the input dict plus `_skill` for the
    requested role.

    `day_index` and `range_counts` are optional: omitting `day_index` skips
    the weight-1.0 hard preference filter entirely, which is what keeps
    existing callers (and the no-preference case) byte-identical.
    """
    eligible: List[Dict[str, Any]] = []
    for e in prepared_employees:
        if role_name not in e["_role_names"]:
            continue
        day_ranges = e["_day_windows"].get(day, [])
        if not day_ranges:
            continue
        if not _time_covers(day_ranges, start, end):
            continue
        if _blackout_blocks(e.get("day_blackouts", []), day, start, end):
            continue
        if day_index is not None:
            from backend.scheduling.preferences import blocked_by_hard_preference

            if blocked_by_hard_preference(e, day_index, start, end, range_counts or {}):
                continue
        skill = next(
            (
                r.get("skill_level", 0)
                for r in e.get("roles", [])
                if r.get("role_name") == role_name
            ),
            0,
        )
        eligible.append({**e, "_skill": skill})
    return eligible


def _format_avail_str(day_windows: Dict[str, List[Tuple[str, str]]]) -> str:
    """Format day-based availability into a readable string."""
    if not day_windows:
        return "NONE"
    parts: List[str] = []
    for day, ranges in day_windows.items():
        range_strs = ", ".join(f"{s}-{e}" for s, e in ranges)
        parts.append(f"{day}: {range_strs}")
    return "; ".join(parts)


def build_schedule_prompt(
    location: Dict[str, Any],
    shift_template: Dict[str, Any],
    employees: List[Dict[str, Any]],
    week_start_date: str,
    conflict_notes: str = "",
    num_days: int = 7,
) -> str:
    """Build the full scheduling prompt for a single location."""
    date_map = _build_date_map(week_start_date, num_days)
    date_to_day = {date: day for day, date in date_map.items()}
    tz_offset = _tz_offset_example(location["timezone"])

    weekly_schedule: Dict[str, List[dict]] = shift_template.get("weekly_schedule", {})

    # Collect all role names required by the template
    required_roles: set[str] = set()
    for slots in weekly_schedule.values():
        for slot in slots:
            rname = slot.get("role_name", "")
            if rname:
                required_roles.add(rname)

    # Only consider days that have shift requirements
    scheduled_days = set(weekly_schedule.keys())

    # Pre-compute per-employee: roles set, day availability
    emp_data: List[Dict[str, Any]] = []
    for emp in employees:
        role_names = {r.get("role_name", "") for r in emp.get("roles", [])}
        # Only include employees who have at least one required role
        if not role_names & required_roles:
            continue
        day_windows = _parse_avail_by_day(
            emp.get("available_windows", []),
            date_to_day,
        )
        # Filter availability to only scheduled days
        day_windows = {d: w for d, w in day_windows.items() if d in scheduled_days}
        emp_data.append({
            **emp,
            "_role_names": role_names,
            "_day_windows": day_windows,
            "_roles_display": ", ".join(
                f'{r.get("role_name", "")} (skill {r.get("skill_level", 0)})'
                for r in emp.get("roles", [])
                if r.get("role_name", "") in required_roles
            ),
            "_avail_display": _format_avail_str(day_windows),
        })

    # Build SHIFT REQUIREMENTS with pre-computed eligible employees per slot
    req_lines: List[str] = []
    for day, slots in weekly_schedule.items():
        for slot in slots:
            role_name = slot.get("role_name", "Unknown")
            headcount = slot.get("headcount", 1)
            start = slot.get("start_time", "??:??")
            end = slot.get("end_time", "??:??")

            # Find eligible employees: have the role AND available that day+time
            # AND are not blocked by a per-day blackout.
            candidates = eligible_for_slot(emp_data, day, role_name, start, end)
            eligible = [f'{c["id"]} [skill={c["_skill"]}]' for c in candidates]

            eligible_str = ", ".join(eligible) if eligible else "NONE AVAILABLE"
            req_lines.append(
                f"{day} | {role_name} | need {headcount} | {start}-{end}\n"
                f"  Eligible: {eligible_str}"
            )

    requirements_block = "\n".join(req_lines)

    # Build employee roster (only role-relevant employees)
    roster_lines: List[str] = []
    for e in emp_data:
        affinities = e.get("affinities", [])
        aff_str = ""
        if affinities:
            aff_parts = [f'{a.get("target_id", "")}:{a.get("level", 0)}' for a in affinities]
            aff_str = f"\n  affinities: [{', '.join(aff_parts)}]"
        roster_lines.append(
            f"- {e['id']}\n"
            f"  roles: [{e['_roles_display']}]\n"
            f"  available: {e['_avail_display']}{aff_str}"
        )

    roster_block = "\n".join(roster_lines) if roster_lines else "(no eligible employees)"

    # Date reference — only show days that have shift requirements
    scheduled_days = set(weekly_schedule.keys())
    date_ref = "\n".join(
        f"{day} = {date}" for day, date in date_map.items()
        if day in scheduled_days
    )

    conflict_section = ""
    if conflict_notes:
        conflict_section = (
            f"PREVIOUS CONFLICTS\n"
            f"==================\n"
            f"{conflict_notes}\n\n"
        )

    # Affinity section — only if any employee has affinities
    has_affinities = any(e.get("affinities") for e in emp_data)
    affinity_section = ""
    if has_affinities:
        affinity_section = (
            f"AFFINITY RULES\n"
            f"==============\n"
            f"level =  1.0  → MUST schedule together on every shared shift (hard constraint)\n"
            f"level = -1.0  → MUST NOT share any shift (hard constraint)\n"
            f"|level| < 1   → soft preference; best effort\n"
            f"\n"
        )

    prompt = (
        f"You are a scheduling assistant for {location['name']} (timezone: {location['timezone']}).\n"
        f"\n"
        f"DATE REFERENCE\n"
        f"==============\n"
        f"{date_ref}\n"
        f"\n"
        f"SHIFT REQUIREMENTS (with eligible employees)\n"
        f"=============================================\n"
        f"{requirements_block}\n"
        f"\n"
        f"EMPLOYEE ROSTER\n"
        f"===============\n"
        f"{roster_block}\n"
        f"\n"
        f"{affinity_section}"
        f"{conflict_section}"
        f"INSTRUCTIONS\n"
        f"============\n"
        f"1. ONLY create shifts for the days and roles listed in SHIFT REQUIREMENTS above.\n"
        f"   Do NOT create shifts for days that have no requirements.\n"
        f"2. FILL EVERY SLOT from the Eligible list. For each shift requirement, pick employees\n"
        f"   from its Eligible list up to the required headcount. Only leave a slot unfilled if\n"
        f"   its Eligible list says \"NONE AVAILABLE\".\n"
        f"3. Do not assign the same employee to overlapping time slots on the same day.\n"
        f"4. Distribute hours fairly — avoid giving one employee all the shifts.\n"
        f"5. Prefer higher skill_level employees.\n"
        f"6. Honour affinity constraints if present.\n"
        f"\n"
        f"OUTPUT FORMAT\n"
        f"=============\n"
        f"Use the submit_schedule tool. For each assignment provide:\n"
        f"  - employee_id: the ID from the Eligible list\n"
        f"  - role_name: exact role name from the shift requirement\n"
        f"  - date: YYYY-MM-DD (from DATE REFERENCE)\n"
        f"  - start_time: full ISO 8601, e.g. {date_map.get('Monday', week_start_date)}T09:00:00{tz_offset}\n"
        f"  - end_time: full ISO 8601, e.g. {date_map.get('Monday', week_start_date)}T17:00:00{tz_offset}\n"
    )
    return prompt


def _tz_offset_example(timezone_str: str) -> str:
    """Return a representative UTC offset string for the given timezone."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(timezone_str)
        offset = datetime(2026, 3, 30, 12, 0, tzinfo=tz).strftime("%z")
        return f"{offset[:3]}:{offset[3:]}"
    except Exception:
        return "+00:00"
