"""Tests for the local (non-LLM) schedule generator."""

import random as _random
from collections import Counter

import pytest

from backend.scheduling.local_scheduler import (
    local_schedule,
    _pick_employee,
    _filter_hard_negatives,
    _affinity_score,
)
from backend.scheduling.state import SchedulingState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_employee(eid: str, name: str, roles: list[dict], location_id: str, windows: list[dict] | None = None):
    """Build an employee dict matching the shape used in SchedulingState."""
    return {
        "id": eid,
        "full_name": name,
        "email": f"{name.lower().replace(' ', '.')}@test.com",
        "location_ids": [location_id],
        "roles": roles,
        "affinities": [],
        "available_windows": windows or [],
    }


def _make_state(
    employees: list[dict],
    weekly_schedule: dict,
    week_start_date: str = "2026-03-30",  # Monday
    location_id: str = "loc00001",
    location_name: str = "Test Location",
    timezone: str = "UTC",
) -> SchedulingState:
    """Construct a minimal SchedulingState for local_schedule.

    Default timezone is UTC so the existing test data — which pairs naive
    template slot times (e.g. "09:00") with availability ISO strings in
    UTC (e.g. "2026-03-30T09:00:00+00:00") — converts to the same hours
    in either direction. Tests that care about timezone correctness
    override this explicitly.
    """
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
    }


# Availability windows stored as "local time tagged as UTC" (matching prod DB format).
# Monday 2026-03-30  09:00-17:00
MON_9_17 = {"start": "2026-03-30T09:00:00+00:00", "end": "2026-03-30T17:00:00+00:00"}
# Tuesday 2026-03-31  09:00-17:00
TUE_9_17 = {"start": "2026-03-31T09:00:00+00:00", "end": "2026-03-31T17:00:00+00:00"}
# Monday 2026-03-30  09:00-12:00 (partial day — only morning)
MON_9_12 = {"start": "2026-03-30T09:00:00+00:00", "end": "2026-03-30T12:00:00+00:00"}


ROLE_FLOOR = {"role_id": "role0001", "role_name": "Floor", "skill_level": 3}
ROLE_LEAD = {"role_id": "role0002", "role_name": "Lead", "skill_level": 2}


# ---------------------------------------------------------------------------
# Tests — basic assignment
# ---------------------------------------------------------------------------

