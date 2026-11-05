# Scheduling

Turning a roster and shift templates into a weekly schedule, then editing,
approving and exporting it.

## Modules owned

- `backend/scheduling/`: `graph.py` (`build_scheduling_graph`,
  `run_scheduling_pipeline`), `nodes.py` (`load_location_context`,
  `build_prompt`, `call_llm`, `parse_schedule`, `validate_schedule`,
  `validate_and_update_availability`, `annotate_preferences`,
  `emit_result`), `local_scheduler.py` (`local_schedule`), `prompts.py`
  (`eligible_for_slot`), `preferences.py`
  (`annotate_preference_violations`, `preference_score`), `open_shifts.py`
  (`is_open_entry`, `to_open_entry`), `state.py` (`SchedulingState`),
  `cost_seniority.py`, `rest_rules.py`, `template_resolver.py`,
  `pipeline_locations.py`.
- `backend/services/schedule_lock.py` (`acquire`, `release`,
  `expand_lock_set`, `LockHeld`), `candidates.py` (`rank_candidates`),
  `edit_warnings.py` (`collect_edit_warnings`), `signal_weights.py`,
  `special_hours.py`.
- Routers: `schedules`, `open_shifts`, `shift_templates`, `special_hours`,
  `export_schedules`. Models: `schedule` (`ShiftSchedule`, `Shift`),
  `schedule_lock`, `shift_template`, `special_hours_day`.
- Frontend: `pages/manager/{Schedule,ApprovedSchedules,ShiftTemplates,SpecialHours,ExportSchedules}.tsx`,
  `hooks/useScheduleStream.ts`, `hooks/useSignalWeights.ts`,
  `utils/openShifts.ts`, `utils/preferenceText.ts`, `utils/scheduleLock.ts`,
  `utils/shiftTime.ts`.

## Invariants

- The graph never raises. A parse failure sets `status="PARSE_ERROR"`. A
  second availability conflict sets `status="CONFLICT"`. LLM output is
  parsed defensively.
- `availability_draft` is a deep copy that each location mutates, so one
  run cannot double-book an employee across locations.
- An open shift is a `Shift` with `employee_id IS NULL`. Inside the graph an
  unfilled slot stays the `VACANT` placeholder, and `emit_result` is the
  only place it becomes open. Legacy drafts are read through
  `open_shifts.is_open_entry`. Every new reader of shifts decides whether it
  includes open shifts.
- `single-evaluator`: `eligible_for_slot` is shared by both scheduling paths.
  `annotate_preference_violations` is the only writer of
  `shifts.preference_violations`. `preference_summary` is a generation-time
  observation and is not recomputed on edit. Candidate ranking and edit
  warnings call `availability_check.is_available`.
- Generate, approve and edit hold the location-scoped lock
  (`schedule_lock.acquire`) for the locations that share employees.
- Generation is gated before any spend: `plan.check_can_generate`,
  `billing.check_ai_credits`, `email_verification.assert_email_verified`
  (`fail-closed`).
- Archived locations' drafts are `discarded`, never deleted. Schedule rows
  are counted by the free quota and the overage meter.
- Roles are never hardcoded in prompts or scoring.

## Entry points for other contexts

`graph.run_scheduling_pipeline`, `local_scheduler.local_schedule`,
`open_shifts.is_open_entry`, `preferences.annotate_preference_violations`,
`candidates.rank_candidates`, `edit_warnings.collect_edit_warnings`,
`schedule_lock.acquire` / `release`.

## Do not

- Raise inside a graph node, or let a malformed LLM response crash the
  stream.
- Write a second "is this slot eligible / is this preference violated"
  check, in the local scheduler, a router or the frontend.
- Compare `employee_id == "VACANT"` outside the graph. Use `is_open_entry`.
- Grow `routers/schedules.py` (already about 1,600 lines) with new rules. Put
  them in a service (`thin-router`).
- Hardcode a role name.

## Note on "available"

`services/availability_check.py` imports the graph's private helpers
(`nodes._subtract_consumed`, `nodes._wall_clock`), and its docstring says it
matches `graph._load_employee_availability` + `nodes.validate_schedule`.
The graph does not call `is_available` itself. Change both together, and
treat a drift between them as a `single-evaluator` finding.

## Tests that pin it

`tests/test_schedule_pipeline_integration.py`, `test_scheduling_model.py`,
`test_local_scheduler_integration.py`, `test_eligibility_shared.py`,
`test_annotate_preferences_node.py`, `test_preference_annotations_integration.py`,
`test_preferences_ai_path.py`, `test_preferences_local_scheduler.py`,
`test_open_shift_helpers.py`, `test_open_shifts_emit.py`,
`test_open_shifts_*_integration.py`, `test_schedule_lock_integration.py`,
`test_schedule_lock_concurrency_integration.py`,
`test_edit_approved_schedule_integration.py`,
`test_edit_approved_warnings_integration.py`,
`test_schedules_integration.py`, `test_schedule_week_limit.py`,
`test_min_rest_clopening.py`, `test_cost_seniority*.py`,
`test_signal_weights.py`, `test_template_resolver_integration.py`;
`frontend/src/utils/openShifts.test.ts`, `preferenceText.test.ts`,
`scheduleLock.test.ts`, `shiftTime.test.ts`.
