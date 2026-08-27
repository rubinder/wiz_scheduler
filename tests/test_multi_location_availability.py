"""Tests for employee availability across multiple locations.

Verifies that when an employee is assigned to multiple locations, the
scheduling pipeline prevents double-booking by tracking consumed windows
in availability_draft.
"""

import importlib
import sys
import types
import uuid
from copy import deepcopy
from typing import Any, Dict, List

import pytest

# Prevent backend.scheduling.__init__ from importing graph.py (which
# pulls in SQLAlchemy models that are incompatible with Python 3.13).
# We stub the package so that nodes.py and state.py can be imported directly.
_pkg = types.ModuleType("backend.scheduling")
_pkg.__path__ = [__import__("backend").__path__[0] + "/scheduling"]
_pkg.__package__ = "backend.scheduling"
sys.modules.setdefault("backend.scheduling", _pkg)

from backend.scheduling.state import LocationResult, SchedulingState, ShiftAssignment  # noqa: E402
from backend.scheduling.nodes import (  # noqa: E402
    _filter_availability_against_draft,
    _filter_employees_for_location,
    _windows_overlap,
    load_location_context,
    validate_and_update_availability,
    emit_result,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------

_COMPANY_ID = str(uuid.uuid4())
_LOCATION_A_ID = str(uuid.uuid4())
_LOCATION_B_ID = str(uuid.uuid4())
_LOCATION_C_ID = str(uuid.uuid4())
_EMP_SHARED_ID = str(uuid.uuid4())  # Works at both A and B
_EMP_A_ONLY_ID = str(uuid.uuid4())  # Works at A only
_EMP_B_ONLY_ID = str(uuid.uuid4())  # Works at B only
_ROLE_ID = str(uuid.uuid4())

_MONDAY = "2026-03-23"
_TUESDAY = "2026-03-24"
_TZ = "-05:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_shift(
    employee_id: str,
    employee_name: str,
    location_id: str,
    date: str = _MONDAY,
    start_hour: int = 9,
    end_hour: int = 17,
) -> ShiftAssignment:
    return {
        "employee_id": employee_id,
        "employee_name": employee_name,
        "role_id": _ROLE_ID,
        "role_name": "Barista",
        "location_id": location_id,
        "date": date,
        "start_time": f"{date}T{start_hour:02d}:00:00{_TZ}",
        "end_time": f"{date}T{end_hour:02d}:00:00{_TZ}",
        "status": "ok",
    }


def _make_employee(
    emp_id: str,
    name: str,
    location_ids: List[str],
    windows: List[Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    if windows is None:
        windows = [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    return {
        "id": emp_id,
        "full_name": name,
        "location_ids": location_ids,
        "roles": [{"role_name": "Barista", "role_id": _ROLE_ID, "skill_level": 3}],
        "affinities": [],
        "available_windows": windows,
    }


def _make_template(location_id: str) -> dict:
    return {
        "weekly_schedule": {
            "Monday": [
                {
                    "day": "Monday",
                    "role_name": "Barista",
                    "role_id": _ROLE_ID,
                    "headcount": 1,
                    "start_time": "09:00",
                    "end_time": "17:00",
                }
            ]
        }
    }


def _build_state(
    location_ids: List[str],
    employees: List[Dict[str, Any]],
    availability_draft: dict | None = None,
    current_location_index: int = 0,
) -> SchedulingState:
    return SchedulingState(
        company_id=_COMPANY_ID,
        week_start_date=_MONDAY,
        locations=[
            {"id": lid, "name": f"Location-{i}", "timezone": "America/New_York"}
            for i, lid in enumerate(location_ids)
        ],
        shift_templates={lid: _make_template(lid) for lid in location_ids},
        employees=deepcopy(employees),
        availability_draft=availability_draft or {},
        current_location_index=current_location_index,
        completed_location_ids=[],
        retry_count=0,
        draft_schedules=[],
        errors=[],
        current_prompt="",
        current_raw_response="",
        current_parsed_shifts=[],
        conflict_notes="",
        total_input_tokens=0,
        total_output_tokens=0,
        current_location={},
        current_shift_template={},
        current_employees=[],
    )


# ---------------------------------------------------------------------------
# Tests: _filter_availability_against_draft
# ---------------------------------------------------------------------------


async def test_fully_consumed_window_removed():
    """An availability window fully covered by a consumed window is removed."""
    emp = _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID])
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert remaining == [], "Fully consumed window should be removed"


async def test_partial_overlap_is_carved_out_not_kept_whole(): 
    """Consumed time is subtracted, leaving the unused remainder on each side.

    This previously asserted the window was kept WHOLE, which is what allowed
    the same hours to be booked again at another location (#85). Keeping the
    employee available is right; keeping the consumed hours available is not.
    """
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T08:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}],
    )
    # Worked 09:00-13:00; 08:00-09:00 and 13:00-17:00 remain free.
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T13:00:00{_TZ}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 2, "A mid-window booking splits the window in two"
    assert "T08:00:00" in remaining[0]["start"] and "T09:00:00" in remaining[0]["end"]
    assert "T13:00:00" in remaining[1]["start"] and "T17:00:00" in remaining[1]["end"]


