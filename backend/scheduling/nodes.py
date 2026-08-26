import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

import anthropic

from backend.config import settings
from backend.scheduling.local_scheduler import _min_rest_violation
from backend.scheduling.preferences import matches_range
from backend.scheduling.prompts import build_schedule_prompt
from backend.scheduling.state import LocationResult, SchedulingState, ShiftAssignment

logger = logging.getLogger(__name__)


def _filter_employees_for_location(
    employees: List[Dict[str, Any]],
    location_id: str,
) -> List[Dict[str, Any]]:
    """Return employees whose location_ids includes the given location_id."""
    filtered: List[Dict[str, Any]] = []
    for emp in employees:
        emp_location_ids = emp.get("location_ids") or []
        if location_id in emp_location_ids:
            filtered.append(emp)
    return filtered


def _wall_clock(ts: str) -> datetime:
    """Parse a timestamp as a naive local wall-clock face, ignoring its offset.

    The two sides of the availability comparison use different conventions and
    must NOT be reconciled by converting either one:

      * Availability is stored as local wall-clock TAGGED UTC — a 09:00 shift
        window is persisted as "09:00:00+00:00" regardless of the location's
        real zone. That is the deliberate contract from #61, and calling
        .astimezone() on it is what that PR exists to prevent.
      * Generated shifts carry the location's REAL offset, e.g. "09:00:00-04:00".

    Compared as instants those two differ by the location's offset, which is
    why cross-location double-booking slipped through (#85): 09:00-04:00 is
    13:00 UTC and never looked like it overlapped 09:00+00:00. Compared as
    wall-clock faces they correctly coincide.
    """
    return datetime.fromisoformat(ts).replace(tzinfo=None)


def _subtract_consumed(
    window_start: datetime,
    window_end: datetime,
    consumed: List[Tuple[datetime, datetime]],
) -> List[Tuple[datetime, datetime]]:
    """Carve every consumed interval out of one availability window.

    Returns the remaining sub-intervals, in order. A consumed span in the
    middle splits the window in two; one that covers it entirely returns [].
    All datetimes are naive wall-clock (see _wall_clock).
    """
    remaining: List[Tuple[datetime, datetime]] = [(window_start, window_end)]
    for c_start, c_end in consumed:
        if c_end <= c_start:
            continue
        nxt: List[Tuple[datetime, datetime]] = []
        for r_start, r_end in remaining:
            if c_end <= r_start or c_start >= r_end:
                nxt.append((r_start, r_end))
                continue
            if c_start > r_start:
                nxt.append((r_start, c_start))
            if c_end < r_end:
                nxt.append((c_end, r_end))
        remaining = nxt
    return remaining


