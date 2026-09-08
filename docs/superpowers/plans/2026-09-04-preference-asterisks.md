# Preference Asterisks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark every shift scheduled against a soft preference with an asterisk that explains itself on hover or focus, and tell the manager when a location could not have been staffed without overriding someone's preference.

**Architecture:** A pure annotator in `backend/scheduling/preferences.py` reuses the existing evaluator to attach `preference_violations` to each shift. A new graph node runs it once per emitted location and computes a per-location `preference_summary`, re-deriving slot eligibility to decide whether each violation was unavoidable. Both are persisted (a JSON column on `shifts`, one on `shift_schedules`) and recomputed on the two hand-edit paths. The frontend renders the column; it never evaluates preferences itself.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 async, Alembic, LangGraph, pytest (SQLite in tests); React 18, TypeScript, Tailwind, vitest.

**Spec:** `docs/superpowers/specs/2026-09-04-preference-asterisks-design.md`

## Global Constraints

- No new dependencies, backend or frontend. Frontend tests are plain vitest with no DOM library, so UI logic under test must live in pure functions.
- Nothing inside the scheduling graph may raise. Every new node and helper skips malformed input and logs at warning level.
- Only weights strictly below `1.0` are ever reported. Hard preferences are already vacated by the trim passes.
- Times in the frontend are sliced from stored `HH:MM` strings, never passed through `Date`.
- Tailwind direction utilities must be logical (`start-0`, `ms-1`, `text-start`); `frontend/src/utils/logicalDirection.test.ts` enforces this.
- "Today" is UTC: `datetime.now(timezone.utc).date()`; `tests/test_utc_today.py` enforces this by AST sweep.
- Use the generic `sqlalchemy.JSON` type for new columns (tests run on SQLite).
- Migration `0034` revises `0033`.
- i18n: `Translations = DeepStringify<typeof en>`, so every key added to `en.ts` must be added to all 18 other locale files or `tsc` fails. English text in every file.
- Commit after every task with the trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01VXX97fJHmU1zF9XB2mwbBV
  ```
- Backend tests: `backend/.venv/bin/pytest tests/ -q -p no:cacheprovider` from the repo root. Frontend: `cd frontend && npm run build && npm test`.

---

## File map

| File | Responsibility |
| --- | --- |
| `backend/scheduling/preferences.py` | `violations_for_slot` (single composition), `annotate_preference_violations` (the annotator) |
| `backend/scheduling/state.py` | new keys on `ShiftAssignment`, `LocationResult`, `SchedulingState` |
| `backend/scheduling/nodes.py` | `validate_and_update_availability` returns `range_counts_before`; new `annotate_preferences` node |
| `backend/scheduling/graph.py` | wires the node; `_load_initial_state` uses the shared preference loader |
| `backend/services/preference_loader.py` | `load_employee_preferences(db, company_id)` shared by the graph and both edit routes |
| `backend/alembic/versions/0034_add_preference_violations.py` | the two JSON columns |
| `backend/models/schedule.py` | `Shift.preference_violations`, `ShiftSchedule.preference_summary` |
| `backend/schemas/schedule.py` | `ShiftUpdate.preference_violations`, `ShiftResponse.preference_violations`, `ShiftScheduleResponse.preference_summary`, `UpdateShiftsResponse` |
| `backend/routers/schedules.py` | generate stores summary; approve copies violations; draft edit re-annotates; approved edit recomputes |
| `frontend/src/types/index.ts` | `PreferenceViolation`, `PreferenceSummary`, fields on `ShiftAssignment` and `LocationResult` |
| `frontend/src/api/schedules.ts`, `frontend/src/api/approvedSchedules.ts` | response types, `toAssignments` carries violations |
| `frontend/src/utils/preferenceText.ts` (+ test) | pure text builder for the tooltip and banner |
| `frontend/src/components/shared/ScheduleGrid.tsx` | asterisk + tooltip |
| `frontend/src/components/shared/RosterThinBanner.tsx` | the banner |
| `frontend/src/pages/manager/Schedule.tsx`, `ApprovedSchedules.tsx` | banner placement; draft edit saves and replaces |
| `frontend/src/i18n/*.ts` | new `schedule.*` keys |

---

### Task 1: `violations_for_slot` — one composition of the three checks

**Files:**
- Modify: `backend/scheduling/preferences.py:127-166`
- Test: `tests/test_preference_annotations.py` (create)

**Interfaces:**
- Produces: `violations_for_slot(emp: Dict, day_index: int, start: str, end: str, range_counts: Dict[Any, int]) -> List[Dict[str, Any]]` — the concatenation `_day_violated + _range_violated + _caps_exceeded`, each dict being the preference row itself (unchanged).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_preference_annotations.py
"""The asterisk feature (#99): one evaluator, reported instead of discarded."""

from backend.scheduling.preferences import (
    blocked_by_hard_preference,
    preference_score,
    violations_for_slot,
)


def _emp(eid="e1", day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": eid,
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_violations_for_slot_returns_the_rows_the_other_two_act_on():
    emp = _emp(
        day_prefs=[{"day_of_week": 0, "weight": 0.5}],
        range_prefs=[{"start_time": "16:00", "end_time": "22:00", "weight": 1.0}],
        caps=[{"start_time": "09:00", "end_time": "17:00", "max_per_week": 1, "weight": 0.7}],
    )
    counts = {("e1", "09:00", "17:00"): 1}
    # Tuesday 09:00-17:00: violates the day pref, the range pref, and the cap.
    v = violations_for_slot(emp, 1, "09:00", "17:00", counts)
    assert sorted(float(x["weight"]) for x in v) == [0.5, 0.7, 1.0]
    assert blocked_by_hard_preference(emp, 1, "09:00", "17:00", counts) is True
    # Only the two soft rows score: (0.5 + 0.7) * 50
    assert preference_score(emp, 1, "09:00", "17:00", counts) == 60.0


def test_no_preferences_means_no_violations():
    assert violations_for_slot(_emp(), 3, "09:00", "17:00", {}) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_annotations.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'violations_for_slot'`

- [ ] **Step 3: Implement**

In `backend/scheduling/preferences.py`, add above `blocked_by_hard_preference` and make both callers use it:

```python
def violations_for_slot(
    emp: Dict[str, Any],
    day_index: int,
    start: str,
    end: str,
    range_counts: Dict[Any, int],
) -> List[Dict[str, Any]]:
    """Every preference row this (day, start, end) slot violates.

    THE single composition of the three checks. blocked_by_hard_preference
    keeps only the weight-1.0 rows; preference_score keeps only the rest;
    annotate_preference_violations reports the rest. All three see the same
    list, which is what keeps the asterisk's explanation from ever
    disagreeing with the scheduler's own reasoning.
    """
    return (
        _day_violated(emp, day_index)
        + _range_violated(emp, start, end)
        + _caps_exceeded(emp, start, end, range_counts)
    )
```

Then replace the two inline `violations = (...)` blocks in `blocked_by_hard_preference` and `preference_score` with `violations = violations_for_slot(emp, day_index, start, end, range_counts)`.

- [ ] **Step 4: Run the new file and the existing scoring tests**

Run: `backend/.venv/bin/pytest tests/test_preference_annotations.py tests/test_preference_scoring.py tests/test_preferences_local_scheduler.py tests/test_preferences_ai_path.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/preferences.py tests/test_preference_annotations.py
git commit -m "refactor(scheduling): one composition of the preference checks (#99)"
```

---

### Task 2: `annotate_preference_violations` — the annotator

**Files:**
- Modify: `backend/scheduling/preferences.py` (append)
- Modify: `backend/scheduling/state.py:12-21`
- Test: `tests/test_preference_annotations.py` (append)

**Interfaces:**
- Consumes: `violations_for_slot` from Task 1; `matches_range` (existing).
- Produces:
  ```python
  def annotate_preference_violations(
      shifts: List[Dict[str, Any]],
      employee_preferences: Dict[str, Dict[str, Any]],
      seed_counts: Dict[Any, int] | None = None,
  ) -> List[Dict[Any, int]]
  ```
  Mutates each shift: sets `shift["preference_violations"]` to a list of dicts of shape `{"kind": "day"|"hour_range"|"cap", "weight": float, "unavoidable": False, ...kind fields}`. Returns a list, index-aligned with `shifts`, of the running cap counts **as they stood before that shift was counted** (the node uses it in Task 4). Non-"ok" shifts get `[]` and their snapshot is the counts at that point.
- `ShiftAssignment` gains `preference_violations: List[Dict[str, Any]]` under `total=False` semantics (see step 3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_preference_annotations.py`:

```python
from backend.scheduling.preferences import annotate_preference_violations


def _shift(eid, date, start, end, status="ok"):
    return {
        "employee_id": eid, "employee_name": eid, "role_id": "r1",
        "role_name": "Cook", "location_id": "loc1", "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00", "status": status,
    }


def _prefs(day_prefs=None, range_prefs=None, caps=None):
    return {"day_preferences": day_prefs or [],
            "hour_range_preferences": range_prefs or [],
            "hour_range_caps": caps or []}


def test_day_violation_reports_the_whole_preferred_set():
    # 2026-09-03 is a Thursday (weekday 3); prefers Mon/Tue/Wed
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    prefs = {"e1": _prefs(day_prefs=[
        {"day_of_week": 0, "weight": 0.7},
        {"day_of_week": 1, "weight": 0.7},
        {"day_of_week": 2, "weight": 0.9},
    ])}
    annotate_preference_violations(shifts, prefs)
    v = shifts[0]["preference_violations"]
    assert v == [{"kind": "day", "weight": 0.9, "days": [0, 1, 2], "unavoidable": False}]


def test_hour_range_violation_carries_the_range():
    shifts = [_shift("e1", "2026-08-31", "09:00", "13:00")]
    prefs = {"e1": _prefs(range_prefs=[
        {"start_time": "16:00", "end_time": "22:00", "weight": 0.6}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"] == [
        {"kind": "hour_range", "weight": 0.6, "start_time": "16:00",
         "end_time": "22:00", "unavoidable": False}
    ]


def test_cap_marks_the_fourth_shift_not_the_first_three():
    shifts = [_shift("e1", f"2026-09-0{d}", "16:00", "22:00") for d in (1, 2, 3, 4)]
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    snapshots = annotate_preference_violations(shifts, prefs)
    assert [s["preference_violations"] for s in shifts[:3]] == [[], [], []]
    assert shifts[3]["preference_violations"] == [
        {"kind": "cap", "weight": 0.8, "start_time": "16:00", "end_time": "22:00",
         "max_per_week": 3, "unavoidable": False}
    ]
    assert snapshots[3] == {("e1", "16:00", "22:00"): 3}
    assert snapshots[0] == {}


def test_cap_count_is_seeded_from_earlier_locations():
    shifts = [_shift("e1", "2026-09-04", "16:00", "22:00")]
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    annotate_preference_violations(shifts, prefs, seed_counts={("e1", "16:00", "22:00"): 3})
    assert shifts[0]["preference_violations"][0]["kind"] == "cap"


def test_shifts_are_walked_in_date_order_regardless_of_input_order():
    later = _shift("e1", "2026-09-04", "16:00", "22:00")
    earlier = [_shift("e1", f"2026-09-0{d}", "16:00", "22:00") for d in (1, 2, 3)]
    shifts = [later] + earlier
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"][0]["kind"] == "cap"
    assert all(s["preference_violations"] == [] for s in shifts[1:])


def test_hard_preferences_are_never_reported():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 1.0}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"] == []


def test_vacant_conflict_and_unconfigured_shifts_get_an_empty_list():
    shifts = [
        _shift("VACANT", "2026-09-03", "09:00", "17:00", status="VACANT"),
        _shift("e1", "2026-09-03", "09:00", "17:00", status="CONFLICT"),
        _shift("e2", "2026-09-03", "09:00", "17:00"),
    ]
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 0.5}])}
    annotate_preference_violations(shifts, prefs)
    assert [s["preference_violations"] for s in shifts] == [[], [], []]


def test_a_malformed_shift_is_skipped_and_the_rest_annotated():
    bad = {"employee_id": "e1", "status": "ok", "date": "not-a-date",
           "start_time": "??", "end_time": "??"}
    good = _shift("e1", "2026-09-03", "09:00", "17:00")
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 0.5}])}
    annotate_preference_violations([bad, good], prefs)
    assert bad["preference_violations"] == []
    assert good["preference_violations"][0]["kind"] == "day"


def test_empty_preferences_is_a_no_op_that_still_sets_the_field():
    s = _shift("e1", "2026-09-03", "09:00", "17:00")
    annotate_preference_violations([s], {})
    assert s["preference_violations"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_annotations.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'annotate_preference_violations'`

- [ ] **Step 3: Implement**

Add `from datetime import date` to the imports of `backend/scheduling/preferences.py`, then append:

```python
def _violation_dict(row: Dict[str, Any], emp_prefs: Dict[str, Any]) -> Dict[str, Any] | None:
    """Shape one violated preference row for the API.

    A day row carries the WHOLE preferred set: "prefers Mon, Tue, Wed" is
    the sentence a manager needs, and _day_violated's collapsed row only
    knows the strongest single day.
    """
    weight = float(row["weight"])
    if "day_of_week" in row:
        days = sorted(int(p["day_of_week"]) for p in emp_prefs.get("day_preferences") or [])
        return {"kind": "day", "weight": weight, "days": days, "unavoidable": False}
    if "max_per_week" in row:
        return {
            "kind": "cap", "weight": weight,
            "start_time": row["start_time"], "end_time": row["end_time"],
            "max_per_week": int(row["max_per_week"]), "unavoidable": False,
        }
    if "start_time" in row:
        return {
            "kind": "hour_range", "weight": weight,
            "start_time": row["start_time"], "end_time": row["end_time"],
            "unavoidable": False,
        }
    return None


def annotate_preference_violations(
    shifts: List[Dict[str, Any]],
    employee_preferences: Dict[str, Dict[str, Any]],
    seed_counts: Dict[Any, int] | None = None,
) -> List[Dict[Any, int]]:
    """Attach `preference_violations` to every shift; report, never act.

    Walks "ok" shifts in (date, start_time) order -- the same order
    nodes._trim_cap_violations uses -- with a running per-(employee, cap)
    count seeded from `seed_counts` (the cross-location count from earlier
    locations in the same graph run). A cap is counted only after the shift
    is evaluated, so the fourth shift against a cap of 3 is the one marked.

    Only weights below 1.0 are reported: hard violations have already been
    vacated by the trim passes, and a defensive filter keeps them out here
    regardless. VACANT, CONFLICT and no-preference shifts get [].

    Returns the count snapshot BEFORE each shift, index-aligned with
    `shifts`, so a caller re-deriving eligibility for a slot can evaluate
    candidates against the same counts this shift was judged by.

    Never raises: a malformed shift or preference row is skipped and its
    shift gets [].
    """
    counts: Dict[Any, int] = dict(seed_counts or {})
    snapshots: List[Dict[Any, int]] = [{} for _ in shifts]
    for s in shifts:
        s["preference_violations"] = []

    order = sorted(
        range(len(shifts)),
        key=lambda i: (str(shifts[i].get("date", "")), str(shifts[i].get("start_time", ""))),
    )
    for i in order:
        shift = shifts[i]
        snapshots[i] = dict(counts)
        if shift.get("status") != "ok":
            continue
        emp_id = str(shift.get("employee_id", ""))
        emp_prefs = employee_preferences.get(emp_id)
        if not emp_prefs:
            continue
        try:
            day_index = date.fromisoformat(str(shift["date"])).weekday()
            start_hm = str(shift["start_time"])[11:16]
            end_hm = str(shift["end_time"])[11:16]
            emp = {"id": emp_id, **emp_prefs}
            rows = violations_for_slot(emp, day_index, start_hm, end_hm, counts)
            reported: List[Dict[str, Any]] = []
            for row in rows:
                if float(row.get("weight", 0)) >= _HARD:
                    continue
                shaped = _violation_dict(row, emp_prefs)
                if shaped is not None:
                    reported.append(shaped)
            shift["preference_violations"] = reported
            for cap in emp_prefs.get("hour_range_caps") or []:
                if matches_range(start_hm, end_hm, cap["start_time"], cap["end_time"]):
                    key = (emp_id, cap["start_time"], cap["end_time"])
                    counts[key] = counts.get(key, 0) + 1
        except (KeyError, ValueError, TypeError, IndexError):
            shift["preference_violations"] = []
            continue
    return snapshots
```

In `backend/scheduling/state.py`, split `ShiftAssignment` so the new key is optional (TypedDict `total=False` on a subclass keeps every existing literal valid):

```python
class _ShiftAssignmentRequired(TypedDict):
    employee_id: str
    employee_name: str
    role_id: str
    role_name: str
    location_id: str
    date: str          # "YYYY-MM-DD"
    start_time: str    # ISO 8601 with tz offset
    end_time: str      # ISO 8601 with tz offset
    status: str        # "ok" | "CONFLICT" | "VACANT"


class ShiftAssignment(_ShiftAssignmentRequired, total=False):
    # Soft preferences this assignment was scheduled against (#99). Set by
    # preferences.annotate_preference_violations; absent on shifts that
    # predate the annotate_preferences node. Reported, never acted on.
    preference_violations: List[Dict[str, Any]]
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preference_annotations.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/preferences.py backend/scheduling/state.py tests/test_preference_annotations.py
git commit -m "feat(scheduling): annotate shifts with the soft preferences they override (#99)"
```

---

### Task 3: `range_counts_before` from the validation node

**Files:**
- Modify: `backend/scheduling/nodes.py:1080-1221` (`validate_and_update_availability`)
- Modify: `backend/scheduling/state.py` (SchedulingState)
- Test: `tests/test_preferences_ai_path.py` (append)

**Interfaces:**
- Produces: `validate_and_update_availability` returns, on both non-retry branches, `"range_counts_before": <copy of state["range_counts_draft"] taken on entry>`. `SchedulingState.range_counts_before: Dict[Any, int]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preferences_ai_path.py`:

```python
def test_validation_reports_the_cap_counts_it_started_from():
    """annotate_preferences (#99) must count caps the way generation counted
    them: seeded from earlier locations, before this location's shifts are
    folded in. The node exposes that pre-fold snapshot."""
    caps = [{"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.5}]
    state = {
        "current_parsed_shifts": [_shift("e1", "2026-08-31", "16:00", "22:00")],
        "availability_draft": {},
        "retry_count": 0,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "range_counts_draft": {("e1", "16:00", "22:00"): 2},
        "employees": [{"id": "e1", "hour_range_caps": caps}],
        "employee_preferences": {"e1": {"day_preferences": [], "hour_range_preferences": [], "hour_range_caps": caps}},
    }
    out = validate_and_update_availability(state)
    assert out["range_counts_before"] == {("e1", "16:00", "22:00"): 2}
    assert out["range_counts_draft"] == {("e1", "16:00", "22:00"): 3}
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preferences_ai_path.py::test_validation_reports_the_cap_counts_it_started_from -q -p no:cacheprovider`
Expected: FAIL with `KeyError: 'range_counts_before'`

- [ ] **Step 3: Implement**

In `validate_and_update_availability`, immediately after `range_counts_draft = dict(...)` add:

```python
    # Snapshot for annotate_preferences (#99): the cap counts as they stood
    # before this location's shifts were folded in, so the annotator judges
    # each shift against the same count generation judged it by.
    range_counts_before: Dict[Any, int] = dict(range_counts_draft)
```

Add `"range_counts_before": range_counts_before,` to both return dicts that also return `"range_counts_draft"` (the CONFLICT-after-retry branch and the no-conflict branch). Leave the retry branch alone.

In `state.py`, add to `SchedulingState` after `range_counts_draft`:

```python
    # Copy of range_counts_draft taken on entry to
    # validate_and_update_availability, before this location's shifts were
    # folded in. Read by annotate_preferences (#99).
    range_counts_before: Dict[Any, int]
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preferences_ai_path.py tests/test_local_scheduler.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scheduling/nodes.py backend/scheduling/state.py tests/test_preferences_ai_path.py
git commit -m "feat(scheduling): expose pre-fold cap counts from validation (#99)"
```

---

### Task 4: `annotate_preferences` node and graph wiring

**Files:**
- Modify: `backend/scheduling/nodes.py` (new node before `emit_result`)
- Modify: `backend/scheduling/nodes.py:1224-1300` (`emit_result` carries `preference_summary`)
- Modify: `backend/scheduling/graph.py:150-233` (wiring)
- Modify: `backend/scheduling/state.py` (`LocationResult.preference_summary`, `SchedulingState.current_preference_summary`)
- Test: `tests/test_annotate_preferences_node.py` (create)

**Interfaces:**
- Consumes: `annotate_preference_violations` (Task 2), `range_counts_before` (Task 3), `eligible_for_slot` and `_build_date_map`, `_parse_avail_by_day` from `backend/scheduling/prompts.py`, `_windows_overlap` in `nodes.py`.
- Produces: `annotate_preferences(state) -> Dict[str, Any]` returning `{"current_parsed_shifts": shifts, "current_preference_summary": {...}}`. `emit_result` copies `current_preference_summary` onto the `LocationResult` as `preference_summary`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_annotate_preferences_node.py
"""annotate_preferences (#99): the post-pass that explains the schedule.

Runs after validate_and_update_availability on both paths. Reports, never
acts: shifts and statuses are untouched, only preference_violations and
the per-location summary are added.
"""

from backend.scheduling.nodes import annotate_preferences, emit_result


def _shift(eid, date, start, end, status="ok", role="Cook"):
    return {
        "employee_id": eid, "employee_name": eid, "role_id": "r1",
        "role_name": role, "location_id": "loc1", "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00", "status": status,
    }


def _emp(eid, day_prefs=None, range_prefs=None, caps=None, avail_dates=("2026-09-03",)):
    return {
        "id": eid, "full_name": eid, "location_ids": ["loc1"],
        "roles": [{"role_id": "r1", "role_name": "Cook", "skill_level": 3}],
        "affinities": [],
        "available_windows": [
            {"start": f"{d}T00:00:00+00:00", "end": f"{d}T23:59:00+00:00"} for d in avail_dates
        ],
        "max_hours_per_week": None, "day_blackouts": [],
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def _state(shifts, employees, availability_draft=None):
    prefs = {
        e["id"]: {
            "day_preferences": e["day_preferences"],
            "hour_range_preferences": e["hour_range_preferences"],
            "hour_range_caps": e["hour_range_caps"],
        }
        for e in employees
    }
    return {
        "week_start_date": "2026-08-31",
        "num_days": 7,
        "current_location": {"id": "loc1", "name": "Main"},
        "current_employees": employees,
        "current_parsed_shifts": shifts,
        "availability_draft": availability_draft or {},
        "range_counts_before": {},
        "employee_preferences": prefs,
        "errors": [],
        "draft_schedules": [],
        "completed_location_ids": [],
        "current_location_index": 0,
        "failure_entries": [],
    }


MON_TUE_WED = [{"day_of_week": d, "weight": 0.7} for d in (0, 1, 2)]


def test_a_violating_shift_is_annotated_and_counted():
    # Thursday shift, e1 prefers Mon-Wed, and e2 is free and unconstrained.
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2")]))
    v = out["current_parsed_shifts"][0]["preference_violations"]
    assert v[0]["kind"] == "day" and v[0]["unavoidable"] is False
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 1, "unavoidable": 0, "roster_thin": False,
    }


def test_no_clean_alternative_marks_the_violation_unavoidable():
    # Both employees prefer Mon-Wed; whoever works Thursday violates.
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    emps = [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2", day_prefs=MON_TUE_WED)]
    out = annotate_preferences(_state(shifts, emps))
    assert out["current_parsed_shifts"][0]["preference_violations"][0]["unavoidable"] is True
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 1, "unavoidable": 1, "roster_thin": True,
    }


def test_an_alternative_already_booked_does_not_count_as_free():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    emps = [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2")]
    draft = {"e2": [{"start": "2026-09-03T08:00:00-04:00", "end": "2026-09-03T12:00:00-04:00"}]}
    out = annotate_preferences(_state(shifts, emps, availability_draft=draft))
    assert out["current_preference_summary"]["unavoidable"] == 1


def test_an_alternative_without_the_role_does_not_count():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    e2 = _emp("e2")
    e2["roles"] = [{"role_id": "r9", "role_name": "Server", "skill_level": 3}]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED), e2]))
    assert out["current_preference_summary"]["unavoidable"] == 1


def test_clean_schedules_have_no_summary_noise():
    shifts = [_shift("e1", "2026-08-31", "09:00", "17:00")]  # Monday
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED)]))
    assert out["current_parsed_shifts"][0]["preference_violations"] == []
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 0, "unavoidable": 0, "roster_thin": False,
    }


def test_statuses_and_shift_count_are_untouched():
    shifts = [
        _shift("e1", "2026-09-03", "09:00", "17:00"),
        _shift("VACANT", "2026-09-03", "17:00", "22:00", status="VACANT"),
    ]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED)]))
    assert [s["status"] for s in out["current_parsed_shifts"]] == ["ok", "VACANT"]
    assert len(out["current_parsed_shifts"]) == 2


def test_a_broken_state_degrades_instead_of_raising():
    state = _state([_shift("e1", "2026-09-03", "09:00", "17:00")], [_emp("e1")])
    state["week_start_date"] = "garbage"
    out = annotate_preferences(state)
    assert len(out["current_parsed_shifts"]) == 1
    assert out["current_preference_summary"] is None


def test_emit_result_carries_the_summary():
    state = _state([_shift("e1", "2026-09-03", "09:00", "17:00")], [_emp("e1", day_prefs=MON_TUE_WED)])
    state.update(annotate_preferences(state))
    out = emit_result(state)
    assert out["draft_schedules"][0]["preference_summary"]["shifts_against_preference"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_annotate_preferences_node.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'annotate_preferences'`

- [ ] **Step 3: Implement the node**

In `backend/scheduling/nodes.py`, add to the imports:

```python
from backend.scheduling.preferences import (
    annotate_preference_violations,
    blocked_by_hard_preference,
    matches_range,
    violations_for_slot,
)
from backend.scheduling.prompts import _build_date_map, _parse_avail_by_day, eligible_for_slot
```

(Replace the existing `from backend.scheduling.preferences import blocked_by_hard_preference, matches_range` line. If `prompts` is not already imported in `nodes.py`, check for a circular import by running the test; `prompts.py` does not import `nodes.py`, so this is safe.)

Insert before `def emit_result`:

```python
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _clean_alternative_exists(
    shift: Dict[str, Any],
    prepared: List[Dict[str, Any]],
    availability_draft: Dict[str, List[Dict[str, str]]],
    counts: Dict[Any, int],
) -> bool:
    """Whether someone other than the assignee could have taken this slot
    without violating any preference of their own.

    Candidates come from eligible_for_slot -- role, availability, blackouts
    and hard preferences -- then must have no soft violation for the slot
    and no overlapping committed window in availability_draft. Weekly-hour
    caps and minimum rest are NOT re-checked, so a candidate counted as
    "free" here might have been refused by generation for those reasons:
    this under-reports unavoidable, which is the safe direction for a
    signal that reads "you may need to hire".
    """
    day_index = date.fromisoformat(shift["date"]).weekday()
    day_name = _DAY_NAMES[day_index]
    start_hm = shift["start_time"][11:16]
    end_hm = shift["end_time"][11:16]
    candidates = eligible_for_slot(
        prepared, day_name, shift["role_name"], start_hm, end_hm,
        day_index=day_index, range_counts=counts,
    )
    for c in candidates:
        cid = str(c.get("id", ""))
        if cid == str(shift.get("employee_id", "")):
            continue
        if violations_for_slot(c, day_index, start_hm, end_hm, counts):
            continue
        booked = any(
            _windows_overlap(shift["start_time"], shift["end_time"], w["start"], w["end"])
            for w in availability_draft.get(cid, [])
        )
        if booked:
            continue
        return True
    return False


def annotate_preferences(state: SchedulingState) -> Dict[str, Any]:
    """Post-pass (#99): mark shifts scheduled against a soft preference and
    say whether the roster left any alternative.

    Runs once per emitted location, after validate_and_update_availability
    on both the local and AI paths. Reports, never acts: no status changes,
    no shifts added or removed. Reuses the evaluator generation used, so the
    explanation cannot disagree with the decision.

    Never raises. On any failure the shifts are returned as they were and
    the summary is None.
    """
    shifts: List[ShiftAssignment] = list(state.get("current_parsed_shifts", []) or [])
    try:
        prefs = state.get("employee_preferences", {}) or {}
        seed = state.get("range_counts_before", {}) or {}
        snapshots = annotate_preference_violations(shifts, prefs, seed)

        date_map = _build_date_map(state["week_start_date"], int(state.get("num_days", 7)))
        date_to_day = {d: day for day, d in date_map.items()}
        prepared: List[Dict[str, Any]] = []
        for emp in state.get("current_employees", []) or []:
            prepared.append({
                **emp,
                "_role_names": {r.get("role_name", "") for r in emp.get("roles", [])},
                "_day_windows": _parse_avail_by_day(emp.get("available_windows", []), date_to_day),
            })
        availability_draft = state.get("availability_draft", {}) or {}

        against = 0
        unavoidable = 0
        for i, shift in enumerate(shifts):
            violations = shift.get("preference_violations") or []
            if not violations:
                continue
            against += 1
            try:
                clean = _clean_alternative_exists(shift, prepared, availability_draft, snapshots[i])
            except (KeyError, ValueError, TypeError, IndexError):
                clean = True  # unknown -> do not claim the roster is thin
            if not clean:
                unavoidable += 1
                for v in violations:
                    v["unavoidable"] = True

        summary = {
            "shifts_against_preference": against,
            "unavoidable": unavoidable,
            "roster_thin": unavoidable >= 1,
        }
        return {"current_parsed_shifts": shifts, "current_preference_summary": summary}
    except Exception as exc:  # degrade, never raise inside the graph
        logger.warning("[SCHED-TRACE] annotate_preferences failed: %s", exc)
        return {"current_parsed_shifts": shifts, "current_preference_summary": None}
```

In `emit_result`, add `"preference_summary": state.get("current_preference_summary"),` to the `result: LocationResult = {...}` literal.

- [ ] **Step 4: State and graph wiring**

`state.py`: add `preference_summary: Dict[str, Any] | None` to `LocationResult`, and `current_preference_summary: Dict[str, Any] | None` to `SchedulingState` (comment: "Set by annotate_preferences, copied onto LocationResult by emit_result (#99)").

`graph.py` `build_scheduling_graph`: register the node and route through it:

```python
    graph.add_node("annotate_preferences", annotate_preferences)
```

Local path: replace `graph.add_edge("validate_and_update_availability", "emit_result")` with two edges `validate_and_update_availability -> annotate_preferences` and `annotate_preferences -> emit_result`.

AI path: change the conditional edge map to `{"build_prompt": "build_prompt", "emit_result": "annotate_preferences"}` and add `graph.add_edge("annotate_preferences", "emit_result")`. `_should_retry_or_emit` still returns the string `"emit_result"`; only the mapping changes, so the node runs once per emitted location and never on the retry loop.

Import `annotate_preferences` alongside the other node imports at the top of `graph.py`.

- [ ] **Step 5: Run tests**

Run: `backend/.venv/bin/pytest tests/test_annotate_preferences_node.py tests/test_schedules.py tests/test_local_scheduler.py tests/test_preferences_local_scheduler.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/scheduling/nodes.py backend/scheduling/graph.py backend/scheduling/state.py tests/test_annotate_preferences_node.py
git commit -m "feat(scheduling): annotate_preferences node with roster-thin summary (#99)"
```

---

### Task 5: Columns, migration, response schemas

**Files:**
- Create: `backend/alembic/versions/0034_add_preference_violations.py`
- Modify: `backend/models/schedule.py`
- Modify: `backend/schemas/schedule.py:42-58, 97-109`
- Test: `tests/test_preference_persistence.py` (create)

**Interfaces:**
- Produces: `Shift.preference_violations: list | None`, `ShiftSchedule.preference_summary: dict | None`; `ShiftResponse.preference_violations: list[dict] = []` (NULL serialises as `[]`); `ShiftScheduleResponse.preference_summary: dict | None = None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preference_persistence.py
"""The persisted asterisk (#99): columns, serialisation, and the write sites."""

import json
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/schedules"
WEEK = date(2026, 8, 31)
DAY_V = [{"kind": "day", "weight": 0.7, "days": [0, 1, 2], "unavoidable": False}]


async def _approved_with_shift(db, violations):
    sid, shid = _id(), _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=WEEK, status="approved",
        created_at=datetime.now(timezone.utc),
        preference_summary={"shifts_against_preference": 1, "unavoidable": 0, "roster_thin": False},
    ))
    db.add(Shift(
        id=shid, company_id=COMPANY_ID, shift_schedule_id=sid, location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID, role_name="Floor Associate",
        date=date(2026, 9, 3),
        start_time=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 3, 17, 0, tzinfo=timezone.utc),
        preference_violations=violations,
    ))
    await db.commit()
    return sid, shid


async def test_week_endpoint_serialises_the_columns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _approved_with_shift(db_session, DAY_V)
    resp = await client.get(f"{BASE}/week/{WEEK.isoformat()}?status=approved",
                            headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    sched = resp.json()[0]
    assert sched["preference_summary"]["shifts_against_preference"] == 1
    assert sched["shifts"][0]["preference_violations"] == DAY_V


async def test_a_pre_existing_null_column_reads_as_an_empty_list(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _approved_with_shift(db_session, None)
    resp = await client.get(f"{BASE}/week/{WEEK.isoformat()}?status=approved",
                            headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.json()[0]["shifts"][0]["preference_violations"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py -q -p no:cacheprovider`
Expected: FAIL with `TypeError: 'preference_summary' is an invalid keyword argument for ShiftSchedule`

- [ ] **Step 3: Model columns**

`backend/models/schedule.py`: change the import to `from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text, text`. Add to `ShiftSchedule` after `created_at`:

```python
    # Per-location roster-thin read from generation (#99):
    # {"shifts_against_preference", "unavoidable", "roster_thin"}. A
    # generation-time observation; NOT recomputed when shifts are edited.
    preference_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

Add to `Shift` after `end_time`:

```python
    # Soft preferences this shift was scheduled against (#99). Written at
    # approval from the draft, rewritten on approved-shift edits. NULL on
    # rows that predate the column; the API serialises NULL as [].
    preference_violations: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Migration**

```python
# backend/alembic/versions/0034_add_preference_violations.py
"""add preference_violations to shifts and preference_summary to shift_schedules

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-04 00:00:00.000000

No backfill: the evaluation needs the preferences as they stood when the
schedule was generated, which nothing recorded. Existing rows stay NULL
and render without an asterisk (#99).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shifts", sa.Column("preference_violations", sa.JSON(), nullable=True))
    op.add_column("shift_schedules", sa.Column("preference_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shift_schedules", "preference_summary")
    op.drop_column("shifts", "preference_violations")
```

- [ ] **Step 5: Schemas**

`backend/schemas/schedule.py`:

```python
from pydantic import BaseModel, Field, field_validator  # extend the existing import line

class ShiftResponse(BaseModel):
    ...existing fields...
    employee_name: str = ""
    # NULL on rows older than #99 -> [] so the client has one shape to read.
    preference_violations: list[dict] = []

    @field_validator("preference_violations", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or []

    model_config = {"from_attributes": True}
```

Add `preference_summary: dict | None = None` to `ShiftScheduleResponse`.

- [ ] **Step 6: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py tests/test_schedules.py tests/test_edit_approved_schedule.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 7: Apply and roll back the migration against Postgres, if a local database is configured**

Run from `backend/`: `alembic upgrade head && alembic downgrade 0033 && alembic upgrade head`
Expected: three clean runs. If no local Postgres is reachable, note it in the commit body and rely on the deploy's `alembic upgrade head`.

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/0034_add_preference_violations.py backend/models/schedule.py backend/schemas/schedule.py tests/test_preference_persistence.py
git commit -m "feat(db): persist preference violations and roster-thin summary (#99)"
```

---

### Task 6: Generate stores the summary; approve copies the violations

**Files:**
- Modify: `backend/routers/schedules.py:205-222` (generate), `:600-634` (approve)
- Test: `tests/test_preference_persistence.py` (append)

- [ ] **Step 1: Write the failing test**

```python
async def test_approve_copies_violations_from_the_draft(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    shifts = [{
        "employee_id": EMPLOYEE1_ID, "employee_name": "Alice Johnson",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-09-03",
        "start_time": "2026-09-03T09:00:00-04:00", "end_time": "2026-09-03T17:00:00-04:00",
        "status": "ok", "preference_violations": DAY_V,
    }]
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID, week_start_date=WEEK,
        status="draft", raw_llm_output=json.dumps(shifts), strategy="random",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post(f"{BASE}/{sid}/approve", headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["shifts"][0]["preference_violations"] == DAY_V
    row = (await db_session.execute(select(Shift).where(Shift.shift_schedule_id == sid))).scalar_one()
    assert row.preference_violations == DAY_V
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py::test_approve_copies_violations_from_the_draft -q -p no:cacheprovider`
Expected: FAIL — `assert [] == DAY_V`

- [ ] **Step 3: Implement**

In `generate_schedule` where the `ShiftSchedule(...)` is constructed, add `preference_summary=chunk.get("preference_summary"),`.

In `approve_schedule`, in the `shift = Shift(...)` constructor inside the `for s in shifts_data:` loop, add:

```python
                        preference_violations=list(s.get("preference_violations") or []),
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py tests/test_schedules.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routers/schedules.py tests/test_preference_persistence.py
git commit -m "feat(api): carry preference annotations through generate and approve (#99)"
```

---

### Task 7: Shared preference loader; draft edits re-annotate

**Files:**
- Create: `backend/services/preference_loader.py`
- Modify: `backend/scheduling/graph.py:722-770` (use the loader)
- Modify: `backend/schemas/schedule.py:26-40` (`ShiftUpdate`, new `UpdateShiftsResponse`)
- Modify: `backend/routers/schedules.py:264-287` (`update_shifts`)
- Test: `tests/test_preference_persistence.py` (append)

**Interfaces:**
- Produces: `async def load_employee_preferences(db: AsyncSession, company_id: str) -> Dict[str, Dict[str, list]]` returning `{employee_id: {"day_preferences": [{"day_of_week", "weight"}], "hour_range_preferences": [{"start_time", "end_time", "weight"}], "hour_range_caps": [{"start_time", "end_time", "max_per_week", "weight"}]}}` with weights as `float`. Employees with no rows are absent from the dict.
- `PUT /schedules/{id}/shifts` now returns `{"ok": true, "shifts": [<annotated ShiftUpdate dicts>]}`.

- [ ] **Step 1: Write the failing tests**

```python
from backend.models.employee import EmployeeDayPreference


async def _mon_tue_wed(db, employee_id):
    for d in (0, 1, 2):
        db.add(EmployeeDayPreference(
            id=_id(), company_id=COMPANY_ID, employee_id=employee_id, day_of_week=d, weight=0.7,
        ))
    await db.commit()


async def test_draft_edit_reannotates_and_returns_the_shifts(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID, week_start_date=WEEK,
        status="draft", raw_llm_output="[]", created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    body = {"shifts": [{
        "employee_id": EMPLOYEE1_ID, "employee_name": "Alice Johnson",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-09-03",  # Thursday
        "start_time": "2026-09-03T09:00:00-04:00", "end_time": "2026-09-03T17:00:00-04:00",
        "status": "ok", "preference_violations": [],
    }]}
    resp = await client.put(f"{BASE}/{sid}/shifts", headers={"Authorization": f"Bearer {manager_token}"}, json=body)
    assert resp.status_code == 200, resp.text
    returned = resp.json()["shifts"][0]["preference_violations"]
    assert returned == DAY_V

    sched = (await db_session.execute(select(ShiftSchedule).where(ShiftSchedule.id == sid))).scalar_one()
    assert json.loads(sched.raw_llm_output)[0]["preference_violations"] == DAY_V


async def test_load_employee_preferences_shape(db_session: AsyncSession, seed_employees):
    from backend.services.preference_loader import load_employee_preferences
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    prefs = await load_employee_preferences(db_session, COMPANY_ID)
    assert prefs[EMPLOYEE1_ID]["day_preferences"] == [
        {"day_of_week": 0, "weight": 0.7}, {"day_of_week": 1, "weight": 0.7}, {"day_of_week": 2, "weight": 0.7},
    ]
    assert prefs[EMPLOYEE1_ID]["hour_range_preferences"] == []
    assert prefs[EMPLOYEE1_ID]["hour_range_caps"] == []
    assert EMPLOYEE1_ID in prefs and len(prefs) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py -q -p no:cacheprovider -k "draft_edit or load_employee"`
Expected: FAIL (`ModuleNotFoundError` for the loader; `KeyError: 'shifts'` for the edit)

- [ ] **Step 3: The loader**

```python
# backend/services/preference_loader.py
"""Load a company's scheduling preferences in the shape the evaluator reads.

Shared by the scheduling graph (_load_initial_state) and the two hand-edit
routes that re-annotate shifts (#99), so all three see the same rows shaped
the same way. Weights are cast to float: the column is Numeric and
SQLAlchemy returns Decimal, which breaks arithmetic against the plain
floats the scoring code uses.
"""

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.employee import (
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)


async def load_employee_preferences(
    db: AsyncSession, company_id: str
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    prefs: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def _bucket(eid: str) -> Dict[str, List[Dict[str, Any]]]:
        return prefs.setdefault(
            eid,
            {"day_preferences": [], "hour_range_preferences": [], "hour_range_caps": []},
        )

    for dp in (await db.execute(
        select(EmployeeDayPreference)
        .where(EmployeeDayPreference.company_id == company_id)
        .order_by(EmployeeDayPreference.day_of_week)
    )).scalars().all():
        _bucket(str(dp.employee_id))["day_preferences"].append(
            {"day_of_week": dp.day_of_week, "weight": float(dp.weight)}
        )

    for rp in (await db.execute(
        select(EmployeeHourRangePreference)
        .where(EmployeeHourRangePreference.company_id == company_id)
    )).scalars().all():
        _bucket(str(rp.employee_id))["hour_range_preferences"].append(
            {"start_time": rp.start_time, "end_time": rp.end_time, "weight": float(rp.weight)}
        )

    for rc in (await db.execute(
        select(EmployeeHourRangeCap)
        .where(EmployeeHourRangeCap.company_id == company_id)
    )).scalars().all():
        _bucket(str(rc.employee_id))["hour_range_caps"].append(
            {"start_time": rc.start_time, "end_time": rc.end_time,
             "max_per_week": rc.max_per_week, "weight": float(rc.weight)}
        )

    return prefs
```

In `graph.py` `_load_initial_state`, replace the three inline query blocks (`day_pref_result` through `emp_range_caps_map`) with:

```python
    loaded_prefs = await load_employee_preferences(db, company_id)
```

and in the `employees.append({...})` literal use:

```python
            "day_preferences": loaded_prefs.get(eid, {}).get("day_preferences", []),
            "hour_range_preferences": loaded_prefs.get(eid, {}).get("hour_range_preferences", []),
            "hour_range_caps": loaded_prefs.get(eid, {}).get("hour_range_caps", []),
```

Add `from backend.services.preference_loader import load_employee_preferences` to `graph.py` imports and remove the now-unused `EmployeeDayPreference`, `EmployeeHourRangePreference`, `EmployeeHourRangeCap` imports if nothing else in the file uses them (grep first).

- [ ] **Step 4: Schema and route**

`backend/schemas/schedule.py`:

```python
class ShiftUpdate(BaseModel):
    ...existing fields...
    status: str = "ok"
    # Round-trips the asterisk data (#99). The server re-annotates on save,
    # so a stale client value is overwritten, never trusted.
    preference_violations: list[dict] = []


class UpdateShiftsResponse(BaseModel):
    ok: bool = True
    shifts: list[ShiftUpdate]
```

`backend/routers/schedules.py` `update_shifts`: change the return annotation to `UpdateShiftsResponse`, and replace the final two lines with:

```python
    shifts = [s.model_dump() for s in body.shifts]
    # Re-annotate server-side (#99): a hand-edit may have moved a shift onto
    # or off a preference, and the evaluator lives here, not in the client.
    prefs = await load_employee_preferences(db, str(current_user.company_id))
    annotate_preference_violations(shifts, prefs)
    schedule.raw_llm_output = json.dumps(shifts)
    await db.commit()
    return UpdateShiftsResponse(shifts=[ShiftUpdate(**s) for s in shifts])
```

Add the imports `from backend.scheduling.preferences import annotate_preference_violations` and `from backend.services.preference_loader import load_employee_preferences`, and import `UpdateShiftsResponse` from the schemas.

- [ ] **Step 5: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py tests/test_schedules.py tests/test_scheduling_preferences_api.py tests/test_preferences_ai_path.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/preference_loader.py backend/scheduling/graph.py backend/schemas/schedule.py backend/routers/schedules.py tests/test_preference_persistence.py
git commit -m "feat(api): re-annotate draft edits; share the preference loader (#99)"
```

---

### Task 8: Approved edits recompute the touched employees' week

**Files:**
- Modify: `backend/routers/schedules.py` (`edit_approved_shifts`, after the edit loop, before `await db.commit()`)
- Test: `tests/test_preference_persistence.py` (append)

**Interfaces:**
- Consumes: `load_employee_preferences`, `annotate_preference_violations`, `_shift_local_face` (graph.py).

- [ ] **Step 1: Write the failing test**

```python
from tests.conftest import EMPLOYEE2_ID


async def test_approved_reassignment_rewrites_both_employees(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    """Alice prefers Mon-Wed and holds a Thursday shift (annotated). Reassigning
    it to Bob, who has no preferences, must clear the asterisk. Bob's other
    shift that week is re-evaluated too and stays clean."""
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid, shid = await _approved_with_shift(db_session, DAY_V)
    other = _id()
    db_session.add(Shift(
        id=other, company_id=COMPANY_ID, shift_schedule_id=sid, location_id=LOCATION_ID,
        employee_id=EMPLOYEE2_ID, role_id=ROLE_FLOOR_ID, role_name="Floor Associate",
        date=date(2026, 9, 1),
        start_time=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
        preference_violations=DAY_V,  # stale on purpose: must be recomputed to []
    ))
    await db_session.commit()

    resp = await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shid, "employee_id": EMPLOYEE2_ID}]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    moved = (await db_session.execute(select(Shift).where(Shift.id == shid))).scalar_one()
    stale = (await db_session.execute(select(Shift).where(Shift.id == other))).scalar_one()
    assert moved.preference_violations == []
    assert stale.preference_violations == []


async def test_approved_reassignment_onto_a_preference_adds_the_asterisk(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid, shid = await _approved_with_shift(db_session, [])
    # Move Alice's Thursday shift... to Alice. Same employee, but a time edit
    # still re-evaluates: keep it Thursday, change the hours.
    resp = await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shid, "start_time": "2026-09-03T10:00:00+00:00",
                          "end_time": "2026-09-03T18:00:00+00:00"}]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    row = (await db_session.execute(select(Shift).where(Shift.id == shid))).scalar_one()
    assert row.preference_violations == DAY_V
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py -q -p no:cacheprovider -k approved_reassignment`
Expected: FAIL — stale values remain

- [ ] **Step 3: Implement**

Inside `edit_approved_shifts`, collect the touched employee ids during the loop: declare `touched: set[str] = set()` before `applied = 0`; in the `if edit.shift_id:` branch add `touched.add(str(shift.employee_id))` right after the shift is loaded (before any mutation, so the previous employee is included), and after `shift.employee_id = edit.employee_id` add `touched.add(str(edit.employee_id))`; in the create branch add `touched.add(str(edit.employee_id))`. Then, after the loop and before `await db.commit()`:

```python
        await _reannotate_approved_week(
            db, str(current_user.company_id), schedule.week_start_date, touched
        )
```

Add the helper near `_shift_to_response`:

```python
async def _reannotate_approved_week(
    db: AsyncSession, company_id: str, week_start_date: date, employee_ids: set[str]
) -> None:
    """Recompute preference_violations for these employees across every
    approved schedule in the week (#99).

    The whole week, not just the edited schedule: a frequency cap counts
    across locations, so moving one shift can change which of an employee's
    OTHER shifts is the one past the cap. Uses the same annotator generation
    used. Wall-clock faces are recovered per location with _shift_local_face
    -- Shift timestamps are true instants, and the evaluator wants the
    location's HH:MM.
    """
    if not employee_ids:
        return
    from backend.scheduling.graph import _shift_local_face
    from backend.scheduling.preferences import annotate_preference_violations
    from backend.services.preference_loader import load_employee_preferences

    prefs = await load_employee_preferences(db, company_id)
    locations = {
        loc.id: loc for loc in (await db.execute(
            select(Location).where(Location.company_id == company_id)
        )).scalars().all()
    }
    rows = (await db.execute(
        select(Shift)
        .join(ShiftSchedule, Shift.shift_schedule_id == ShiftSchedule.id)
        .where(
            ShiftSchedule.company_id == company_id,
            ShiftSchedule.week_start_date == week_start_date,
            ShiftSchedule.status == "approved",
            Shift.employee_id.in_(employee_ids),
        )
    )).scalars().all()

    dicts: list[dict] = []
    for row in rows:
        loc = locations.get(row.location_id)
        face = _shift_local_face(row, loc, keep_tzinfo=True) if loc is not None else None
        start, end = face if face is not None else (row.start_time, row.end_time)
        dicts.append({
            "_row": row,
            "employee_id": row.employee_id,
            "status": "ok",
            "date": row.date.isoformat(),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        })
    annotate_preference_violations(dicts, prefs)
    for d in dicts:
        d["_row"].preference_violations = d["preference_violations"]
```

`Location` is already imported in the router (it is used by `get_week_schedules`); confirm with grep and add the import if not.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/pytest tests/test_preference_persistence.py tests/test_edit_approved_schedule.py tests/test_edit_approved_warnings.py -q -p no:cacheprovider`
Expected: all PASS

- [ ] **Step 5: Run the whole backend suite**

Run: `backend/.venv/bin/pytest tests/ -q -p no:cacheprovider`
Expected: all PASS (was 803 before this work).

- [ ] **Step 6: Commit**

```bash
git add backend/routers/schedules.py tests/test_preference_persistence.py
git commit -m "feat(api): recompute preference annotations on approved edits (#99)"
```

---

### Task 9: Frontend types, API, text builder, i18n

**Files:**
- Modify: `frontend/src/types/index.ts:257-276`
- Modify: `frontend/src/api/schedules.ts:14-24`
- Modify: `frontend/src/api/approvedSchedules.ts:6-55`
- Create: `frontend/src/utils/preferenceText.ts`, `frontend/src/utils/preferenceText.test.ts`
- Modify: `frontend/src/i18n/en.ts` (inside `schedule: {` which ends at line 719) and the same block in the other 18 locale files: `ar bn de es fr hi id ja mr pcm pt ru ta te tr ur vi zh`

**Interfaces:**
- Produces:
  ```ts
  export interface PreferenceViolation {
    kind: "day" | "hour_range" | "cap";
    weight: number;
    unavoidable: boolean;
    days?: number[];
    start_time?: string;
    end_time?: string;
    max_per_week?: number;
  }
  export interface PreferenceSummary {
    shifts_against_preference: number;
    unavoidable: number;
    roster_thin: boolean;
  }
  ```
  `ShiftAssignment.preference_violations?: PreferenceViolation[]`, `LocationResult.preference_summary?: PreferenceSummary | null`, `WeekShift.preference_violations: PreferenceViolation[]`, `WeekSchedule.preference_summary: PreferenceSummary | null`.
  ```ts
  export interface PreferenceStrings {
    prefDay: string; prefHourRange: string; prefCap: string;
    prefUnavoidable: string; weekdaysShort: string;
  }
  export function describeViolations(v: PreferenceViolation[], s: PreferenceStrings): string[]
  export function rosterThinMessage(summary: PreferenceSummary, template: string): string
  ```

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/utils/preferenceText.test.ts
import { describe, expect, it } from "vitest";
import { describeViolations, rosterThinMessage } from "./preferenceText";

const S = {
  prefDay: "prefers {days}",
  prefHourRange: "prefers {start}–{end}",
  prefCap: "already at {n}× this week for {start}–{end}",
  prefUnavoidable: "no one else was free",
  weekdaysShort: "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
};

describe("describeViolations", () => {
  it("names the preferred days in order", () => {
    expect(describeViolations([{ kind: "day", weight: 0.7, unavoidable: false, days: [0, 1, 2] }], S))
      .toEqual(["prefers Mon, Tue, Wed"]);
  });

  it("slices HH:MM from stored times without Date", () => {
    expect(describeViolations(
      [{ kind: "hour_range", weight: 0.5, unavoidable: false, start_time: "16:00:00", end_time: "22:00" }], S,
    )).toEqual(["prefers 16:00–22:00"]);
  });

  it("reports the cap with its allowance", () => {
    expect(describeViolations(
      [{ kind: "cap", weight: 0.8, unavoidable: false, start_time: "16:00", end_time: "22:00", max_per_week: 3 }], S,
    )).toEqual(["already at 3× this week for 16:00–22:00"]);
  });

  it("appends the unavoidable note", () => {
    expect(describeViolations([{ kind: "day", weight: 0.7, unavoidable: true, days: [4] }], S))
      .toEqual(["prefers Fri — no one else was free"]);
  });

  it("returns nothing for an empty or missing list", () => {
    expect(describeViolations([], S)).toEqual([]);
    expect(describeViolations(undefined, S)).toEqual([]);
  });
});

describe("rosterThinMessage", () => {
  it("fills both counts", () => {
    expect(rosterThinMessage(
      { shifts_against_preference: 4, unavoidable: 1, roster_thin: true },
      "{unavoidable} of {total} had no one else free.",
    )).toBe("1 of 4 had no one else free.");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/utils/preferenceText.test.ts`
Expected: FAIL — cannot resolve `./preferenceText`

- [ ] **Step 3: Types**

`frontend/src/types/index.ts`: add the two interfaces above before `ShiftAssignment`; add `preference_violations?: PreferenceViolation[];` to `ShiftAssignment` and `preference_summary?: PreferenceSummary | null;` to `LocationResult`.

`frontend/src/api/approvedSchedules.ts`: import `PreferenceSummary, PreferenceViolation` from `../types`; add `preference_summary: PreferenceSummary | null;` to `WeekSchedule` and `preference_violations: PreferenceViolation[];` to `WeekShift`; in `toAssignments` add `preference_violations: s.preference_violations ?? [],` to the mapped object and update its doc comment: "Approved shifts carry their persisted preference_violations (#99)."

`frontend/src/api/schedules.ts`: change `updateShifts` to return `Promise<{ ok: boolean; shifts: ShiftAssignment[] }>` and the generic on `apiFetch` to match.

- [ ] **Step 4: The text builder**

```ts
// frontend/src/utils/preferenceText.ts
/**
 * Words for the preference asterisk (#99).
 *
 * Pure so it can be tested without a DOM. Times are sliced from the stored
 * HH:MM strings, never parsed through Date — a preference is a wall-clock
 * face, and Date would re-anchor it to the viewer's zone (#92).
 */
