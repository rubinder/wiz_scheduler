import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import anthropic

from backend.config import settings
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


def _filter_availability_against_draft(
    employee: Dict[str, Any],
    availability_draft: Dict[str, List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Filter an employee's available_windows by removing already-consumed windows
    that overlap with their availability.

    Returns a new list of available_windows with consumed time removed.
    """
    emp_id = str(employee.get("id", ""))
    windows: List[Dict[str, str]] = list(employee.get("available_windows", []))
    consumed: List[Dict[str, str]] = availability_draft.get(emp_id, [])

    if not consumed:
        return windows

    remaining: List[Dict[str, str]] = []
    for window in windows:
        w_start = datetime.fromisoformat(window["start"])
        w_end = datetime.fromisoformat(window["end"])

        has_full_overlap = False
        for c in consumed:
            c_start = datetime.fromisoformat(c["start"])
            c_end = datetime.fromisoformat(c["end"])
            # If the consumed window fully covers this availability window, skip it
            if c_start <= w_start and c_end >= w_end:
                has_full_overlap = True
                break

        if not has_full_overlap:
            remaining.append(window)

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
                    model="claude-sonnet-4-20250514",
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
        #    Both sides (shift and window) are tz-aware ISO strings. Convert
        #    both to the location's local timezone before HH:MM compare —
        #    otherwise UTC-stored avail windows won't match local-stored shift
        #    times even when they refer to the same wall-clock instant.
        if emp_id in emp_by_id and not issues:
            emp_windows = emp_by_id[emp_id].get("available_windows", [])
            if not emp_windows:
                issues.append(
                    f"employee {shift['employee_id']} has no availability set "
                    f"(cannot be scheduled {shift['start_time']}-{shift['end_time']})"
                )
            else:
                try:
                    from zoneinfo import ZoneInfo
                    loc_tz_name = location.get("timezone")
                    loc_tz = ZoneInfo(loc_tz_name) if loc_tz_name else None

                    def _local(dt: datetime) -> datetime:
                        if loc_tz is not None and dt.tzinfo is not None:
                            return dt.astimezone(loc_tz)
                        return dt

                    shift_start = _local(datetime.fromisoformat(shift["start_time"]))
                    shift_end = _local(datetime.fromisoformat(shift["end_time"]))
                    shift_date_str = shift_start.strftime("%Y-%m-%d")
                    shift_start_hm = shift_start.strftime("%H:%M")
                    shift_end_hm = shift_end.strftime("%H:%M")
                    covered = False
                    for w in emp_windows:
                        w_start = _local(datetime.fromisoformat(w["start"]))
                        w_end = _local(datetime.fromisoformat(w["end"]))
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

            return {
                "current_parsed_shifts": shifts,
                "availability_draft": availability_draft,
                "employee_weekly_hours_draft": weekly_hours_draft,
            }
    else:
        # No conflicts: consume all windows (skip VACANT placeholders)
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

        return {
            "current_parsed_shifts": shifts,
            "availability_draft": availability_draft,
            "employee_weekly_hours_draft": weekly_hours_draft,
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
