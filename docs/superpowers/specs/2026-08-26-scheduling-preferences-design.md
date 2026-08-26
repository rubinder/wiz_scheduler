# Weighted Scheduling Preferences — Design Spec

Issue: [#83](https://github.com/rubinder/wiz_scheduler/issues/83)
Status: approved, not yet implemented

Split from the original #83, which also carried approved-schedule editing and
a calendar view. Those moved to [#84](https://github.com/rubinder/wiz_scheduler/issues/84)
— they share no code with this work and ship independently.

## Goal

Three per-employee scheduling preferences, each carrying a weight from 0 to 1
in 0.1 increments:

1. **Day preference** — this employee prefers to work Mondays, Tuesdays and
   Wednesdays.
2. **Hour-range frequency cap** — this employee should work 16:00–22:00 no
   more than 3 times a week.
3. **Hour-range preference** — this employee prefers to work 13:00–17:00.

Weight semantics, matching the existing `employee_affinities.level`:

| Weight | Meaning |
| --- | --- |
| `0` | Carries no weight. The default state: an employee with no row configured. |
| `0.1`–`0.9` | Soft. Strongly considered, but the scheduler will violate it rather than leave a shift unfilled. |
| `1.0` | Hard. Never violated — the shift is left **VACANT** instead. |

`0.7` is the value the UI starts a *newly created* preference at. It is not a
default applied to employees: an employee with no row is at 0 and is unaffected
by this feature entirely. That distinction is what makes the change additive —
no backfill, and no existing schedule changes behaviour until a manager opts a
specific employee in.

## Why the weight semantics are not new

`local_scheduler._affinity_score` already implements exactly this split, and
says so:

> *"Hard constraints (|1.0|) are handled upstream by `_filter_hard_negatives`,
> so this only handles soft preferences and the must-schedule-together bonus."*

Hard weights filter the candidate list; soft weights contribute points to a
score that `_pick_employee` sorts on (`level * 50`, lower is better). This spec
adds three scoring terms to a pipeline already shaped to receive them, rather
than introducing a weighting concept.

## The problem this design has to solve

Two scheduling paths produce shifts, and they honour constraints by completely
different mechanisms:

- **The deterministic path** (`local_scheduler.py`) is code. Weights are exact,
  reproducible and testable.
- **The AI path** (`nodes.py` + `prompts.py`) is an LLM given prose. Testing on
  2026-08-26 found it double-booking a single employee into overlapping shifts
  on *every* run of the seeded demo — the validator caught each one and marked
  the location `CONFLICT`. A component that cannot reliably honour a hard
  constraint it was told about in prose will not reliably distinguish weight
  0.6 from 0.7.

If preferences work on the free deterministic scheduler and fail on the paid AI
scheduler, the value proposition inverts. The design below makes the *hard*
guarantee identical in both paths by construction, and keeps soft weights exact
wherever a scoring function runs.

## Approach: enforce structurally, score deterministically

### Hard weights never reach the model

Both paths already pre-compute the eligible employees for each slot:

- `local_scheduler._build_eligible_map` (line 41) — *"Pre-compute eligible
  employees for each (day, role_name) slot."*
- `prompts.py` (line 151) — *"Build SHIFT REQUIREMENTS with pre-computed
  eligible employees per slot"*, rendered into the prompt under
  `SHIFT REQUIREMENTS (with eligible employees)` with the instruction
  *"FILL EVERY SLOT from the Eligible list."*

These are two independent implementations of "has the role AND is available".
**Extract them into one shared eligibility builder and apply the weight-1.0
filter there.** A candidate removed at that point is invisible to both the
sorting code and the language model. The LLM cannot violate a constraint it was
never offered a candidate for — the guarantee comes from construction, not from
instruction-following.

When no candidate survives the filter, the slot is emitted VACANT. That is the
intended behaviour of a hard preference, not a failure.

### Soft weights: one scoring function, two consumers

```
preference_score(employee, slot, prefs, counts) -> float   # lower is better
```

Same convention and points scale as `_affinity_score`. Each parameter
contributes `weight * PENALTY` when the slot *violates* it.

- **Deterministic mode** — `_pick_employee` folds the score into the `scored`
  list it already builds and sorts, so weights apply across all four strategies
  (`random`, `rotation`, `rotation_history`, `max_hours`).
- **AI mode** — the same function orders each slot's eligible list, best
  candidate first, and the prompt prefers earlier entries. This is close to
  free: the list is already being constructed and rendered.

### Range matching

A shift is compared against a configured hour range by the fraction of the
*shift's* duration that falls inside it. **One threshold, ≥ 50%, used by both
range parameters:**

- An hour-range **preference** is satisfied when at least half the shift falls
  inside the preferred range.
- A shift **counts toward a frequency cap** when at least half of it falls
  inside the capped range.

One named constant, one shared overlap-fraction helper, tested once, and one
sentence to explain in the UI. Both tabs state the same rule, so a manager
learns it once rather than holding two numbers in their head.

### Frequency-cap counting

The cap needs running per-employee, per-range counts for the week. The
deterministic scheduler assembles shifts incrementally and tracks them as it
goes — the pattern `availability_draft` already uses for consumed windows.

**The AI path cannot pre-filter this one.** It generates a whole week in a
single call, so per-slot counts do not exist beforehand. A frequency cap at
weight 1.0 is therefore enforced *after* generation, in
`validate_and_update_availability`, by vacating assignments beyond
`max_per_week`. Day and hour-range preferences at 1.0 have no such problem and
pre-filter cleanly in both modes.

This is a real asymmetry and is stated rather than hidden: two of three
parameters carry an identical structural guarantee in both modes; the third is
structural in deterministic mode and post-hoc in AI mode. The observable
outcome is the same — the cap is never exceeded in a delivered schedule.

## Data model

Three focused tables, following the `EmployeeDayBlackout` precedent
(`backend/models/employee.py:119`) rather than one table with a `kind`
discriminator, which would need `day_of_week` and `max_per_week` nullable for
the kinds that do not use them. This also matches how `employee_affinities`,
`employee_day_blackouts` and `employee_role_minutes` each stay single-purpose.

| Table | Columns beyond `id` / `company_id` / `employee_id` | Unique on |
| --- | --- | --- |
| `employee_day_preferences` | `day_of_week`, `weight` | `(employee_id, day_of_week)` |
| `employee_hour_range_preferences` | `start_time`, `end_time`, `weight` | `(employee_id, start_time, end_time)` |
| `employee_hour_range_caps` | `start_time`, `end_time`, `max_per_week`, `weight` | `(employee_id, start_time, end_time)` |

Shared conventions, all copied from `EmployeeDayBlackout`:

- `String(8)` primary keys via `generate_short_id`.
- `company_id` and `employee_id` are indexed FKs; employee uses
  `ondelete="CASCADE"`.
- `day_of_week` is `SmallInteger` following Python's `datetime.weekday()`
  convention (0 = Monday), with `CheckConstraint("day_of_week BETWEEN 0 AND 6")`.
- `start_time` / `end_time` are `String(5)` `"HH:MM"` — **local wall-clock**,
  never converted. Consistent with the availability write-path contract and
  with blackouts.

The weight column:

```python
weight: Mapped[float] = mapped_column(
    Numeric(2, 1), nullable=False, server_default=text("0.7")
)
# CheckConstraint("weight >= 0 AND weight <= 1")
```

`Numeric(2, 1)` gives exactly one decimal place and the check bounds it to
0–1, so **"0 to 1 in 0.1 increments" is enforced by the database**, not only by
a slider. The `0.7` server default is the create-time starting value; absence
of a row is what means 0.

The unique constraints exist so a manager editing a preference updates it
rather than silently stacking duplicate rows that would each contribute points.

## API

Three REST resources under `/api/v1`, one per table, all `require_manager` and
all filtered by `company_id` per the standing multi-tenancy rule. Pydantic
validates `0 ≤ weight ≤ 1` at one decimal place, mirroring the database check
so malformed input fails at the edge.

**Not plan-gated.** Unlike AI generation and the 7shifts/Deputy importers,
these do not call `assert_paid_plan`. The deterministic scheduler is the free
tier's product, and these preferences make it materially better — gating them
would deliberately weaken the tier they most improve.

## Frontend

### Pages

Three routes, following `HourRestrictions.tsx` (278 lines: local draft rows
plus a name filter). No new page pattern.

Each page adds:

- **A weight slider**, `min=0 max=1 step=0.1`, initialised at **0.7** when a
  row is created. Employees with no row are simply not listed.
- **A visible consequence at 1.0.** When the slider reaches 1, the row warns
  that shifts may be left VACANT if no preferred employee is free. A manager
  has to see that where the choice is made, not discover it in the next
  schedule.
- **The matching rule, stated in the tab** — "a shift counts when at least 50%
  of it falls inside this range." The same sentence on both range tabs.

### Sidebar

`Sidebar.tsx` is 93 lines of flat `NavItem[]` — `baseManagerLinks` (12) plus
`postEmployeeManagerLinks` (8), joined conditionally on `hasEmployees`. Twenty
items today; these three would make twenty-three.

`NavItem` gains an optional `children`. A parent renders as a clickable row
that expands an indented list. The group containing the active route
auto-expands, so a deep link never lands in a collapsed tree. The
`hasEmployees` gating carries over unchanged — it hides children instead of
flat rows.

```
Dashboard
Organization ▸      Company · Team · Regions · Locations · Special Hours
Roles ▸             Roles · Role Equivalents
People ▸            Employees · Onboarding · Availability
Scheduling rules ▸  Association · Hour Restrictions · Day Blackouts
                    · Day Preferences ★ · Hour Range Preferences ★ · Frequency Caps ★
Scheduling ▸        Shift Templates · Schedule · Export
Check-in ▸          QR · Report
Data Privacy
```

Nine top-level rows instead of twenty. The new preferences sit beside the
existing per-employee constraint pages rather than in a separate silo.

**This regrouping ships as its own PR, before the preferences feature.** It
touches every manager's navigation and is unrelated to scheduling; reviewed
alone it is a self-contained nav change, and the preferences PR then adds three
rows to a group that already exists.

### i18n

`LanguageContext.tsx` types translations as `Record<Language, Translations>`
derived from `en.ts`. **A key added to `en.ts` alone fails the TypeScript
build.** All 19 locale files (`ar bn de en es fr hi id ja mr pcm pt ru ta te tr
ur vi zh`) must carry every new key — the group labels in the nav PR, and the
page strings in the feature PR.

## Testing

**The no-op regression is the most important test.** With zero preference rows,
every schedule must be identical to today. This change touches `_pick_employee`,
which all four strategies run through, so this is the safety net for the whole
feature — and it is cheap to guarantee, because absent rows contribute nothing.
`test_local_scheduler.py` is the place for it.

- **Threshold boundaries** — a shift exactly 50% inside a range matches; 49.9%
  does not. Tested once on the shared helper, then exercised through both the
  preference and the cap. Fraction thresholds are where off-by-one bugs live.
- **Hard filter** — weight 1.0 removes the candidate, and a slot with no
  surviving candidate is emitted VACANT rather than raising. Asserted
  explicitly, because "leaves a hole in the schedule" is correct-but-alarming
  behaviour that someone will otherwise mistake for a bug and "fix".
- **A structural guard that both paths use the shared eligibility builder**, in
  the style of `tests/test_scheduling_model.py` — failing if `prompts.py` or
  `local_scheduler.py` grows a private copy again. That duplication is what
  made this feature awkward; a test keeps it from returning.
- **Frequency-cap counting** in both modes, including the AI case where the
  model exceeds `max_per_week` and the validator has to trim.
- **Multi-tenancy** — a preference must never influence another company's
  schedule.
- **Database constraints** — weight outside 0–1 rejected, two decimal places
  rejected by `Numeric(2, 1)`, duplicate rows rejected by the unique
  constraints.

Error handling follows the pipeline's existing contract: degrade to VACANT,
never raise. Over-constraining is possible — day preference 1.0 combined with
hour-range preference 1.0 can leave an employee unschedulable — and surfaces as
unfilled shifts plus the UI's 1.0 warning, not an exception.

## Out of scope

- Approved-schedule editing and the calendar view — [#84](https://github.com/rubinder/wiz_scheduler/issues/84).
- Employee self-service for these preferences. They are manager-set, consistent
  with `HourRestrictions` and `DayBlackouts`.
- Per-role or per-location preference defaults. Per-employee only.
- Any change to `employee_affinities`, which already works.