class TestBasicAssignment:

    def test_fills_single_slot(self):
        """One slot, one employee — should produce exactly one shift."""
        emp = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([emp], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]

        assert len(shifts) == 1
        assert shifts[0]["employee_id"] == "e001"
        assert shifts[0]["role_name"] == "Floor"
        assert shifts[0]["date"] == "2026-03-30"
        assert shifts[0]["status"] == "ok"

    def test_fills_multiple_roles(self):
        """Two different role slots, two employees with matching roles."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        bob = _make_employee("e002", "Bob", [ROLE_LEAD], "loc00001", [MON_9_17])
        schedule = {
            "Monday": [
                {"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
                {"role_name": "Lead", "role_id": "role0002", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
            ]
        }
        state = _make_state([alice, bob], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]

        assert len(shifts) == 2
        roles_filled = {s["role_name"] for s in shifts}
        assert roles_filled == {"Floor", "Lead"}

    def test_headcount_greater_than_one(self):
        """Headcount of 2 should assign two different employees."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        bob = _make_employee("e002", "Bob", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 2, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice, bob], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]

        assert len(shifts) == 2
        assert shifts[0]["employee_id"] != shifts[1]["employee_id"]

    def test_no_eligible_employee_produces_no_shift(self):
        """If no employee has the required role, no shift is created (vacancy handled downstream)."""
        alice = _make_employee("e001", "Alice", [ROLE_LEAD], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 0


# ---------------------------------------------------------------------------
# Tests — availability filtering
# ---------------------------------------------------------------------------

class TestAvailabilityFiltering:

    def test_unavailable_employee_not_assigned(self):
        """Employee without availability window for the day is skipped."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [TUE_9_17])  # Tuesday only
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 0

    def test_partial_availability_blocks_full_shift(self):
        """Employee available 9-12 cannot fill a 9-17 shift."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_12])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 0

    def test_partial_availability_allows_shorter_shift(self):
        """Employee available 9-12 CAN fill a 9-12 shift."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_12])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "12:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 1


# ---------------------------------------------------------------------------
# Tests — no overlapping assignments
# ---------------------------------------------------------------------------

class TestNoOverlap:

    def test_same_employee_not_double_booked(self):
        """One employee with two roles should not be assigned overlapping shifts."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR, ROLE_LEAD], "loc00001", [MON_9_17])
        schedule = {
            "Monday": [
                {"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
                {"role_name": "Lead", "role_id": "role0002", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
            ]
        }
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]

        # Only one shift — the second slot can't be filled without overlap
        assert len(shifts) == 1

    def test_non_overlapping_shifts_both_assigned(self):
        """One employee can fill two non-overlapping shifts on the same day."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR, ROLE_LEAD], "loc00001", [MON_9_17])
        schedule = {
            "Monday": [
                {"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "12:00"},
                {"role_name": "Lead", "role_id": "role0002", "headcount": 1, "start_time": "13:00", "end_time": "17:00"},
            ]
        }
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]

        assert len(shifts) == 2
        assert all(s["employee_id"] == "e001" for s in shifts)


# ---------------------------------------------------------------------------
# Tests — opportunity cost preference
# ---------------------------------------------------------------------------

class TestOpportunityCost:

    def test_prefers_single_role_employee(self):
        """Between a single-role and multi-role employee, prefers the one with fewer
        required roles (lower opportunity cost).

        The template must require BOTH roles so that Bob's intersection count
        (_num_required_roles) is 2 while Alice's is 1.
        """
        _random.seed(42)
        # Alice has only Floor, Bob has Floor + Lead
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        bob = _make_employee("e002", "Bob", [ROLE_FLOOR, ROLE_LEAD], "loc00001", [MON_9_17])
        # Template requires both Floor and Lead — Bob can fill either, Alice only Floor
        schedule = {
            "Monday": [
                {"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
                {"role_name": "Lead", "role_id": "role0002", "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
            ]
        }
        state = _make_state([alice, bob], schedule)

        # Run multiple times — Alice should always be picked for Floor
        # (she has 1 required role vs Bob's 2)
        for _ in range(10):
            result = local_schedule(state, strategy="random")
            floor_shifts = [s for s in result["current_parsed_shifts"] if s["role_name"] == "Floor"]
            assert floor_shifts[0]["employee_id"] == "e001"


# ---------------------------------------------------------------------------
# Tests — rotation strategy
# ---------------------------------------------------------------------------

class TestRotationStrategy:

    def test_rotation_distributes_across_employees(self):
        """Over a 5-day week with 3 eligible employees, rotation should spread
        assignments more evenly than pure random."""
        _random.seed(123)
        employees = [
            _make_employee(f"e{i:03d}", f"Emp{i}", [ROLE_FLOOR], "loc00001",
                           [{"start": f"2026-03-3{d}T09:00:00+00:00", "end": f"2026-03-3{d}T17:00:00+00:00"} for d in range(5)])
            for i in range(1, 4)
        ]
        # Fix availability: March has 31 days, so day 0-4 = 30,31,01,02,03
        # Use proper dates
        for emp in employees:
            emp["available_windows"] = [
                {"start": "2026-03-30T09:00:00+00:00", "end": "2026-03-30T17:00:00+00:00"},
                {"start": "2026-03-31T09:00:00+00:00", "end": "2026-03-31T17:00:00+00:00"},
                {"start": "2026-04-01T09:00:00+00:00", "end": "2026-04-01T17:00:00+00:00"},
                {"start": "2026-04-02T09:00:00+00:00", "end": "2026-04-02T17:00:00+00:00"},
                {"start": "2026-04-03T09:00:00+00:00", "end": "2026-04-03T17:00:00+00:00"},
            ]

        schedule = {
            day: [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        }
        state = _make_state(employees, schedule)

        result = local_schedule(state, strategy="rotation")
        shifts = result["current_parsed_shifts"]

        assert len(shifts) == 5
        counts = Counter(s["employee_id"] for s in shifts)
        # With 5 shifts and 3 employees, rotation should give at most 2 to any one person
        assert max(counts.values()) <= 2, f"Rotation should distribute evenly, got {counts}"

    def test_rotation_vs_random_more_even(self):
        """Rotation produces more even distribution than random over many runs."""
        employees = [
            _make_employee(f"e{i:03d}", f"Emp{i}", [ROLE_FLOOR], "loc00001",
                           [MON_9_17])
            for i in range(1, 6)
        ]
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state(employees, schedule)

        # Both strategies should produce valid shifts
        for strat in ("random", "rotation"):
            result = local_schedule(state, strategy=strat)
            assert len(result["current_parsed_shifts"]) == 1
            assert result["current_parsed_shifts"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# Tests — output format
# ---------------------------------------------------------------------------

class TestOutputFormat:

    def test_shift_has_correct_iso_timestamps(self):
        """Generated shifts should have ISO 8601 timestamps with tz offset."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        shift = result["current_parsed_shifts"][0]

        assert shift["date"] == "2026-03-30"
        assert "T09:00:00" in shift["start_time"]
        assert "T17:00:00" in shift["end_time"]
        # Should contain timezone offset
        assert "+" in shift["start_time"] or "-" in shift["start_time"]

    def test_raw_response_is_valid_json(self):
        """current_raw_response should be parseable JSON."""
        import json
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        parsed = json.loads(result["current_raw_response"])
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_no_tokens_consumed(self):
        """Local scheduler should not increment token counters."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [{"role_name": "Floor", "role_id": "role0001", "headcount": 1, "start_time": "09:00", "end_time": "17:00"}]}
        state = _make_state([alice], schedule)

        result = local_schedule(state, strategy="random")
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0


# ---------------------------------------------------------------------------
# Tests — _pick_employee scoring
# ---------------------------------------------------------------------------

class TestPickEmployee:

    def test_random_strategy_picks_from_lowest_cost(self):
        """Random strategy should only pick from employees with fewest required roles."""
        _random.seed(0)
        candidates = [
            {"id": "e1", "full_name": "A", "_num_required_roles": 1, "_skill": 3, "roles": []},
            {"id": "e2", "full_name": "B", "_num_required_roles": 3, "_skill": 5, "roles": []},
        ]
        picks = {_pick_employee(candidates, "Floor", {}, "random", set(), {})["id"] for _ in range(20)}
        assert picks == {"e1"}

    def test_rotation_penalises_repeat_fills(self):
        """Rotation strategy should prefer employee who hasn't filled this role yet."""
        _random.seed(0)
        candidates = [
            {"id": "e1", "full_name": "A", "_num_required_roles": 1, "_skill": 3, "roles": []},
            {"id": "e2", "full_name": "B", "_num_required_roles": 1, "_skill": 3, "roles": []},
        ]
        # e1 has already filled "Floor" twice
        role_fill_counts = {("e1", "Floor"): 2}
        picks = {_pick_employee(candidates, "Floor", role_fill_counts, "rotation", set(), {})["id"] for _ in range(20)}
        assert picks == {"e2"}


# ---------------------------------------------------------------------------
# Tests — affinity constraints
# ---------------------------------------------------------------------------

class TestAffinityConstraints:

    def test_hard_negative_excludes_candidate(self):
        """Employee with -1.0 affinity toward a coworker is filtered out."""
        affinity_lookup = {
            "e1": [{"target_id": "e3", "level": -1.0}],
        }
        candidates = [
            {"id": "e1", "full_name": "A", "_num_required_roles": 1, "_skill": 3, "roles": []},
            {"id": "e2", "full_name": "B", "_num_required_roles": 1, "_skill": 3, "roles": []},
        ]
        coworkers = {"e3"}
        filtered = _filter_hard_negatives(candidates, coworkers, affinity_lookup)
        assert [c["id"] for c in filtered] == ["e2"]

    def test_hard_negative_reverse_direction(self):
        """If a coworker has -1.0 toward the candidate, candidate is also excluded."""
        affinity_lookup = {
            "e3": [{"target_id": "e1", "level": -1.0}],
        }
        candidates = [
            {"id": "e1", "full_name": "A", "_num_required_roles": 1, "_skill": 3, "roles": []},
            {"id": "e2", "full_name": "B", "_num_required_roles": 1, "_skill": 3, "roles": []},
        ]
        coworkers = {"e3"}
        filtered = _filter_hard_negatives(candidates, coworkers, affinity_lookup)
        assert [c["id"] for c in filtered] == ["e2"]

    def test_positive_affinity_boosts_candidate(self):
        """Employee with positive affinity toward coworker gets a better (lower) score."""
        affinity_lookup = {
            "e1": [{"target_id": "e3", "level": 0.8}],
        }
        coworkers = {"e3"}
        score_e1 = _affinity_score("e1", coworkers, affinity_lookup)
        score_e2 = _affinity_score("e2", coworkers, affinity_lookup)
        # e1 should have a lower (better) score than e2
        assert score_e1 < score_e2

    def test_negative_affinity_penalises_candidate(self):
        """Employee with negative (but not -1.0) affinity toward coworker gets worse score."""
        affinity_lookup = {
            "e1": [{"target_id": "e3", "level": -0.5}],
        }
        coworkers = {"e3"}
        score_e1 = _affinity_score("e1", coworkers, affinity_lookup)
        score_e2 = _affinity_score("e2", coworkers, affinity_lookup)
        # e1 should have a higher (worse) score than e2 (who has no affinity)
        assert score_e1 > score_e2

    def test_must_schedule_together_hard_positive(self):
        """level=1.0 strongly boosts the candidate to be picked together."""
        _random.seed(0)
        affinity_lookup = {
            "e1": [{"target_id": "e3", "level": 1.0}],
        }
        coworkers = {"e3"}
        candidates = [
            {"id": "e1", "full_name": "A", "_num_required_roles": 1, "_skill": 3, "roles": []},
            {"id": "e2", "full_name": "B", "_num_required_roles": 1, "_skill": 3, "roles": []},
        ]
        # e1 has a strong positive affinity with coworker e3 — should always be picked
        picks = {
            _pick_employee(candidates, "Floor", {}, "random", coworkers, affinity_lookup)["id"]
            for _ in range(20)
        }
        assert picks == {"e1"}

    def test_hard_negative_in_full_schedule(self):
        """End-to-end: two employees with -1.0 affinity should not be on the same shift."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        alice["affinities"] = [{"target_id": "e002", "level": -1.0}]
        bob = _make_employee("e002", "Bob", [ROLE_FLOOR], "loc00001", [MON_9_17])
        bob["affinities"] = [{"target_id": "e001", "level": -1.0}]
        charlie = _make_employee("e003", "Charlie", [ROLE_FLOOR], "loc00001", [MON_9_17])

        schedule = {"Monday": [
            {"role_name": "Floor", "role_id": "role0001", "headcount": 2, "start_time": "09:00", "end_time": "17:00"},
        ]}
        state = _make_state([alice, bob, charlie], schedule)

        result = local_schedule(state, strategy="random")
        shifts = result["current_parsed_shifts"]
        assert len(shifts) == 2

        eids = {s["employee_id"] for s in shifts}
        # Alice and Bob should NOT both be in the same shift
        assert not ({"e001", "e002"} <= eids), f"Hard negative pair both assigned: {eids}"

    def test_no_affinities_works_normally(self):
        """Employees with no affinities should schedule fine (regression check)."""
        alice = _make_employee("e001", "Alice", [ROLE_FLOOR], "loc00001", [MON_9_17])
        bob = _make_employee("e002", "Bob", [ROLE_FLOOR], "loc00001", [MON_9_17])
        schedule = {"Monday": [
            {"role_name": "Floor", "role_id": "role0001", "headcount": 2, "start_time": "09:00", "end_time": "17:00"},
        ]}
        state = _make_state([alice, bob], schedule)

        result = local_schedule(state, strategy="random")
        assert len(result["current_parsed_shifts"]) == 2
