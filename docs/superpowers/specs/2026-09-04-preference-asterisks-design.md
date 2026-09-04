# Preference Asterisks — Design Spec

Issue: [#99](https://github.com/rubinder/wiz_scheduler/issues/99)
Status: implemented

Builds on [#83](https://github.com/rubinder/wiz_scheduler/issues/83)
(the preference model and evaluator this reads) and
[#84](https://github.com/rubinder/wiz_scheduler/issues/84) (hand-edited
shifts, which need the same evaluation).

## Goal

When the scheduler assigns a shift that contradicts an employee's soft
preference, nothing says so today. The schedule looks clean, and a manager
cannot tell a shift that fit everyone's preferences from one that overrode
three of them.

Three outputs:

1. **An asterisk** on every shift scheduled against a soft preference, in
   both the post-generation review grid and the approved-schedule grid.
2. **An explanation on hover or focus** naming the specific preference in
   words: "prefers Mon, Tue, Wed", "prefers 16:00–22:00", "already at 3×
   this week for 16:00–22:00".
3. **A staffing signal** per location: whether any of those shifts could
   not have been staffed without overriding *someone's* preference. That is
   the only honest evidence the roster is too thin, and it is distinct from
   VACANT (which means the slot could not be staffed at all).

Display and analysis only. Nothing about how shifts are assigned changes.

## Approach: evaluate the finished schedule, don't ask the model

Generation stays exactly as it is. After a location's shifts exist, a
rules-based pass over them decides which preferences were scheduled
against. A self-reported explanation from the model would describe what
the model *says* it did; the rules pass describes what the schedule *is*,
which stays true when the model is wrong — and the manager is looking at
the asterisk precisely to check the model's work.

The same pass covers the deterministic `local_scheduler` path and
hand-edited shifts, so all three ways a shift can come to exist are
evaluated by one piece of code.

### The evaluator already exists

`backend/scheduling/preferences.py` already computes the violated
preference objects on every eligibility check:

- `_day_violated(emp, day_index)` — day preferences the slot contradicts,
  collapsed to at most one carrying the strongest weight
- `_range_violated(emp, start, end)` — hour-range preferences, same set logic
- `_caps_exceeded(emp, start, end, range_counts)` — caps whose weekly
  allowance the slot exceeds

All three are weight-agnostic. `blocked_by_hard_preference` composes them
and discards everything below weight 1.0; `preference_score` composes them
again and sums the rest into a penalty. The soft violations are computed
and thrown away twice per check. This feature stops throwing them away.

Reusing these functions is what keeps the hover text from disagreeing with
the scheduler: a second evaluator would drift the first time either
changed, and the asterisk would start explaining a decision that was not
made.

### Store, don't derive

Violations are computed once, at generation, and persisted. Reads
dominate: a manager opens an approved week far more often than anyone
edits one. With a persisted column the approved calendar costs nothing
extra per read, whereas deriving would add three preference queries and a
pass over every shift on every `GET /schedules/week/…`. Generation already
has the preferences and the shifts in memory, so computing there is nearly
free. The only recurring cost is recomputing one employee's week after a
hand-edit, which is small and rare.

Consequence to state plainly: the asterisks reflect the preferences as
they stood at the last write, not the preferences as they stand today. A
manager who changes an employee's preference after approval does not see
the approved calendar re-evaluate. That matches what the asterisk means —
"the scheduler overrode a preference" — and avoids a calendar whose marks
shift under a manager who changed nothing on it.

## Backend

### `preferences.py`

`violations_for_slot(emp, day_index, start, end, range_counts) -> List[dict]`
becomes the single public composition of the three checks.
`blocked_by_hard_preference` and `preference_score` both call it, so there
is one composition rather than the two that exist today. Byte-identical
behaviour for both callers.

`annotate_preference_violations(shifts, employee_preferences, seed_counts) -> None`
walks the "ok" shifts in `(date, start_time)` order — the same order
`_trim_cap_violations` uses, so cap counting agrees with the trim pass —
keeps a per-`(employee_id, cap_start, cap_end)` running count seeded from
`seed_counts`, and sets `shift["preference_violations"]` on every "ok"
shift to the list of soft violations, each as:

```python
{
  "kind": "day" | "hour_range" | "cap",
  "weight": 0.7,
  # kind == "day":       "days": [0, 1, 2]           (the whole preferred set)
  # kind == "hour_range": "start_time": "16:00", "end_time": "22:00"
  # kind == "cap":        "start_time", "end_time", "max_per_week": 3
}
```

Only weights below 1.0 are reported. Hard violations never reach this pass
because `_trim_cap_violations` and `_trim_hard_preference_violations` have
already vacated them; a defensive filter keeps them out regardless.

The day kind carries the whole preferred set, not the one collapsed row
`_day_violated` returns, because "prefers Mon, Tue, Wed" is the sentence a
manager needs and the collapsed row only knows one day. The count is
incremented for a cap only when the shift stays "ok", matching the trim
pass's decide-then-commit rule.

VACANT and CONFLICT shifts get an empty list. Shifts with no preferences
configured get an empty list. Malformed shifts or preference rows are
skipped rather than raising, per the graph's degrade-don't-raise contract.

### Graph: `annotate_preferences` node

A new node between `validate_and_update_availability` and `emit_result` on
both the local and AI paths. On the AI path it sits after the
retry-or-emit decision, so it runs once per emitted location, not per
attempt.

It calls the annotator with the `range_counts_draft` **as it stood before
this location's shifts were folded in**. `validate_and_update_availability`
grows the draft with this location's committed shifts, so it additionally
returns `range_counts_before` — a copy taken on entry, added to
`SchedulingState` — and the annotator seeds from that. A cap is then
counted across locations exactly the way generation counted it.

For the staffing signal it re-derives eligibility per violating shift:
`eligible_for_slot` over the prepared employees for the slot's
`(day, role, start, end)`, then filters candidates to those whose
`violations_for_slot` for that slot is empty, whose availability draft has
no overlapping window, and who are not the assigned employee. A violation
is `unavoidable` when that filtered set is empty. This re-derivation was
chosen over recording the candidate set during generation because it keeps
the pass self-contained and covers both generation paths with one
implementation.

The node emits on the `LocationResult`:

```python
"preference_summary": {
  "shifts_against_preference": 4,
  "unavoidable": 1,
  "roster_thin": True,
}
```

`roster_thin` is `unavoidable >= 1`. No threshold: one shift that could not
be staffed cleanly is a fact, and the two counts let the manager judge the
scale. Every violation dict additionally carries `"unavoidable": bool`.

The node never raises. On any failure it leaves the shifts un-annotated
and the summary absent, and logs at warning level.

### Persistence

Migration `0034_add_preference_violations`:

- `shifts.preference_violations` — `JSON`, nullable. NULL and `[]` both
  render as "no asterisk"; NULL is the state of every pre-existing row.
- `shift_schedules.preference_summary` — `JSON`, nullable.

Write sites:

| Site | What happens |
| --- | --- |
| `POST /schedules/generate` | Stores `preference_summary` on the draft `ShiftSchedule` row. Violations ride inside `raw_llm_output` with the shifts, as every other shift field does. |
| `POST /{id}/approve` | Copies each shift's `preference_violations` from the draft JSON onto its new `Shift` row. |
| `PUT /{id}/shifts` (draft edit) | `ShiftUpdate` gains `preference_violations: list[dict] = []` so the field round-trips. The route re-annotates the posted list server-side (loading the company's preferences) before storing, and returns the annotated list so the review page can replace its local copy. |
| `PUT /{id}/approved-shifts` | After applying edits, re-annotates every approved shift that week for each employee touched by an edit (old and new employee of a reassignment), across all of the week's approved schedules so cross-location caps count, and rewrites their column. |

`preference_summary` is a generation-time observation and is not
recomputed on edit. The approved-edit response does not touch it, and the
frontend banner on the approved page says "at generation".

### API

- `ShiftAssignment` (TypedDict) gains `preference_violations: List[dict]`
  (optional, so `local_scheduler` and existing callers keep working).
- `ShiftResponse` gains `preference_violations: list[dict] = []`.
- `LocationResult` and `ShiftScheduleResponse` gain
  `preference_summary: dict | None = None`.

## Frontend

### `ScheduleGrid`

When `shift.preference_violations` is non-empty, an asterisk renders after
the employee name. It is a `<button type="button">` — focusable — with
`aria-describedby` pointing at a tooltip element that is visible on
`group-hover` and `focus-within`, so keyboard and touch reach it. Tablets
are where managers actually use this page; an asterisk whose meaning is
mouse-only would be decoration there.

The tooltip lists each violation on its own line, built from i18n
templates:

| kind | template |
| --- | --- |
| day | `prefers {days}` — day names from a short-weekday list, joined with ", " |
| hour_range | `prefers {start}–{end}` |
| cap | `already at {n}× this week for {start}–{end}` |

Times are the stored `HH:MM` strings sliced with `fmtHM`, never through
`Date` — the same category error #92 fixed in this component. An
`unavoidable` violation gets a short suffix: "no one else was free".

Logical direction utilities only (`start-0`, `ms-1`); the RTL guard test
enforces this.

### Banner

Both the review page (`Schedule.tsx`, from the streamed `LocationResult`)
and the approved page (`ApprovedSchedules.tsx`, from
`ShiftScheduleResponse.preference_summary`) render a banner above the grid
when `roster_thin` is true:

> {unavoidable} of {shifts_against_preference} shifts scheduled against a
> preference had no one else free. You may need more staff to honor
> preferences and stay covered.

The approved page appends "(at generation)". No banner when the summary is
absent or `roster_thin` is false.

### Draft edits

After `handleSaveShift`, the review page calls `PUT /{id}/shifts` with the
edited list and replaces its local copy with the annotated response, so
the asterisk on a hand-edited shift is correct immediately. This changes
the current behaviour where draft edits are held locally until approval;
the save already happens before approve, so this moves it earlier rather
than adding a new write.

### i18n

New keys under `schedule.` in every locale file, English text in all of
them, matching how the other recently added blocks ship. Translation is a
separate pass.

## Testing

Backend:

- `violations_for_slot` returns the same set `blocked_by_hard_preference`
  and `preference_score` acted on before the refactor (existing tests in
  `test_preference_scoring.py` stay green unchanged).
- Annotator: one test per kind; the day kind reports the whole preferred
  set; a cap's fourth shift is marked and the first three are not; the
  cap count is seeded from `seed_counts` so a cross-location fourth shift
  is caught; a weight-1.0 preference is never reported; VACANT, CONFLICT
  and no-preference shifts get `[]`; a malformed shift is skipped and the
  rest are annotated.
- Node: a violating shift reaches `emit_result` annotated on both paths;
  a slot with a clean alternative available is not `unavoidable`; a slot
  where every eligible candidate would violate something is; the summary
  counts match the shifts; a failure inside the node leaves the result
  usable.
- Router: approval copies the column; a draft edit round-trips and
  re-annotates; an approved reassignment rewrites both employees' shifts;
  a pre-existing NULL column serialises as `[]`.
- The existing invariant tests for the trim passes and the abuse/UTC AST
  sweeps stay green.

Frontend:

- Grid renders the asterisk and tooltip text for an annotated shift, and
  nothing for `[]` or absent.
- The RTL logical-direction guard stays green.

## Out of scope

- Any change to how shifts are assigned. Hard preferences already block
  and emit VACANT; this is strictly about making soft trade-offs visible.
- Re-evaluating persisted asterisks when a preference is later edited.
- Recomputing `preference_summary` after hand-edits.
- Translating the new strings.
- New preference kinds.