import type { PreferenceSummary, PreferenceViolation } from "../types";

export interface PreferenceStrings {
  prefDay: string;
  prefHourRange: string;
  prefCap: string;
  prefUnavoidable: string;
  /** Comma-separated short weekday names, Monday first. */
  weekdaysShort: string;
}

const hm = (t: string | undefined) => (t ?? "").slice(0, 5);

export function describeViolations(
  violations: PreferenceViolation[] | undefined,
  s: PreferenceStrings,
): string[] {
  if (!violations || violations.length === 0) return [];
  const names = s.weekdaysShort.split(",");
  return violations.map((v) => {
    let line: string;
    if (v.kind === "day") {
      const days = (v.days ?? []).map((d) => names[d] ?? String(d)).join(", ");
      line = s.prefDay.replace("{days}", days);
    } else if (v.kind === "cap") {
      line = s.prefCap
        .replace("{n}", String(v.max_per_week ?? 0))
        .replace("{start}", hm(v.start_time))
        .replace("{end}", hm(v.end_time));
    } else {
      line = s.prefHourRange.replace("{start}", hm(v.start_time)).replace("{end}", hm(v.end_time));
    }
    return v.unavoidable ? `${line} — ${s.prefUnavoidable}` : line;
  });
}

export function rosterThinMessage(summary: PreferenceSummary, template: string): string {
  return template
    .replace("{unavoidable}", String(summary.unavoidable))
    .replace("{total}", String(summary.shifts_against_preference));
}
```

- [ ] **Step 5: i18n keys**

Add inside the `schedule: {` block of `frontend/src/i18n/en.ts` (before its closing `},`):

```ts
    prefAsteriskLabel: "Scheduled against a preference",
    prefDay: "prefers {days}",
    prefHourRange: "prefers {start}–{end}",
    prefCap: "already at {n}× this week for {start}–{end}",
    prefUnavoidable: "no one else was free",
    weekdaysShort: "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
    rosterThinBanner: "{unavoidable} of {total} shifts scheduled against a preference had no one else free. You may need more staff to honor preferences and stay covered.",
    rosterThinAtGeneration: "(at generation)",