def _filter_availability_against_draft(
    employee: Dict[str, Any],
    availability_draft: Dict[str, List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Remove already-consumed time from an employee's available_windows.

    This is the guard that stops one employee being scheduled at two locations
    at once. Consumed time is genuinely subtracted, so a window that is only
    partly used comes back as its unused remainder(s) rather than being kept
    whole — keeping it whole is what allowed the same hours to be booked twice.

    Comparison is done on wall-clock faces, never on instants; see _wall_clock.
    Output timestamps are rebuilt with each window's own original offset so the
    shape handed downstream is unchanged.
    """
    emp_id = str(employee.get("id", ""))
    windows: List[Dict[str, str]] = list(employee.get("available_windows", []))
    consumed_raw: List[Dict[str, str]] = availability_draft.get(emp_id, [])

    if not consumed_raw:
        return windows

    consumed: List[Tuple[datetime, datetime]] = []
    for c in consumed_raw:
        try:
            consumed.append((_wall_clock(c["start"]), _wall_clock(c["end"])))
        except (KeyError, ValueError, TypeError):
            # A window we cannot parse is skipped rather than raised on: the
            # scheduling graph degrades, it never throws.
            continue

    if not consumed:
        return windows

    remaining: List[Dict[str, str]] = []
    for window in windows:
        try:
            w_start_aware = datetime.fromisoformat(window["start"])
            w_end_aware = datetime.fromisoformat(window["end"])
        except (KeyError, ValueError, TypeError):
            remaining.append(window)
            continue

        tz = w_start_aware.tzinfo
        pieces = _subtract_consumed(
            w_start_aware.replace(tzinfo=None),
            w_end_aware.replace(tzinfo=None),
            consumed,
        )
        for p_start, p_end in pieces:
            remaining.append({
                "start": p_start.replace(tzinfo=tz).isoformat(),
                "end": p_end.replace(tzinfo=tz).isoformat(),
            })

    return remaining


def load_location_context(state: SchedulingState) -> Dict[str, Any]:
    """Load context for the current location: filter employees and availability."""
    location = state["locations"][state["current_location_index"]]
    location_id = str(location["id"])
    location_name = location.get("name", location_id)
    shift_template = state["shift_templates"].get(location_id, {})

    # Collect role names required by this location's shift template
    required_role_names: set[str] = set()
    weekly_schedule = shift_template.get("weekly_schedule", {})
    for slots in weekly_schedule.values():
        for slot in slots:
            rname = slot.get("role_name", "")
            if rname:
                required_role_names.add(rname)

    logger.warning(
        "[SCHED-TRACE] load_location_context: location=%s required_roles=%s",
        location_name, required_role_names,
    )

    # Filter employees for this location
    all_location_employees = _filter_employees_for_location(
        state["employees"], location_id
    )
    logger.warning(
        "[SCHED-TRACE]   employees at location: %d (ids: %s)",
        len(all_location_employees),
        [e.get("id", "") for e in all_location_employees],
    )

    # Further filter to only employees who have at least one required role
    location_employees = all_location_employees
    if required_role_names:
        role_relevant: List[Dict[str, Any]] = []
        for emp in all_location_employees:
            emp_roles = {r.get("role_name", "") for r in emp.get("roles", [])}
            matched = emp_roles & required_role_names
            if matched:
                role_relevant.append(emp)
            else:
                logger.warning(
                    "[SCHED-TRACE]   FILTERED OUT %s — roles %s don't match required %s",
                    emp.get("id", ""), emp_roles, required_role_names,
                )
        location_employees = role_relevant

    logger.warning(
        "[SCHED-TRACE]   role-relevant employees: %d", len(location_employees),
    )

    # Log each employee's availability window count
    for emp in location_employees:
        windows_before = len(emp.get("available_windows", []))
        emp["available_windows"] = _filter_availability_against_draft(
            emp, state["availability_draft"]
        )
        windows_after = len(emp["available_windows"])
        emp_roles = [r.get("role_name", "") for r in emp.get("roles", [])]
        logger.warning(
            "[SCHED-TRACE]   employee %s: roles=%s, avail_windows=%d (was %d before draft filter)",
            emp.get("id", ""), emp_roles, windows_after, windows_before,
        )

    return {
        "current_location": location,
        "current_shift_template": shift_template,
        "current_employees": location_employees,
        "retry_count": 0,
        "conflict_notes": "",
        "current_parsed_shifts": [],
        "current_raw_response": "",
        "current_prompt": "",
    }


def build_prompt(state: SchedulingState) -> Dict[str, Any]:
    """Build the LLM prompt for the current location."""
    location = state["current_location"]
    shift_template = state["current_shift_template"]
    employees = state["current_employees"]
    conflict_notes = state.get("conflict_notes", "")

    prompt = build_schedule_prompt(
        location=location,
        shift_template=shift_template,
        employees=employees,
        week_start_date=state["week_start_date"],
        conflict_notes=conflict_notes,
        num_days=state.get("num_days", 7),
    )
    logger.warning(
        "[SCHED-TRACE] build_prompt: location=%s prompt_length=%d chars",
        location.get("name", ""), len(prompt),
    )
    return {"current_prompt": prompt}


SHIFT_SCHEDULE_TOOL: Dict[str, Any] = {
    "name": "submit_schedule",
    "description": "Submit the generated shift schedule for the location.",
    "input_schema": {
        "type": "object",
        "properties": {
            "shifts": {
                "type": "array",
                "description": "Array of shift assignments for the week.",
                "items": {
                    "type": "object",
                    "properties": {
                        "employee_id": {
                            "type": "string",
                            "description": "UUID of the employee.",
                        },
                        "role_name": {
                            "type": "string",
                            "description": "Role name from the shift template.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Date of the shift in YYYY-MM-DD format.",
                        },
                        "start_time": {
                            "type": "string",
                            "description": "Shift start in ISO 8601 with tz offset (YYYY-MM-DDTHH:MM:SS+HH:MM).",
                        },
                        "end_time": {
                            "type": "string",
                            "description": "Shift end in ISO 8601 with tz offset (YYYY-MM-DDTHH:MM:SS+HH:MM).",
                        },
                    },
                    "required": [
                        "employee_id",
                        "role_name",
                        "date",
                        "start_time",
                        "end_time",
                    ],
                },
            },
        },
        "required": ["shifts"],
    },
}


async def call_llm(state: SchedulingState) -> Dict[str, Any]:
    """Invoke Anthropic Claude with tool use to generate a schema-constrained schedule."""
    location = state["current_location"]
    location_id = str(location.get("id", ""))

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        max_retries = 3
        message = None
        for attempt in range(max_retries):
            try:
                # Use streaming to avoid SDK timeout on long requests
                async with client.messages.stream(
                    model=settings.SCHEDULING_MODEL,
                    max_tokens=64000,
                    tools=[SHIFT_SCHEDULE_TOOL],
                    tool_choice={"type": "tool", "name": "submit_schedule"},
                    messages=[
                        {"role": "user", "content": state["current_prompt"]},
                    ],
                ) as stream:
                    message = await stream.get_final_message()
                break
            except anthropic.APIStatusError as api_err:
                if api_err.status_code in (429, 529) and attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise

        # Check if output was truncated
        stop_reason = message.stop_reason if message else "unknown"
        input_tokens = message.usage.input_tokens if message.usage else 0
        output_tokens = message.usage.output_tokens if message.usage else 0
        logger.warning(
            "[SCHED-TRACE] call_llm: location=%s stop_reason=%s input_tokens=%d output_tokens=%d",
            location.get("name", location_id), stop_reason, input_tokens, output_tokens,
        )
        if stop_reason == "max_tokens":
            logger.warning(
                "[SCHED-TRACE] WARNING: LLM output was TRUNCATED (hit max_tokens). "
                "Schedule will be incomplete — expect VACANT shifts.",
            )

        # Extract the tool use input
        raw_response = ""
        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_schedule":
                raw_response = json.dumps(block.input.get("shifts", []))
                break

        if not raw_response:
            logger.warning(
                "[SCHED-TRACE] call_llm: no submit_schedule tool_use found in response. "
                "Content blocks: %s",
                [(b.type, getattr(b, 'name', None)) for b in message.content] if message else "none",
            )

        return {
            "current_raw_response": raw_response,
            "total_input_tokens": state["total_input_tokens"] + input_tokens,
            "total_output_tokens": state["total_output_tokens"] + output_tokens,
        }
    except Exception as exc:
        error_msg = f"LLM_ERROR for location {location_id}: {exc}"
        failure_entry = {
            "category": "SCHEDULING",
            "severity": "error",
            "source": "scheduling.call_llm",
            "message": error_msg,
            "detail": {
                "location_id": location_id,
                "exception_type": type(exc).__name__,
                "exception": str(exc),
            },
        }
        return {
            "current_raw_response": "",
            "errors": state["errors"] + [error_msg],
            "failure_entries": state.get("failure_entries", []) + [failure_entry],
        }


def parse_schedule(state: SchedulingState) -> Dict[str, Any]:
    """Parse the tool-use JSON array of shifts into ShiftAssignment dicts. Never raises."""
    raw = state["current_raw_response"]
    location = state["current_location"]
    location_id = str(location.get("id", ""))
    shift_template = state["current_shift_template"]

    # Build a role_name -> role_id lookup from the shift template
    role_name_to_id: Dict[str, str] = {}
    weekly_schedule = shift_template.get("weekly_schedule", {})
    for _day, slots in weekly_schedule.items():
        for slot in slots:
            rname = slot.get("role_name", "")
            rid = slot.get("role_id", "")
            if rname and rid:
                role_name_to_id[rname] = rid

    if not raw.strip():
        error_msg = f"PARSE_ERROR for location {location_id}: empty LLM response"
        failure_entry = {
            "category": "SCHEDULING",
            "severity": "warning",
            "source": "scheduling.parse_schedule",
            "message": error_msg,
            "detail": {"location_id": location_id, "reason": "empty response"},
        }
        return {
            "current_parsed_shifts": [],
            "errors": state["errors"] + [error_msg],
            "failure_entries": state.get("failure_entries", []) + [failure_entry],
        }

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        error_msg = f"PARSE_ERROR for location {location_id}: {exc}"
        failure_entry = {
            "category": "SCHEDULING",
            "severity": "warning",
            "source": "scheduling.parse_schedule",
            "message": error_msg,
            "detail": {"location_id": location_id, "reason": str(exc)},
        }
        return {
            "current_parsed_shifts": [],
            "errors": state["errors"] + [error_msg],
            "failure_entries": state.get("failure_entries", []) + [failure_entry],
        }

    if not isinstance(parsed, list):
        error_msg = f"PARSE_ERROR for location {location_id}: response is not a JSON array"
        failure_entry = {
            "category": "SCHEDULING",
            "severity": "warning",
            "source": "scheduling.parse_schedule",
            "message": error_msg,
            "detail": {"location_id": location_id, "reason": "not a list"},
        }
        return {
            "current_parsed_shifts": [],
            "errors": state["errors"] + [error_msg],
            "failure_entries": state.get("failure_entries", []) + [failure_entry],
        }

    logger.warning(
        "[SCHED-TRACE] parse_schedule: location=%s role_name_to_id=%s LLM returned %d shifts",
        location.get("name", location_id), role_name_to_id, len(parsed),
    )

    shifts: List[ShiftAssignment] = []
    for item in parsed:
        role_name = item.get("role_name", "")
        role_id = role_name_to_id.get(role_name, "")

        if not role_id:
            logger.warning(
                "[SCHED-TRACE]   parse: role_name=%r NOT FOUND in template role_name_to_id — role_id will be empty",
                role_name,
            )

        shift: ShiftAssignment = {
            "employee_id": str(item.get("employee_id", "")),
            "employee_name": str(item.get("employee_name", "")),
            "role_id": role_id,
            "role_name": role_name,
            "location_id": location_id,
            "date": str(item.get("date", "")),
            "start_time": str(item.get("start_time", "")),
            "end_time": str(item.get("end_time", "")),
            "status": "ok",
        }
        shifts.append(shift)
        logger.warning(
            "[SCHED-TRACE]   parsed shift: %s -> %s on %s %s-%s (role_id=%s)",
            shift["employee_id"], shift["role_name"], shift["date"],
            shift["start_time"], shift["end_time"], shift["role_id"],
        )

    return {"current_parsed_shifts": shifts}


def validate_schedule(state: SchedulingState) -> Dict[str, Any]:
    """Validate parsed shifts against business rules. Runs after parse_schedule.

    Checks:
    1. Each shift references a known employee for this location
    2. Each employee is qualified for their assigned role
    3. Shift start_time < end_time and date falls within the schedule week
    4. All required template slots are covered
    Marks invalid shifts with status="VALIDATION_ERROR" and logs warnings.
    """
    from datetime import timedelta

    shifts: List[ShiftAssignment] = list(state["current_parsed_shifts"])
    if not shifts:
        logger.warning("[SCHED-TRACE] validate_schedule: no shifts to validate")
        return {}

    location = state["current_location"]
    location_id = str(location.get("id", ""))
    employees = state["current_employees"]
    shift_template = state["current_shift_template"]
    week_start_date = state["week_start_date"]

    # Build lookup maps
    emp_by_id: Dict[str, Dict[str, Any]] = {str(e["id"]): e for e in employees}

    # Resolve real employee names from IDs (LLM receives pseudonymized names)
    for shift in shifts:
        eid = shift.get("employee_id", "")
        emp = emp_by_id.get(eid)
        if emp:
            shift["employee_name"] = emp.get("full_name", shift.get("employee_name", ""))
    role_equivalents: Dict[str, List[str]] = state.get("role_equivalents", {})

    # Build a complete role_id equivalence map that includes:
    #   - condensed role member mappings (role_equivalents)
    #   - same-name duplicate role IDs within the company
    # This lets us match a shift's role_id against any equivalent employee role_id.
    all_role_names: Dict[str, str] = {}  # role_id -> role_name
    name_to_ids: Dict[str, set[str]] = {}  # role_name -> set of role_ids
    for emp in employees:
        for r in emp.get("roles", []):
            rid = r.get("role_id", "")
            rname = r.get("role_name", "")
            if rid and rname:
                all_role_names[rid] = rname
                name_to_ids.setdefault(rname, set()).add(rid)
    # Also include role_ids from the shift template
    for slots in shift_template.get("weekly_schedule", {}).values():
        for slot in slots:
            rid = slot.get("role_id", "")
            rname = slot.get("role_name", "")
            if rid and rname:
                all_role_names[rid] = rname
                name_to_ids.setdefault(rname, set()).add(rid)

    # Build full equivalence: merge condensed role groups + same-name duplicates
    full_equivalents: Dict[str, set[str]] = {}
    # Start with condensed role equivalents
    for rid, equiv_set in role_equivalents.items():
        full_equivalents.setdefault(rid, set()).update(equiv_set)
    # Add same-name duplicates (e.g., two "Runner" IDs in the same company)
    for rname, ids in name_to_ids.items():
        if len(ids) > 1:
            for rid in ids:
                full_equivalents.setdefault(rid, set()).update(ids)

    # Expand employee role_ids using full equivalents
    emp_role_ids_by_id: Dict[str, set[str]] = {}
    emp_role_names_by_id: Dict[str, set[str]] = {}
    for emp in employees:
        eid = str(emp["id"])
        direct_ids = {r["role_id"] for r in emp.get("roles", [])}
        expanded_ids = set(direct_ids)
        for rid in direct_ids:
            expanded_ids.update(full_equivalents.get(rid, set()))
        emp_role_ids_by_id[eid] = expanded_ids
        emp_role_names_by_id[eid] = {r.get("role_name", "") for r in emp.get("roles", [])}

    logger.warning(
        "[SCHED-TRACE] validate_schedule: location=%s, %d shifts to validate, %d known employees",
        location.get("name", location_id), len(shifts), len(emp_by_id),
    )

    # Parse week boundaries
    num_days = state.get("num_days", 7)
    week_start = datetime.strptime(week_start_date, "%Y-%m-%d")
    week_end = week_start + timedelta(days=num_days)

    warnings: List[str] = []
    failure_entries: List[Dict[str, Any]] = []

    # Seed per-employee accumulator from prior locations processed in this
    # run, then add hours from shifts validated in *this* location to enforce
    # max_hours_per_week holistically.
    prior_weekly_hours: Dict[str, float] = dict(
        state.get("employee_weekly_hours_draft", {}) or {}
    )
    location_running_hours: Dict[str, float] = {}

    # Minimum-rest ("clopening") enforcement. Seed each employee's committed
    # shift windows from prior locations in this run (availability_draft holds
    # only assigned shifts and is updated *after* this node, so it reflects
    # earlier locations only), then grow it as shifts here are validated so
    # later shifts in this pass see the accumulated windows.
    min_rest_hours = location.get("min_rest_hours")
    rest_windows: Dict[str, List[Dict[str, str]]] = {
        eid: list(windows)
        for eid, windows in (state.get("availability_draft", {}) or {}).items()
    }

    valid_shifts: List[ShiftAssignment] = []
    for shift in shifts:
        emp_id = shift["employee_id"]
        issues: List[str] = []

        # 1. Known employee check
        if emp_id not in emp_by_id:
            issues.append(f"unknown employee_id {emp_id}")

        # 2. Role qualification check (expanded via condensed role equivalents).
        #    Fall back to role name match when IDs don't match — handles
        #    duplicate roles (same name, different IDs) in the same company.
        elif shift["role_id"]:
            id_match = shift["role_id"] in emp_role_ids_by_id.get(emp_id, set())
            name_match = shift["role_name"] in emp_role_names_by_id.get(emp_id, set())
            if not id_match and not name_match:
                issues.append(
                    f"employee {shift['employee_id']} not qualified for role {shift['role_name']} "
                    f"(shift_role_id={shift['role_id']}, emp_role_names={emp_role_names_by_id.get(emp_id, set())})"
                )

        # 3. Availability window check — shift must fall within an available window.
        #    Employees are unavailable by default: a shift is only valid if the
        #    employee has an explicit availability window that covers it.
        #    Availability is stored as local wall-clock tagged UTC and shift
        #    times carry the location offset, but both encode the same naive
        #    local wall-clock. Compare date + HH:MM directly WITHOUT timezone
        #    conversion — astimezone()ing the avail window (which only looks
        #    UTC) would shift it off the template slots and drop every shift
        #    at non-UTC locations. See tests/test_avail_local_wallclock.py.
        if emp_id in emp_by_id and not issues:
            emp_windows = emp_by_id[emp_id].get("available_windows", [])
            if not emp_windows:
                issues.append(
                    f"employee {shift['employee_id']} has no availability set "
                    f"(cannot be scheduled {shift['start_time']}-{shift['end_time']})"
                )
            else:
                try:
                    shift_start = datetime.fromisoformat(shift["start_time"])
                    shift_end = datetime.fromisoformat(shift["end_time"])
                    shift_date_str = shift_start.strftime("%Y-%m-%d")
                    shift_start_hm = shift_start.strftime("%H:%M")
                    shift_end_hm = shift_end.strftime("%H:%M")
                    covered = False
                    for w in emp_windows:
                        w_start = datetime.fromisoformat(w["start"])
                        w_end = datetime.fromisoformat(w["end"])
                        w_date_str = w_start.strftime("%Y-%m-%d")
                        if w_date_str != shift_date_str:
                            continue
                        w_start_hm = w_start.strftime("%H:%M")
                        w_end_hm = w_end.strftime("%H:%M")
                        if w_start_hm <= shift_start_hm and w_end_hm >= shift_end_hm:
                            covered = True
                            break
                    if not covered:
                        window_details = [
                            f"[{w['start']} to {w['end']}]" for w in emp_windows
                        ]
                        issues.append(
                            f"employee {shift['employee_id']} not available "
                            f"{shift['start_time']}-{shift['end_time']} "
                            f"(windows: {', '.join(window_details)})"
                        )
                except (ValueError, TypeError):
                    pass  # time format issues caught in next check

        # 4. Time validity check
        try:
            st = datetime.fromisoformat(shift["start_time"])
            et = datetime.fromisoformat(shift["end_time"])
            if st >= et:
                issues.append(f"start_time >= end_time ({shift['start_time']} >= {shift['end_time']})")
        except (ValueError, TypeError):
            issues.append(f"invalid start/end time format")

        # 5. Date within week check
        try:
            shift_date = datetime.strptime(shift["date"], "%Y-%m-%d")
            if shift_date < week_start or shift_date >= week_end:
                issues.append(f"date {shift['date']} outside schedule week {week_start_date}")
        except (ValueError, TypeError):
            issues.append(f"invalid date format: {shift['date']}")

        # 5b. Per-day-of-week blackout check — reject shifts that land inside
        #     a recurring "do not schedule" range for this employee.
        if emp_id in emp_by_id and not issues:
            blackouts = emp_by_id[emp_id].get("day_blackouts", [])
            if blackouts:
                try:
                    shift_start = datetime.fromisoformat(shift["start_time"])
                    shift_end = datetime.fromisoformat(shift["end_time"])
                    day_idx = datetime.strptime(shift["date"], "%Y-%m-%d").weekday()
                    day_names_validator = [
                        "Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday",
                    ]
                    day_name = day_names_validator[day_idx]
                    s_hm = shift_start.strftime("%H:%M")
                    e_hm = shift_end.strftime("%H:%M")
                    for bo in blackouts:
                        if bo.get("day") != day_name:
                            continue
                        bo_s = bo.get("start", "")
                        bo_e = bo.get("end", "")
                        if not bo_s or not bo_e:
                            continue
                        if s_hm < bo_e and bo_s < e_hm:
                            issues.append(
                                f"employee {emp_id} blocked by day blackout "
                                f"{day_name} {bo_s}-{bo_e} "
                                f"(shift {s_hm}-{e_hm})"
                            )
                            break
                except (ValueError, TypeError):
                    pass

        # 6. Per-employee weekly hour cap check. Accounts for hours already
        #    committed at earlier locations in this graph run plus any valid
        #    shifts already added for this same location pass.
        if emp_id in emp_by_id and not issues:
            cap = emp_by_id[emp_id].get("max_hours_per_week")
            if cap is not None:
                try:
                    st_iso = datetime.fromisoformat(shift["start_time"])
                    et_iso = datetime.fromisoformat(shift["end_time"])
                    duration_hrs = max(
                        (et_iso - st_iso).total_seconds() / 3600.0, 0.0
                    )
                    prior = prior_weekly_hours.get(emp_id, 0.0)
                    running = location_running_hours.get(emp_id, 0.0)
                    projected = prior + running + duration_hrs
                    if projected > float(cap):
                        issues.append(
                            f"employee {emp_id} would exceed max_hours_per_week "
                            f"({projected:.2f}h > {float(cap):.2f}h cap)"
                        )
                except (ValueError, TypeError):
                    pass  # invalid times already caught in check 4

        # 7. Minimum-rest ("clopening") check. A shift that leaves less than
        #    the location's min_rest_hours of rest before/after another of the
        #    employee's shifts on a different day is dropped (Fair Workweek).
        if emp_id in emp_by_id and not issues and min_rest_hours:
            if _min_rest_violation(
                shift["start_time"], shift["end_time"],
                rest_windows.get(emp_id, []),
                min_rest_hours,
            ):
                issues.append(
                    f"employee {emp_id} would violate minimum rest of "
                    f"{float(min_rest_hours):.1f}h between shifts (clopening)"
                )

        if issues:
            # Drop invalid shifts — unfilled slots will become VACANT in step 5
            detail = "; ".join(issues)
            warnings.append(
                f"Dropped shift {shift['employee_id']} on {shift['date']} {shift['role_name']}: {detail}"
            )
            logger.warning(
                "[SCHED-TRACE] DROPPED shift: %s -> %s on %s (%s-%s) REASON: %s",
                shift["employee_id"], shift["role_name"], shift["date"],
                shift["start_time"], shift["end_time"], detail,
            )
        else:
            valid_shifts.append(shift)
            logger.warning(
                "[SCHED-TRACE] VALID shift: %s -> %s on %s (%s-%s)",
                shift["employee_id"], shift["role_name"], shift["date"],
                shift["start_time"], shift["end_time"],
            )
            # Update per-location running total so subsequent shifts in this
            # same pass see the accumulated hours when checking the cap.
            try:
                st_iso = datetime.fromisoformat(shift["start_time"])
                et_iso = datetime.fromisoformat(shift["end_time"])
                duration_hrs = max(
                    (et_iso - st_iso).total_seconds() / 3600.0, 0.0
                )
                location_running_hours[emp_id] = (
                    location_running_hours.get(emp_id, 0.0) + duration_hrs
                )
            except (ValueError, TypeError):
                pass
            # Record window so later shifts this pass see it for min-rest.
            rest_windows.setdefault(emp_id, []).append(
                {"start": shift["start_time"], "end": shift["end_time"]}
            )

    logger.warning(
        "[SCHED-TRACE] validate_schedule: %d valid, %d dropped out of %d total",
        len(valid_shifts), len(state["current_parsed_shifts"]) - len(valid_shifts),
        len(state["current_parsed_shifts"]),
    )
    shifts = valid_shifts

    # 6. Template coverage check — inject VACANT placeholders for unfilled slots
    weekly_schedule = shift_template.get("weekly_schedule", {})
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    start_weekday = week_start.weekday()  # 0=Mon, 1=Tue, ...
    day_to_date: Dict[str, str] = {}
    date_to_day: Dict[str, str] = {}
    for i in range(num_days):
        day_name = day_names[(start_weekday + i) % 7]
        d = week_start + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        day_to_date[day_name] = d_str
        date_to_day[d_str] = day_name

    # Build role_name -> role_id from template
    template_role_ids: Dict[str, str] = {}
    for _day, slots in weekly_schedule.items():
        for slot in slots:
            rname = slot.get("role_name", "")
            rid = slot.get("role_id", "")
            if rname and rid:
                template_role_ids[rname] = rid

    # Count assigned shifts per (day, role_name) — only valid shifts
    assigned_counts: Dict[tuple[str, str], int] = {}
    for shift in shifts:
        if shift["status"] == "ok":
            day = date_to_day.get(shift["date"], "")
            key = (day, shift["role_name"])
            assigned_counts[key] = assigned_counts.get(key, 0) + 1

    for day, slots in weekly_schedule.items():
        for slot in slots:
            role_name = slot.get("role_name", "")
            role_id = template_role_ids.get(role_name, "")
            headcount = slot.get("headcount", 1)
            key = (day, role_name)
            filled = assigned_counts.get(key, 0)
            shortage = headcount - filled
            if shortage > 0:
                slot_date = day_to_date.get(day, "")
                slot_start = slot.get("start_time", "")
                slot_end = slot.get("end_time", "")
                # Build ISO timestamps from date + template time
                start_iso = f"{slot_date}T{slot_start}:00" if slot_date and slot_start else ""
                end_iso = f"{slot_date}T{slot_end}:00" if slot_date and slot_end else ""

                for _ in range(shortage):
                    vacant_shift: ShiftAssignment = {
                        "employee_id": "VACANT",
                        "employee_name": "VACANT-Staffing Shortage",
                        "role_id": role_id,
                        "role_name": role_name,
                        "location_id": location_id,
                        "date": slot_date,
                        "start_time": start_iso,
                        "end_time": end_iso,
                        "status": "VACANT",
                    }
                    shifts.append(vacant_shift)
                logger.warning(
                    "[SCHED-TRACE] VACANT: %s %s needs %d, filled %d, creating %d vacant "
                    "(assigned_counts key=(%s, %s), all keys=%s)",
                    day, role_name, headcount, filled, shortage,
                    day, role_name, list(assigned_counts.keys()),
                )
                warnings.append(
                    f"Staffing shortage: {day} {role_name} needs {headcount}, filled {filled} ({shortage} vacant)"
                )

    if warnings:
        failure_entries.append({
            "category": "SCHEDULING",
            "severity": "warning",
            "source": "scheduling.validate_schedule",
            "message": f"Validation warnings for location {location.get('name', '')} ({location_id})",
            "detail": {
                "location_id": location_id,
                "warnings": warnings,
            },
        })

    result: Dict[str, Any] = {"current_parsed_shifts": shifts}
    if failure_entries:
        result["failure_entries"] = state.get("failure_entries", []) + failure_entries
    return result


def _windows_overlap(
    start1: str, end1: str, start2: str, end2: str
) -> bool:
    """Check if two time windows overlap."""
    try:
        s1 = datetime.fromisoformat(start1)
        e1 = datetime.fromisoformat(end1)
        s2 = datetime.fromisoformat(start2)
        e2 = datetime.fromisoformat(end2)
        return s1 < e2 and s2 < e1
    except (ValueError, TypeError):
        return False


def _grow_range_counts(
    shift: ShiftAssignment,
    emp_by_id: Dict[str, Dict[str, Any]],
    range_counts_draft: Dict[Any, int],
) -> None:
    """Increment range_counts_draft for one committed shift.

    Mirrors the weekly_hours_draft growth beside each call site so a
    weight-1.0 hour_range_cap holds across the whole graph run rather than
    resetting at each location — the same cross-location pattern
    employee_weekly_hours_draft uses for max_hours_per_week.

    Shift times are ISO strings carrying the location's UTC offset but
    encoding local wall-clock (never .astimezone()'d — see
    tests/test_avail_local_wallclock.py), so the "HH:MM" is read directly
    off the string rather than through a timezone conversion.
    """
    emp = emp_by_id.get(shift["employee_id"])
    if not emp:
        return
    try:
        start_hm = shift["start_time"][11:16]
        end_hm = shift["end_time"][11:16]
        for cap in emp.get("hour_range_caps") or []:
            if matches_range(start_hm, end_hm, cap["start_time"], cap["end_time"]):
                key = (shift["employee_id"], cap["start_time"], cap["end_time"])
                range_counts_draft[key] = range_counts_draft.get(key, 0) + 1
    except (ValueError, TypeError, IndexError):
        # A malformed/unparseable timestamp must not raise out of the
        # scheduling graph. Skip this shift's contribution to the cap
        # count and move on -- under-counting is the safe direction (it
        # can only fail to block someone, never spuriously block them).
        pass


def _trim_cap_violations(
    shifts: List[ShiftAssignment],
    employee_preferences: Dict[str, Dict[str, Any]],
    range_counts_draft: Dict[Any, int],
) -> None:
    """Vacate shifts beyond a weight>=1.0 hour_range_cap's weekly allowance.

    The deterministic scheduler enforces frequency caps at pick-time via
    eligible_for_slot's hard filter, because it assigns one slot at a time
    and can consult a running range_counts as it goes. The AI path generates
    a whole week in a single LLM call, so no such running count exists until
    after generation -- this is the "enforced after generation instead"
    mentioned in this module's tests. It walks the week's shifts in date
    order, counts matches per (employee_id, cap range) with matches_range,
    and marks every assignment past max_per_week VACANT, keeping the
    earliest occurrences.

    `counts` is seeded from `range_counts_draft` -- the same cross-location
    running total employee_weekly_hours_draft's sibling accumulates in
    _grow_range_counts -- so a cap holds across the whole graph run rather
    than resetting to zero at each location.

    Only "ok" shifts feed the count and are eligible to be trimmed. A shift
    already marked CONFLICT is not actually worked, exactly like VACANT, so
    it must neither consume cap allowance nor have its CONFLICT status
    overwritten (this graph never raises -- status is the only channel a
    failure like CONFLICT reaches the manager through, and clobbering it
    back to VACANT would silently drop that signal from emit_result).

    Must run before _grow_range_counts is called on these shifts (both call
    sites below do this): a shift trimmed to VACANT here is then skipped by
    the existing "skip VACANT" / "status == ok" checks that guard
    _grow_range_counts, so a vacated shift -- one that is not actually
    worked -- never contributes to range_counts_draft for later locations
    in the same graph run.

    A no-op when `employee_preferences` is empty, which is the state of
    every graph run until _load_initial_state populates it. Never raises:
    a malformed shift or preference entry is skipped rather than blocking
    the rest of the pass, matching this node's degrade-don't-raise contract.
    """
    if not employee_preferences:
        return
    counts: Dict[Any, int] = dict(range_counts_draft or {})
    ordered = sorted(
        (s for s in shifts if s["status"] == "ok"),
        key=lambda s: (s.get("date", ""), s.get("start_time", "")),
    )
    for shift in ordered:
        prefs = employee_preferences.get(shift.get("employee_id", ""))
        if not prefs:
            continue
        try:
            start_hm = shift["start_time"][11:16]
            end_hm = shift["end_time"][11:16]
        except (KeyError, TypeError, IndexError):
            continue
        for cap in prefs.get("hour_range_caps") or []:
            try:
                if float(cap.get("weight", 0)) < 1.0:
                    continue
                if not matches_range(
                    start_hm, end_hm, cap["start_time"], cap["end_time"]
                ):
                    continue
                key = (shift["employee_id"], cap["start_time"], cap["end_time"])
                counts[key] = counts.get(key, 0) + 1
                if counts[key] > int(cap["max_per_week"]):
                    shift["status"] = "VACANT"
                    break
                # Not yet over the allowance for this cap -- keep checking
                # the employee's remaining caps; a shift can match more than
                # one overlapping range (e.g. a broad cap and a narrower one
                # nested inside it), and each must get its own chance to
                # trim.
            except (KeyError, ValueError, TypeError, IndexError):
                continue


def validate_and_update_availability(state: SchedulingState) -> Dict[str, Any]:
    """Validate parsed shifts against availability_draft for overlaps.

    - Conflict + retry_count == 0: increment retry_count, add conflict note
    - Conflict + retry_count >= 1: mark shifts as CONFLICT
    - No conflict: append consumed windows to availability_draft
    """
    shifts: List[ShiftAssignment] = list(state["current_parsed_shifts"])
    availability_draft: Dict[str, List[Dict[str, str]]] = dict(state["availability_draft"])
    retry_count: int = state["retry_count"]
    conflict_notes: str = state.get("conflict_notes", "")
    weekly_hours_draft: Dict[str, float] = dict(
        state.get("employee_weekly_hours_draft", {}) or {}
    )
    range_counts_draft: Dict[Any, int] = dict(
        state.get("range_counts_draft", {}) or {}
    )
    emp_by_id: Dict[str, Dict[str, Any]] = {
        str(e.get("id", "")): e for e in state.get("employees", [])
    }

    # Deep copy availability_draft entries
    for k, v in availability_draft.items():
        availability_draft[k] = list(v)

    conflicts_found: List[str] = []

    for shift in shifts:
        # Skip VACANT placeholders — they don't consume real availability
        if shift["status"] == "VACANT":
            continue

        emp_id = shift["employee_id"]
        consumed = availability_draft.get(emp_id, [])

        for window in consumed:
            if _windows_overlap(
                shift["start_time"], shift["end_time"],
                window["start"], window["end"],
            ):
                conflicts_found.append(
                    f"Employee {emp_id} has overlapping "
                    f"shift on {shift['date']} {shift['start_time']}-{shift['end_time']} "
                    f"with existing window {window['start']}-{window['end']}"
                )
                break

    if conflicts_found:
        if retry_count == 0:
            # First conflict: retry
            new_conflict_notes = conflict_notes + "\n".join(conflicts_found) + "\n"
            return {
                "retry_count": retry_count + 1,
                "conflict_notes": new_conflict_notes,
            }
        else:
            # Already retried: mark shifts as CONFLICT
            for shift in shifts:
                emp_id = shift["employee_id"]
                consumed = availability_draft.get(emp_id, [])
                for window in consumed:
                    if _windows_overlap(
                        shift["start_time"], shift["end_time"],
                        window["start"], window["end"],
                    ):
                        shift["status"] = "CONFLICT"
                        break

            # Still consume windows for non-conflict shifts
            _trim_cap_violations(
                shifts, state.get("employee_preferences", {}) or {}, range_counts_draft,
            )
            for shift in shifts:
                if shift["status"] == "ok":
                    emp_id = shift["employee_id"]
                    if emp_id not in availability_draft:
                        availability_draft[emp_id] = []
                    availability_draft[emp_id].append({
                        "start": shift["start_time"],
                        "end": shift["end_time"],
                    })
                    try:
                        st_iso = datetime.fromisoformat(shift["start_time"])
                        et_iso = datetime.fromisoformat(shift["end_time"])
                        duration_hrs = max(
                            (et_iso - st_iso).total_seconds() / 3600.0, 0.0
                        )
                        weekly_hours_draft[emp_id] = (
                            weekly_hours_draft.get(emp_id, 0.0) + duration_hrs
                        )
                    except (ValueError, TypeError):
                        pass
                    _grow_range_counts(shift, emp_by_id, range_counts_draft)

            return {
                "current_parsed_shifts": shifts,
                "availability_draft": availability_draft,
                "employee_weekly_hours_draft": weekly_hours_draft,
                "range_counts_draft": range_counts_draft,
            }
    else:
        # No conflicts: consume all windows (skip VACANT placeholders)
        _trim_cap_violations(
            shifts, state.get("employee_preferences", {}) or {}, range_counts_draft,
        )
        for shift in shifts:
            if shift["status"] == "VACANT":
                continue
            emp_id = shift["employee_id"]
            if emp_id not in availability_draft:
                availability_draft[emp_id] = []
            availability_draft[emp_id].append({
                "start": shift["start_time"],
                "end": shift["end_time"],
            })
            # Fold committed shift hours into the running weekly total so
            # subsequent locations respect per-employee max_hours_per_week.
            try:
                st_iso = datetime.fromisoformat(shift["start_time"])
                et_iso = datetime.fromisoformat(shift["end_time"])
                duration_hrs = max(
                    (et_iso - st_iso).total_seconds() / 3600.0, 0.0
                )
                weekly_hours_draft[emp_id] = (
                    weekly_hours_draft.get(emp_id, 0.0) + duration_hrs
                )
            except (ValueError, TypeError):
                pass
            _grow_range_counts(shift, emp_by_id, range_counts_draft)

        return {
            "current_parsed_shifts": shifts,
            "availability_draft": availability_draft,
            "employee_weekly_hours_draft": weekly_hours_draft,
            "range_counts_draft": range_counts_draft,
        }


def emit_result(state: SchedulingState) -> Dict[str, Any]:
    """Create a LocationResult and append it to draft_schedules."""
    location = state["current_location"]
    location_id = str(location.get("id", ""))
    location_name = str(location.get("name", ""))
    shifts = state["current_parsed_shifts"]

    # Determine status
    has_conflict = any(s["status"] == "CONFLICT" for s in shifts)
    has_parse_error = any(
        f"PARSE_ERROR for location {location_id}" in e for e in state["errors"]
    )

    if has_parse_error:
        status = "PARSE_ERROR"
    elif has_conflict:
        status = "CONFLICT"
    else:
        status = "ok"

    # Increment Prometheus scheduling counter
    try:
        from backend.middleware.metrics import SCHEDULING_RUNS
        SCHEDULING_RUNS.labels(status=status.lower()).inc()
    except Exception:
        pass  # metrics may not be available in tests

    # Collect location-specific errors
    location_errors = [
        e for e in state["errors"] if location_id in e
    ]

    # Accumulate failure entry for non-ok statuses
    new_failure_entries: List[Dict[str, Any]] = []
    if status in ("PARSE_ERROR", "CONFLICT"):
        new_failure_entries.append({
            "category": "SCHEDULING",
            "severity": "warning" if status == "CONFLICT" else "error",
            "source": f"scheduling.emit_result.{status.lower()}",
            "message": f"{status} for location {location_name} ({location_id})",
            "detail": {
                "location_id": location_id,
                "location_name": location_name,
                "status": status,
                "errors": location_errors,
            },
        })

    result: LocationResult = {
        "location_id": location_id,
        "location_name": location_name,
        "shifts": list(shifts),
        "errors": location_errors,
        "status": status,
    }

    draft_schedules = list(state["draft_schedules"])
    draft_schedules.append(result)

    completed = list(state["completed_location_ids"])
    completed.append(location_id)

    return {
        "draft_schedules": draft_schedules,
        "completed_location_ids": completed,
        "current_location_index": state["current_location_index"] + 1,
        "failure_entries": state.get("failure_entries", []) + new_failure_entries,
    }