async def test_no_consumed_windows_all_kept():
    """When nothing is consumed yet, all windows are returned."""
    emp = _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID])
    remaining = _filter_availability_against_draft(emp, {})
    assert len(remaining) == 1


async def test_multiple_windows_selective_removal():
    """Only the consumed windows are removed, others are kept."""
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T13:00:00{_TZ}"},
            {"start": f"{_MONDAY}T14:00:00{_TZ}", "end": f"{_MONDAY}T18:00:00{_TZ}"},
        ],
    )
    # Consume only the morning window
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T13:00:00{_TZ}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 1
    assert "T14:00:00" in remaining[0]["start"]


# ---------------------------------------------------------------------------
# Tests: load_location_context with shared employees
# ---------------------------------------------------------------------------


async def test_shared_employee_available_at_second_location_before_scheduling():
    """Before any shifts are scheduled, a shared employee is available at both locations."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
        _make_employee(_EMP_A_ONLY_ID, "Bob", [_LOCATION_A_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Load context for location B
    state["current_location_index"] = 1
    result = load_location_context(state)

    emp_ids = [e["id"] for e in result["current_employees"]]
    assert _EMP_SHARED_ID in emp_ids, "Shared employee should appear at location B"
    assert _EMP_A_ONLY_ID not in emp_ids, "A-only employee should not appear at location B"

    # Alice should still have her availability window
    alice = next(e for e in result["current_employees"] if e["id"] == _EMP_SHARED_ID)
    assert len(alice["available_windows"]) == 1


async def test_shared_employee_availability_removed_after_first_location():
    """After being scheduled at location A, the shared employee's window
    should be removed when loading context for location B."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
        _make_employee(_EMP_B_ONLY_ID, "Carol", [_LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Simulate: Alice was scheduled at location A (9am-5pm Monday)
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    }
    state["current_location_index"] = 1  # Now processing location B

    result = load_location_context(state)

    alice = next(e for e in result["current_employees"] if e["id"] == _EMP_SHARED_ID)
    assert alice["available_windows"] == [], (
        "Alice's Monday window should be fully consumed after location A"
    )

    # Carol (B-only) should still have full availability
    carol = next(e for e in result["current_employees"] if e["id"] == _EMP_B_ONLY_ID)
    assert len(carol["available_windows"]) == 1


async def test_shared_employee_partial_day_keeps_only_the_unused_hours():
    """A partly-used window returns its unused remainder, not the whole window."""
    employees = [
        _make_employee(
            _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID],
            windows=[{"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T22:00:00{_TZ}"}],
        ),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Alice worked 9-13 at location A (only part of her 6am-10pm window)
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T13:00:00{_TZ}"},
        ]
    }
    state["current_location_index"] = 1

    result = load_location_context(state)
    alice = next(e for e in result["current_employees"] if e["id"] == _EMP_SHARED_ID)
    # 06:00-22:00 minus the 09:00-13:00 worked at A -> 06:00-09:00 and 13:00-22:00
    assert len(alice["available_windows"]) == 2
    assert "T09:00:00" in alice["available_windows"][0]["end"]
    assert "T13:00:00" in alice["available_windows"][1]["start"]


# ---------------------------------------------------------------------------
# Tests: validate_and_update_availability across locations
# ---------------------------------------------------------------------------


async def test_validate_detects_conflict_for_shared_employee():
    """If the LLM assigns a shared employee at location B during an already-consumed
    window from location A, validate_and_update_availability detects the conflict."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Alice was already scheduled at location A
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    }

    # LLM incorrectly assigns Alice at location B for the same time
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID),
    ]
    state["retry_count"] = 0

    result = validate_and_update_availability(state)

    # First conflict triggers a retry
    assert result["retry_count"] == 1
    assert _EMP_SHARED_ID in result["conflict_notes"]


async def test_conflict_marked_after_retry_exhausted():
    """After retry is exhausted, conflicting shifts are marked CONFLICT."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    }
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID),
    ]
    state["retry_count"] = 1  # Already retried once

    result = validate_and_update_availability(state)

    assert result["current_parsed_shifts"][0]["status"] == "CONFLICT"


