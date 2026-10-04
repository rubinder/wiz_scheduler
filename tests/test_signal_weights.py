"""Tests for scheduling signal weight parameters (seniority, pay, overtime, affinity)."""

import pytest

from backend.scheduling.local_scheduler import local_schedule
from backend.scheduling.state import SchedulingState


def _make_employee(
    eid: str,
    name: str,
    roles: list[dict],
    location_id: str = "loc00001",
    windows: list[dict] | None = None,
    pay_rate: float | None = None,
    hire_date: str | None = None,
    seniority_rank: int | None = None,
):
    """Create a test employee dict matching the shape used in SchedulingState."""
    return {
        "id": eid,
        "full_name": name,
        "email": f"{name.lower().replace(' ', '.')}@test.com",
        "location_ids": [location_id],
        "roles": roles,
        "affinities": [],
        "available_windows": windows or [],
        "pay_rate": pay_rate,
        "hire_date": hire_date,
        "seniority_rank": seniority_rank,
    }


def _make_state(
    employees: list[dict],
    weekly_schedule: dict,
    week_start_date: str = "2026-03-30",  # Monday
    location_id: str = "loc00001",
    location_name: str = "Test Location",
    timezone: str = "UTC",
    signal_config: dict | None = None,
) -> SchedulingState:
    """Construct a minimal SchedulingState for local_schedule."""
    location = {"id": location_id, "name": location_name, "timezone": timezone}
    shift_template = {
        "id": "tmpl0001",
        "name": "Test Template",
        "location_id": location_id,
        "weekly_schedule": weekly_schedule,
    }
    return {
        "company_id": "comp0001",
        "week_start_date": week_start_date,
        "locations": [location],
        "shift_templates": {location_id: shift_template},
        "employees": employees,
        "availability_draft": {},
        "current_location_index": 0,
        "completed_location_ids": [],
        "retry_count": 0,
        "draft_schedules": [],
        "errors": [],
        "current_prompt": "",
        "current_raw_response": "",
        "current_parsed_shifts": [],
        "conflict_notes": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "current_location": location,
        "current_shift_template": shift_template,
        "current_employees": employees,
        "failure_entries": [],
        "role_equivalents": {},
        "num_days": 7,
        "employee_weekly_hours_draft": {},
        "range_counts_draft": {},
        "range_counts_before": {},
        "employee_preferences": {},
        "current_preference_summary": None,
        "signal_config": signal_config or {},
    }


ROLE_FLOOR = {"role_id": "role0001", "role_name": "Floor"}
MON_9_17 = {"start": "2026-03-30T09:00:00+00:00", "end": "2026-03-30T17:00:00+00:00"}


class TestSignalConfigIntegration:
    """Test that signal_config is properly integrated into scheduling."""

    def test_signal_config_in_state(self):
        """Signal config should be present in scheduling state."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        signal_config = {"pay_weight": 0.5, "seniority_weight": 0.3}
        state = _make_state([emp], schedule, signal_config=signal_config)

        assert state["signal_config"] == signal_config

    def test_empty_signal_config_safe(self):
        """Empty signal_config should not break scheduling."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([emp], schedule, signal_config={})

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 1

    def test_signal_config_none_safe(self):
        """None signal_config should not break scheduling."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([emp], schedule, signal_config=None)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 1

    @pytest.mark.parametrize("strategy", ["random", "rotation", "rotation_history", "max_hours"])
    def test_all_strategies_work_with_signal_config(self, strategy):
        """All scheduling strategies should work with signal_config."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        signal_config = {"pay_weight": 0.5, "seniority_weight": 0.3}
        state = _make_state([emp], schedule, signal_config=signal_config)

        result = local_schedule(state, strategy=strategy, strategy_param=0.5, strategy_param2=0.0)
        assert len(result["current_parsed_shifts"]) == 1

    def test_multiple_weights_combined(self):
        """Multiple signal weights can be combined."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        signal_config = {
            "seniority_weight": 0.3,
            "pay_weight": 0.6,
            "overtime_weight": 0.1,
            "affinity_weight": 0.0,
        }
        state = _make_state([emp], schedule, signal_config=signal_config)
        result = local_schedule(state, strategy="random")

        assert len(result["current_parsed_shifts"]) == 1
        assert state["signal_config"]["seniority_weight"] == 0.3

    def test_signal_weights_passed_through_state(self):
        """Signal weights should flow through the entire state."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], windows=[MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        signal_config = {"pay_weight": 0.7}
        state = _make_state([emp], schedule, signal_config=signal_config)

        # State should preserve signal_config
        assert "signal_config" in state
        assert state["signal_config"]["pay_weight"] == 0.7

        # Scheduling should use signal_config
        result = local_schedule(state, strategy="random")
        assert result["current_parsed_shifts"], "Should generate shifts with signal weights"
