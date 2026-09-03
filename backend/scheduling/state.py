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
    # "ok" | "PARSE_ERROR" | "CONFLICT" | "QUOTA_EXCEEDED"
    # QUOTA_EXCEEDED: the free plan's per-location allowance for this
    # month is spent. Reported per location and skipped rather than
    # failing the run, so a tenant with one spent location and one
    # fresh one still gets the fresh one scheduled.
    status: str
    schedule_id: str   # UUID of the persisted ShiftSchedule row (set by the router)


class SchedulingState(TypedDict):
    company_id: str
    week_start_date: str
    locations: List[dict]
    # location_id -> fused ShiftTemplate dict (day-name keyed weekly_schedule).
    # Built by graph._load_initial_state using resolve_templates_for_week so
    # that specific-date override templates supplant the recurring template on
    # the matching calendar dates within the schedule window.
    shift_templates: dict
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
    # Running per-employee total hours committed across already-processed
    # locations in this graph run. Used to enforce per-employee
    # max_hours_per_week caps across multi-location schedules.
    employee_weekly_hours_draft: Dict[str, float]
    # Running count of committed shifts per (employee_id, range_start,
    # range_end) across already-processed locations in this graph run.
    # Used to enforce weight-1.0 hour_range_caps ("N times a week") across
    # multi-location schedules, the same way employee_weekly_hours_draft
    # enforces max_hours_per_week. Grown in validate_and_update_availability
    # from committed shifts, and seeded into local_schedule's range_counts.
    range_counts_draft: Dict[Any, int]
    # Per-employee scheduling preferences, keyed by employee_id:
    # {"day_preferences": [...], "hour_range_preferences": [...],
    # "hour_range_caps": [...]}. Same shape as the per-employee fields
    # embedded in each `employees` dict, duplicated here so
    # validate_and_update_availability can trim weight>=1.0 hour_range_cap
    # violations after AI generation without threading the whole employees
    # list through. Populated by _load_initial_state.
    employee_preferences: Dict[str, Dict[str, list]]