async def test_no_conflict_different_days():
    """A shared employee working at location A on Monday and location B on Tuesday
    should have no conflicts."""
    employees = [
        _make_employee(
            _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID],
            windows=[
                {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
                {"start": f"{_TUESDAY}T09:00:00{_TZ}", "end": f"{_TUESDAY}T17:00:00{_TZ}"},
            ],
        ),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Monday consumed at location A
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
        ]
    }
    # Tuesday shift at location B
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID, date=_TUESDAY),
    ]
    state["retry_count"] = 0

    result = validate_and_update_availability(state)

    # No conflict — Tuesday is a different day
    assert "retry_count" not in result or result.get("retry_count", 0) == 0
    shifts = result["current_parsed_shifts"]
    assert all(s["status"] == "ok" for s in shifts)

    # Tuesday window should now be consumed in the draft
    assert _EMP_SHARED_ID in result["availability_draft"]
    consumed = result["availability_draft"][_EMP_SHARED_ID]
    assert len(consumed) == 2  # Monday from loc A + Tuesday from loc B


async def test_no_conflict_non_overlapping_hours_same_day():
    """A shared employee working morning at location A and evening at location B
    on the same day should have no conflicts."""
    employees = [
        _make_employee(
            _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID],
            windows=[
                {"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T22:00:00{_TZ}"},
            ],
        ),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Morning consumed at location A
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T12:00:00{_TZ}"},
        ]
    }
    # Evening shift at location B
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID, start_hour=14, end_hour=20),
    ]
    state["retry_count"] = 0

    result = validate_and_update_availability(state)

    shifts = result["current_parsed_shifts"]
    assert all(s["status"] == "ok" for s in shifts)
    consumed = result["availability_draft"][_EMP_SHARED_ID]
    assert len(consumed) == 2


async def test_overlapping_hours_detected_even_partial():
    """Even a 1-hour overlap across locations is detected as a conflict."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # Location A: 9am-1pm
    state["availability_draft"] = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T13:00:00{_TZ}"},
        ]
    }
    # Location B: 12pm-5pm — overlaps by 1 hour (12-1pm)
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID, start_hour=12, end_hour=17),
    ]
    state["retry_count"] = 0

    result = validate_and_update_availability(state)
    assert result["retry_count"] == 1, "Partial overlap should trigger conflict"


# ---------------------------------------------------------------------------
# Tests: Full two-location pipeline flow (node-by-node)
# ---------------------------------------------------------------------------


async def test_full_two_location_flow_no_double_booking():
    """Simulate the full node pipeline for 2 locations with a shared employee.
    After location A consumes Alice's window, location B should not have her
    available, and a different employee (Bob) should be assigned instead."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
        _make_employee(_EMP_B_ONLY_ID, "Carol", [_LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # --- Location A ---
    ctx_a = load_location_context(state)
    state.update(ctx_a)

    # Simulate LLM assigning Alice at location A
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_A_ID),
    ]
    val_a = validate_and_update_availability(state)
    state.update(val_a)

    # All shifts OK
    assert all(s["status"] == "ok" for s in state["current_parsed_shifts"])

    # Alice's window consumed
    assert _EMP_SHARED_ID in state["availability_draft"]
    assert len(state["availability_draft"][_EMP_SHARED_ID]) == 1

    emit_a = emit_result(state)
    state.update(emit_a)

    # --- Location B ---
    ctx_b = load_location_context(state)
    state.update(ctx_b)

    # Alice should have no available windows at location B
    alice_b = next(
        (e for e in state["current_employees"] if e["id"] == _EMP_SHARED_ID), None
    )
    assert alice_b is not None, "Alice is assigned to location B"
    assert alice_b["available_windows"] == [], (
        "Alice should have no availability left for location B"
    )

    # Carol should still have full availability
    carol_b = next(
        (e for e in state["current_employees"] if e["id"] == _EMP_B_ONLY_ID), None
    )
    assert carol_b is not None
    assert len(carol_b["available_windows"]) == 1

    # Simulate LLM correctly assigning Carol (not Alice) at location B
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_B_ONLY_ID, "Carol", _LOCATION_B_ID),
    ]
    val_b = validate_and_update_availability(state)
    state.update(val_b)

    assert all(s["status"] == "ok" for s in state["current_parsed_shifts"])

    emit_b = emit_result(state)
    state.update(emit_b)

    # --- Assertions on final state ---
    assert len(state["draft_schedules"]) == 2
    assert state["draft_schedules"][0]["status"] == "ok"
    assert state["draft_schedules"][1]["status"] == "ok"

    # Collect all shifts across both locations
    all_shifts = []
    for sched in state["draft_schedules"]:
        all_shifts.extend(sched["shifts"])

    # No employee should have overlapping shifts
    by_employee: Dict[str, List[ShiftAssignment]] = {}
    for s in all_shifts:
        by_employee.setdefault(s["employee_id"], []).append(s)

    for emp_id, shifts in by_employee.items():
        for i in range(len(shifts)):
            for j in range(i + 1, len(shifts)):
                assert not _windows_overlap(
                    shifts[i]["start_time"], shifts[i]["end_time"],
                    shifts[j]["start_time"], shifts[j]["end_time"],
                ), (
                    f"Employee {emp_id} has overlapping shifts: "
                    f"{shifts[i]['start_time']}-{shifts[i]['end_time']} and "
                    f"{shifts[j]['start_time']}-{shifts[j]['end_time']}"
                )


