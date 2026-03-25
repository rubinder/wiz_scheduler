import json
import re
from datetime import datetime
from typing import Any, Dict, List

import anthropic

from backend.config import settings
from backend.scheduling.prompts import build_schedule_prompt
from backend.scheduling.state import LocationResult, SchedulingState, ShiftAssignment


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
    shift_template = state["shift_templates"].get(location_id, {})

    # Filter employees for this location
    location_employees = _filter_employees_for_location(
        state["employees"], location_id
    )

    # Filter each employee's availability against the draft
    for emp in location_employees:
        emp["available_windows"] = _filter_availability_against_draft(
            emp, state["availability_draft"]
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
    )
    return {"current_prompt": prompt}


async def call_llm(state: SchedulingState) -> Dict[str, Any]:
    """Invoke Anthropic Claude to generate the schedule."""
    location = state["current_location"]
    location_id = str(location.get("id", ""))

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16384,
            messages=[
                {"role": "user", "content": state["current_prompt"]},
            ],
        )

        raw_response = ""
        for block in message.content:
            if block.type == "text":
                raw_response += block.text

        input_tokens = message.usage.input_tokens if message.usage else 0
        output_tokens = message.usage.output_tokens if message.usage else 0

        return {
            "current_raw_response": raw_response,
            "total_input_tokens": state["total_input_tokens"] + input_tokens,
            "total_output_tokens": state["total_output_tokens"] + output_tokens,
        }
    except Exception as exc:
        error_msg = f"LLM_ERROR for location {location_id}: {exc}"
        return {
            "current_raw_response": "",
            "errors": state["errors"] + [error_msg],
        }


def _salvage_truncated_json(text: str) -> list | None:
    """Try to recover complete JSON objects from a truncated array.

    Finds the last complete object (ending with '}') and closes the array.
    """
    # Find the last complete object boundary
    last_brace = text.rfind("}")
    if last_brace == -1:
        return None
    truncated = text[: last_brace + 1].rstrip().rstrip(",") + "\n]"
    try:
        parsed = json.loads(truncated)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def parse_schedule(state: SchedulingState) -> Dict[str, Any]:
    """Extract JSON array of shifts from the LLM response. Never raises."""
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

    import logging
    logger = logging.getLogger(__name__)

    # Strip markdown code fences if present
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        # Try direct JSON parse first
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract a JSON array from the response using regex
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                # JSON may be truncated — try to salvage complete objects
                parsed = _salvage_truncated_json(match.group(0))
                if parsed is None:
                    return {
                        "current_parsed_shifts": [],
                        "errors": state["errors"]
                        + [f"PARSE_ERROR for location {location_id}: could not extract JSON from LLM response"],
                    }
        else:
            # No closing ']' found — response is likely truncated
            match_open = re.search(r"\[.*", cleaned, re.DOTALL)
            if match_open:
                parsed = _salvage_truncated_json(match_open.group(0))
                if parsed is not None:
                    logger.warning(
                        "Salvaged %d shifts from truncated LLM response for location %s",
                        len(parsed), location_id,
                    )
                else:
                    return {
                        "current_parsed_shifts": [],
                        "errors": state["errors"]
                        + [f"PARSE_ERROR for location {location_id}: no JSON array found in LLM response"],
                    }
            else:
                return {
                    "current_parsed_shifts": [],
                    "errors": state["errors"]
                    + [f"PARSE_ERROR for location {location_id}: no JSON array found in LLM response"],
                }

    if not isinstance(parsed, list):
        return {
            "current_parsed_shifts": [],
            "errors": state["errors"]
            + [f"PARSE_ERROR for location {location_id}: LLM response is not a JSON array"],
        }

    shifts: List[ShiftAssignment] = []
    for item in parsed:
        role_name = item.get("role_name", "")
        role_id = role_name_to_id.get(role_name, "")

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

    return {"current_parsed_shifts": shifts}


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

    # Deep copy availability_draft entries
    for k, v in availability_draft.items():
        availability_draft[k] = list(v)

    conflicts_found: List[str] = []

    for shift in shifts:
        emp_id = shift["employee_id"]
        consumed = availability_draft.get(emp_id, [])

        for window in consumed:
            if _windows_overlap(
                shift["start_time"], shift["end_time"],
                window["start"], window["end"],
            ):
                conflicts_found.append(
                    f"Employee {shift['employee_name']} ({emp_id}) has overlapping "
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

            return {
                "current_parsed_shifts": shifts,
                "availability_draft": availability_draft,
            }
    else:
        # No conflicts: consume all windows
        for shift in shifts:
            emp_id = shift["employee_id"]
            if emp_id not in availability_draft:
                availability_draft[emp_id] = []
            availability_draft[emp_id].append({
                "start": shift["start_time"],
                "end": shift["end_time"],
            })

        return {
            "current_parsed_shifts": shifts,
            "availability_draft": availability_draft,
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

    # Collect location-specific errors
    location_errors = [
        e for e in state["errors"] if location_id in e
    ]

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
    }