```

Add the identical eight lines to the `schedule: {` block of every other locale file. Locate each block with `grep -n '^  schedule: {' frontend/src/i18n/*.ts`; insert immediately after that line in each file (English text, per the Global Constraints).

- [ ] **Step 6: Run tests and type-check**

Run: `cd frontend && npx vitest run src/utils/preferenceText.test.ts && npx tsc --noEmit`
Expected: 6 tests PASS; `tsc` clean (a missing key in any locale fails here).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/schedules.ts frontend/src/api/approvedSchedules.ts frontend/src/utils/preferenceText.ts frontend/src/utils/preferenceText.test.ts frontend/src/i18n/
git commit -m "feat(ui): preference violation types, text builder and strings (#99)"
```

---

### Task 10: Asterisk and tooltip in `ScheduleGrid`

**Files:**
- Modify: `frontend/src/components/shared/ScheduleGrid.tsx:143-162`

**Interfaces:**
- Consumes: `describeViolations` (Task 9), `t.schedule.*` keys (Task 9).

- [ ] **Step 1: Implement**

Add imports: `import { useId } from "react";` (merge with the existing `useMemo` import) and `import { describeViolations } from "../../utils/preferenceText";`.

Add a small component at the bottom of the file:

```tsx
/** The asterisk (#99). A real button so keyboard and touch reach the
 *  explanation; the tooltip shows on hover and on focus-within. Logical
 *  direction utilities only — ar and ur are RTL. */
function PreferenceMark({ lines, label }: { lines: string[]; label: string }) {
  const id = useId();
  return (
    <span className="group relative inline-block ms-1 align-baseline">
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        className="text-amber-700 font-bold leading-none px-0.5 rounded focus:outline-none focus:ring-2 focus:ring-amber-400"
      >
        *
      </button>
      <span
        id={id}
        role="tooltip"
        className="hidden group-hover:block group-focus-within:block absolute start-0 top-full mt-1 z-30 bg-gray-900 text-white text-xs rounded px-2 py-1.5 whitespace-nowrap shadow-lg pointer-events-none"
      >
        <span className="block font-semibold mb-0.5">{label}</span>
        {lines.map((l, i) => (
          <span key={i} className="block">{l}</span>
        ))}
      </span>
    </span>
  );
}
```

In the cell render, replace the employee-name `div` with:

```tsx
                            <div className="text-sm font-medium text-inherit">
                              {s.employee_name}
                              {(s.preference_violations?.length ?? 0) > 0 && (
                                <PreferenceMark
                                  label={t.schedule.prefAsteriskLabel}
                                  lines={describeViolations(s.preference_violations, t.schedule)}
                                />
                              )}
                            </div>
```

The cell `div` has an `onClick` for editing; the button's click must not open the edit modal. Add `onClick={(e) => e.stopPropagation()}` to the `<button>`.

- [ ] **Step 2: Build, test, and run the RTL guard**

Run: `cd frontend && npm run build && npm test`
Expected: build clean; all tests pass, including `logicalDirection.test.ts`.

- [ ] **Step 3: Visual check**

Run the dev servers (`uvicorn main:app --reload` in `backend/`, `npm run dev` in `frontend/`), generate a schedule for a seeded company with a Mon–Wed day preference on one employee, and confirm: the asterisk appears on a Thursday shift, hovering shows "prefers Mon, Tue, Wed", tabbing to it shows the same, and clicking it does not open the edit modal. If no local database is available, note the skipped check in the commit body.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/shared/ScheduleGrid.tsx
git commit -m "feat(ui): asterisk with keyboard-reachable explanation on preference overrides (#99)"
```

---

### Task 11: Banners and the draft-edit save

**Files:**
- Create: `frontend/src/components/shared/RosterThinBanner.tsx`
- Modify: `frontend/src/pages/manager/Schedule.tsx:661-673` (`handleSaveShift`), `:1409` (before `<ScheduleGrid`)
- Modify: `frontend/src/pages/manager/ApprovedSchedules.tsx:251` (before `<ScheduleGrid`)

**Interfaces:**
- Consumes: `rosterThinMessage` (Task 9), `schedulesApi.updateShifts` returning `{ ok, shifts }` (Task 9).

- [ ] **Step 1: The banner**

```tsx
// frontend/src/components/shared/RosterThinBanner.tsx
import { useLanguage } from "../../i18n/LanguageContext";
import type { PreferenceSummary } from "../../types";
import { rosterThinMessage } from "../../utils/preferenceText";

/** "You may need more staff" (#99). Renders nothing unless the summary
 *  says the roster left no clean alternative for at least one shift. */
export default function RosterThinBanner({
  summary,
  atGeneration = false,
}: {
  summary: PreferenceSummary | null | undefined;
  atGeneration?: boolean;
}) {
  const { t } = useLanguage();
  if (!summary || !summary.roster_thin) return null;
  return (
    <div
      role="status"
      className="mx-4 my-3 rounded-lg border border-amber-300 bg-amber-50 text-amber-900 text-sm px-3 py-2"
    >
      {rosterThinMessage(summary, t.schedule.rosterThinBanner)}
      {atGeneration && <span className="ms-1 opacity-75">{t.schedule.rosterThinAtGeneration}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Review page**

In `Schedule.tsx`, import the banner and render it directly above the `{currentShifts.length > 0 && (<ScheduleGrid` block:

```tsx
              <RosterThinBanner summary={locationResult.preference_summary} />
```

Replace `handleSaveShift` so a draft edit is saved and re-annotated immediately:

```tsx
  const handleSaveShift = async (updated: ShiftAssignment) => {
    if (!editingShift) return;
    const { locationId, shiftIndex } = editingShift;
    const result = results.find((r) => r.location_id === locationId);
    const current = editedShifts[locationId] ?? result?.shifts ?? [];
    const next = [...current];
    next[shiftIndex] = updated;
    setEditedShifts((prev) => ({ ...prev, [locationId]: next }));
    setEditingShift(null);
    // Save now rather than at approve (#99): the server re-annotates the
    // list, so the asterisk on a hand-edited shift is right immediately.
    if (result?.schedule_id) {
      try {
        const saved = await schedulesApi.updateShifts(result.schedule_id, next);
        setEditedShifts((prev) => ({ ...prev, [locationId]: saved.shifts }));
      } catch (err: unknown) {
        setActionError(err instanceof Error ? err.message : "Save failed");
      }
    }
  };
```

`results` is already in scope from `useScheduleStream()`. `saveShifts` and the approve flow stay as they are: a second save before approve is harmless.

- [ ] **Step 3: Approved page**

In `ApprovedSchedules.tsx`, import the banner and render it above the grid:

```tsx
                  <RosterThinBanner summary={sched.preference_summary} atGeneration />
```

- [ ] **Step 4: Build and test**

Run: `cd frontend && npm run build && npm test`
Expected: clean build; all tests pass.

- [ ] **Step 5: Visual check**

With the dev servers running: put the same Mon–Wed preference on every employee who can cover a Thursday slot, generate, and confirm the banner appears on the review page with "1 of 1"; approve and confirm the approved page shows the banner with "(at generation)"; edit the draft shift to a Wednesday before approving and confirm the asterisk clears without a page reload. Note in the commit body if skipped.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/shared/RosterThinBanner.tsx frontend/src/pages/manager/Schedule.tsx frontend/src/pages/manager/ApprovedSchedules.tsx
git commit -m "feat(ui): roster-thin banner; save draft edits immediately (#99)"
```

---

### Task 12: Full verification, docs, PR

**Files:**
- Modify: `CLAUDE.md` (Conventions section)
- Modify: `docs/superpowers/specs/2026-09-04-preference-asterisks-design.md` (Status line)

- [ ] **Step 1: Full suites**

Run: `backend/.venv/bin/pytest tests/ -q -p no:cacheprovider && cd frontend && npm run build && npm test`
Expected: all PASS.

- [ ] **Step 2: Conventions note**

Add to the Conventions list in `CLAUDE.md`, after the abuse-report bullet:

```markdown
- **Preference asterisks report, never act.** `preferences.annotate_preference_violations`
  is the only evaluator behind `shifts.preference_violations`; the frontend renders the
  column and never evaluates preferences itself. `shift_schedules.preference_summary`
  is a generation-time observation and is not recomputed on edit.
```

Change the spec's `Status:` line to `implemented`.

- [ ] **Step 3: Refresh the knowledge graph**

Run `/graphify . --update` in-session (the pre-push hook reminds you; it cannot run the skill itself).

- [ ] **Step 4: Commit and push**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-04-preference-asterisks-design.md graphify-out/
git commit -m "docs: preference asterisks convention and spec status (#99)"
git push -u origin feat/preference-asterisks
```

- [ ] **Step 5: Open the PR**

`gh pr create --base main --title "feat(scheduling): mark shifts scheduled against a preference (#99)"` with a body covering: the evaluate-don't-ask approach, store-not-derive and why (reads dominate), what `unavoidable` does and does not check (weekly hours and rest are not re-checked, so it under-reports), the three write sites, the draft-edit behaviour change (saves immediately), test counts, and "Closes #99". End with the standard generated-with footer.