async def test_full_flow_conflict_detected_when_llm_ignores_availability():
    """Even if the LLM assigns Alice at location B despite her consumed window,
    the validation node catches it and marks the shift as CONFLICT after retry."""
    employees = [
        _make_employee(_EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID]),
    ]
    state = _build_state([_LOCATION_A_ID, _LOCATION_B_ID], employees)

    # --- Location A ---
    ctx_a = load_location_context(state)
    state.update(ctx_a)
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_A_ID),
    ]
    val_a = validate_and_update_availability(state)
    state.update(val_a)
    emit_a = emit_result(state)
    state.update(emit_a)

    # --- Location B (LLM ignores availability, assigns Alice again) ---
    ctx_b = load_location_context(state)
    state.update(ctx_b)

    # First attempt: LLM assigns Alice (conflict)
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID),
    ]
    val_b1 = validate_and_update_availability(state)
    state.update(val_b1)

    assert state["retry_count"] == 1, "First conflict should trigger retry"

    # Second attempt: LLM still assigns Alice (conflict persists)
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID),
    ]
    val_b2 = validate_and_update_availability(state)
    state.update(val_b2)

    # After retry exhausted, shift should be marked CONFLICT
    assert state["current_parsed_shifts"][0]["status"] == "CONFLICT"

    emit_b = emit_result(state)
    state.update(emit_b)

    assert state["draft_schedules"][1]["status"] == "CONFLICT"


async def test_three_locations_cumulative_availability_drain():
    """With 3 locations sharing the same employee across different time slots,
    availability drains cumulatively."""
    employees = [
        _make_employee(
            _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID, _LOCATION_B_ID, _LOCATION_C_ID],
            windows=[
                {"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T10:00:00{_TZ}"},
                {"start": f"{_MONDAY}T12:00:00{_TZ}", "end": f"{_MONDAY}T16:00:00{_TZ}"},
                {"start": f"{_MONDAY}T18:00:00{_TZ}", "end": f"{_MONDAY}T22:00:00{_TZ}"},
            ],
        ),
    ]
    state = _build_state(
        [_LOCATION_A_ID, _LOCATION_B_ID, _LOCATION_C_ID], employees
    )

    # --- Location A: consume morning window ---
    ctx_a = load_location_context(state)
    state.update(ctx_a)
    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_A_ID, start_hour=6, end_hour=10),
    ]
    val_a = validate_and_update_availability(state)
    state.update(val_a)
    emit_a = emit_result(state)
    state.update(emit_a)

    # --- Location B: consume afternoon window ---
    ctx_b = load_location_context(state)
    state.update(ctx_b)

    alice_b = next(e for e in state["current_employees"] if e["id"] == _EMP_SHARED_ID)
    assert len(alice_b["available_windows"]) == 2, (
        "Morning consumed, afternoon and evening should remain"
    )

    state["current_parsed_shifts"] = [
        _make_shift(_EMP_SHARED_ID, "Alice", _LOCATION_B_ID, start_hour=12, end_hour=16),
    ]
    val_b = validate_and_update_availability(state)
    state.update(val_b)
    emit_b = emit_result(state)
    state.update(emit_b)

    # --- Location C: only evening remains ---
    ctx_c = load_location_context(state)
    state.update(ctx_c)

    alice_c = next(e for e in state["current_employees"] if e["id"] == _EMP_SHARED_ID)
    assert len(alice_c["available_windows"]) == 1, (
        "Only evening window should remain after morning and afternoon consumed"
    )
    assert "T18:00:00" in alice_c["available_windows"][0]["start"]

    # All three locations should be conflict-free
    assert all(s["status"] == "ok" for s in state["draft_schedules"])


