from typing import Any, Dict, List, TypedDict


class FailureEntry(TypedDict):
    category: str
    severity: str
    source: str
    message: str
    detail: Dict[str, Any]


class ShiftAssignment(TypedDict):
    employee_id: str
    employee_name: str
    role_id: str
    role_name: str
    location_id: str
    date: str          # "YYYY-MM-DD"
    start_time: str    # ISO 8601 with tz offset
    end_time: str      # ISO 8601 with tz offset
    status: str        # "ok" | "CONFLICT" | "VACANT"


class LocationResult(TypedDict, total=False):
    location_id: str
    location_name: str
    shifts: List[ShiftAssignment]
    errors: List[str]
    status: str        # "ok" | "PARSE_ERROR" | "CONFLICT"
    schedule_id: str   # UUID of the persisted ShiftSchedule row (set by the router)


class SchedulingState(TypedDict):
    company_id: str
    week_start_date: str
    locations: List[dict]
    shift_templates: dict        # keyed by location_id
    employees: List[dict]
    availability_draft: dict     # employee_id -> list of consumed {"start": str, "end": str}
    current_location_index: int
    completed_location_ids: List[str]
    retry_count: int
    draft_schedules: List[LocationResult]
    errors: List[str]
    current_prompt: str
    current_raw_response: str
    current_parsed_shifts: List[ShiftAssignment]
    conflict_notes: str
    # Token usage accumulators across all LLM calls
    total_input_tokens: int
    total_output_tokens: int
    # Intermediate per-location context set by load_location_context
    current_location: dict
    current_shift_template: dict
    current_employees: List[dict]
    # Accumulated failure entries to be persisted by the router
    failure_entries: List[FailureEntry]
    # Condensed role equivalents: role_id -> list of equivalent role_ids
    role_equivalents: Dict[str, List[str]]
    # Number of days to schedule (default 7)
    num_days: int