# ---------------------------------------------------------------------------
# Regression tests for #85 — cross-location double-booking
#
# The fixtures above use one consistent offset (_TZ) on BOTH sides, which is
# exactly why the timezone half of #85 went unnoticed for so long. Production
# does not: availability is persisted as local wall-clock TAGGED UTC (+00:00,
# the #61 contract) while generated shifts carry the location's real offset.
# These tests reproduce that mismatch deliberately.
# ---------------------------------------------------------------------------

_UTC = "+00:00"       # how availability actually reaches the pipeline
_REAL = "-04:00"      # how a generated America/New_York shift actually looks


async def test_mixed_offsets_still_consume_the_window():
    """The production shape: +00:00 availability vs -04:00 consumed shift.

    Before #85 these were compared as instants, so 09:00-04:00 (13:00 UTC)
    never looked like it touched 09:00+00:00 and the employee stayed fully
    available at the next location.
    """
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T09:00:00{_UTC}", "end": f"{_MONDAY}T17:00:00{_UTC}"}],
    )
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_REAL}", "end": f"{_MONDAY}T17:00:00{_REAL}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert remaining == [], (
        "A shift worked 09:00-17:00 local must consume the 09:00-17:00 window "
        "even though the two sides carry different offsets"
    )


async def test_mixed_offsets_partial_booking_carves_correctly():
    """Same offset mismatch, but only part of the day is worked."""
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T06:00:00{_UTC}", "end": f"{_MONDAY}T22:00:00{_UTC}"}],
    )
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_REAL}", "end": f"{_MONDAY}T13:00:00{_REAL}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 2
    assert "T06:00:00" in remaining[0]["start"] and "T09:00:00" in remaining[0]["end"]
    assert "T13:00:00" in remaining[1]["start"] and "T22:00:00" in remaining[1]["end"]


async def test_output_preserves_each_windows_own_offset():
    """Subtraction must not silently rewrite the offset it was handed."""
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T08:00:00{_UTC}", "end": f"{_MONDAY}T17:00:00{_UTC}"}],
    )
    draft = {
        _EMP_SHARED_ID: [
            {"start": f"{_MONDAY}T09:00:00{_REAL}", "end": f"{_MONDAY}T13:00:00{_REAL}"},
        ]
    }
    remaining = _filter_availability_against_draft(emp, draft)
    assert all(w["start"].endswith(_UTC) and w["end"].endswith(_UTC) for w in remaining)


async def test_booking_at_the_start_leaves_only_the_tail():
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}],
    )
    draft = {_EMP_SHARED_ID: [
        {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T12:00:00{_TZ}"}]}
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 1
    assert "T12:00:00" in remaining[0]["start"] and "T17:00:00" in remaining[0]["end"]


async def test_booking_at_the_end_leaves_only_the_head():
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}],
    )
    draft = {_EMP_SHARED_ID: [
        {"start": f"{_MONDAY}T14:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}]}
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 1
    assert "T09:00:00" in remaining[0]["start"] and "T14:00:00" in remaining[0]["end"]


async def test_two_bookings_leave_the_gap_between_them():
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T22:00:00{_TZ}"}],
    )
    draft = {_EMP_SHARED_ID: [
        {"start": f"{_MONDAY}T06:00:00{_TZ}", "end": f"{_MONDAY}T10:00:00{_TZ}"},
        {"start": f"{_MONDAY}T18:00:00{_TZ}", "end": f"{_MONDAY}T22:00:00{_TZ}"}]}
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 1
    assert "T10:00:00" in remaining[0]["start"] and "T18:00:00" in remaining[0]["end"]


async def test_a_different_day_is_untouched():
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[
            {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"},
            {"start": f"{_TUESDAY}T09:00:00{_TZ}", "end": f"{_TUESDAY}T17:00:00{_TZ}"},
        ],
    )
    draft = {_EMP_SHARED_ID: [
        {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}]}
    remaining = _filter_availability_against_draft(emp, draft)
    assert len(remaining) == 1
    assert _TUESDAY in remaining[0]["start"]


async def test_a_malformed_consumed_window_does_not_raise():
    """The scheduling graph degrades; it never throws."""
    emp = _make_employee(
        _EMP_SHARED_ID, "Alice", [_LOCATION_A_ID],
        windows=[{"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}],
    )
    draft = {_EMP_SHARED_ID: [
        {"start": "not-a-timestamp", "end": "also-not"},
        {"start": f"{_MONDAY}T09:00:00{_TZ}", "end": f"{_MONDAY}T17:00:00{_TZ}"}]}
    remaining = _filter_availability_against_draft(emp, draft)
    assert remaining == [], "the parseable window is still applied"
