# Cost & Seniority Scheduling Parameters — Design Spec

Issue: [#134](https://github.com/rubinder/wiz_scheduler/issues/134)
Status: proposed

## Goal

Three new per-employee/per-company signals that both scheduling paths can use
when picking who fills a slot, each **nullable and opt-in** — a manager who
sets nothing gets byte-identical scheduling behavior to today:

1. **Pay rate** (`Employee.pay_rate`, $/hr) — a soft "prefer cheaper" nudge.
2. **Overtime avoidance** — a configurable per-week hours threshold
   (`overtime_threshold_hours`, company default overridable per location) past
   which assigning an employee carries a soft penalty.
3. **Seniority** — two-part: automatic tenure from `Employee.hire_date`, or an
   explicit manager-set `Employee.seniority_rank` that takes precedence when
   set. A soft "prefer more senior, all else equal" nudge.

**Pay rate and overtime are paid-plan only. Seniority is free.** This follows
the existing `assert_paid_plan` pattern (`backend/services/plan.py`,
used by `backend/routers/payroll.py`) rather than adding a new gating
mechanism, and gates at the **write** endpoints only — a free-plan company
can never populate `pay_rate` or an overtime threshold in the first place, so
the "NULL = no behavior change" guarantee doubles as the plan boundary with
no separate runtime check needed in the scheduler itself. This mirrors how
`backend/routers/scheduling_preferences.py` documents the opposite case
in its docstring: *"these endpoints must NOT call assert_paid_plan"* — free
day/hour preferences vs. paid cost/overtime is a deliberate, explicit split
at the router layer, not a drift.

## Why this reuses the existing preference machinery

`backend/scheduling/preferences.py` already holds the one soft-scoring
function (`preference_score`) shared by both scheduling paths:

- The deterministic path (`local_scheduler._pick_employee`) folds it into the
  `score` it sorts every strategy on.
- The AI path (`prompts.py`) uses the same function to order each slot's
  `Eligible` list, best candidate first.

This spec adds three more scoring terms next to it rather than inventing a
second weighting concept — see
[`2026-08-26-scheduling-preferences-design.md`](2026-08-26-scheduling-preferences-design.md)
for the precedent this follows.

**One deliberate departure from that precedent:** day/hour preferences never
expose their weight or score to the AI prompt text — they only reorder the
`Eligible` list, and the model is told to "prefer earlier entries." Pay rate,
overtime standing, and seniority are numbers a manager already reasons about
directly, so this design renders them as explicit numbers in the prompt (a
0–1 relative cost score, hours-committed-vs-threshold, and a seniority rank)
in addition to using them to order the list. This is a deliberate choice
made in brainstorming, not an oversight — flagging it here so a future reader
doesn't "fix" the inconsistency.

## Approach

### Cost score — relative, never raw dollars in the AI prompt

Per-slot, min-max normalized among that slot's *eligible* candidates only:

```
cost_score(emp) = (pay_rate - min_rate_among_eligible) / (max_rate_among_eligible - min_rate_among_eligible)
```

0.0 = cheapest eligible candidate for this slot, 1.0 = most expensive. All
candidates score 0.5 when every eligible candidate shares the same rate
(avoids divide-by-zero, and correctly signals "no discriminating information
here"). An employee with `pay_rate IS NULL` is excluded from the normalization
range and contributes **`0.5`** to their own score — not `0.0` as an earlier
draft of this spec said. `0.5` is the neutral midpoint already used for "no
discriminating information"; `0.0` is the *cheapest* end of the scale, so
using it for "no data" made an unrated employee look artificially cheap
(during partial rollout) and penalized the one employee who *did* have a rate
entered in an otherwise-unrated pool — the opposite of this paragraph's own
stated intent. Pay rate is opt-in per employee too, not just per company, and
an employee nobody entered a rate for should never look artificially
expensive *or* artificially cheap.

This was explicitly chosen over sending raw `pay_rate` dollars to the LLM,
after weighing it in brainstorming: a per-slot relative rank gives the model
exactly what it needs ("who's cheaper among these candidates") without wage
data leaving the backend in the AI path. The deterministic path never talks
to an LLM at all, so this normalization is purely a scoring convenience
there, not a privacy boundary.

### Overtime score

Both paths already track an employee's hours committed so far this
scheduling run:

- Deterministic: `employee_hours` (param already threaded through
  `_pick_employee`).
- AI: `SchedulingState.employee_weekly_hours_draft`, threaded across
  locations exactly like `range_counts_draft` in the preferences design.

```
resolved_threshold = location.overtime_threshold_hours
                      or company.overtime_threshold_hours
                      or None   # NOT a 40.0 fallback — see below
```

**Corrected post-implementation (a final-review finding, 2026-10-01): the
threshold must resolve to `None`, not a 40.0 default, when neither level sets
one.** The original draft of this section had `resolved_threshold` fall back
to `DEFAULT_OVERTIME_THRESHOLD_HOURS` (40.0) when both levels were unset. That
directly contradicted this spec's own Goal section ("a manager who sets
nothing gets byte-identical scheduling behavior to today"): a silent 40h
fallback makes overtime scoring active for *every* tenant, including ones who
configured nothing, the moment any employee's hours (within one location, or
carried across locations in one run) exceed 40. `resolve_overtime_threshold`
returns `float | None`; `None` means overtime scoring is skipped entirely for
that location (the `overtime_score` term contributes `0.0`, unconditionally,
not `0.0`-via-a-40h-comparison). `DEFAULT_OVERTIME_THRESHOLD_HOURS` survives
only as placeholder text in the Company/Location settings UI, suggesting a
starting value — it is never read as a runtime fallback.

`overtime_score(emp)` returns a penalty proportional to how far
`employee_hours[eid] + shift_duration_hrs` would land past `resolved_threshold`
if this candidate is chosen, **only when `resolved_threshold` is not `None`**
— 0 when the projected total stays at or under threshold, and the term is
skipped entirely (not computed against an implicit default) when no threshold
is configured. This is a **soft** nudge exactly like preference weights: the
scheduler may still push someone into overtime when no alternative covers the
slot. `overtime_premium_multiplier` (same company/location override shape,
default 1.5) is stored now for the payroll/reporting side to read later, but
does **not** change the score itself — the size of the premium doesn't make
working the shift more or less desirable from a coverage standpoint, only
more or less expensive after the fact.

### Seniority score

```
resolved_rank(emp) = emp.seniority_rank if emp.seniority_rank is not None
                      else rank_by(emp.hire_date)  # earlier hire_date = lower rank number = more senior
```

**Corrected post-implementation (a final-review finding, 2026-10-01): a
manual `seniority_rank` must only claim its own number, not outrank every
hire-date-derived employee.** Derived ranks are assigned from whichever
positive integers no manual rank already claims (in hire_date order), so a
manual `seniority_rank=5` leaves ranks 1-4 open for the four most-senior
derived employees — matching the natural reading "5th most senior" — rather
than an earlier draft's behavior of starting every derived rank right after
the single highest manual rank in the pool, which made any manual rank
outrank every derived employee regardless of actual hire date.

Employees with neither field set are excluded from ranking and contribute a
neutral **`0.5`** — not `0.0` as an earlier draft of this spec said, for the
same reason cost's neutral value was corrected above: `0.0` is the *most
senior* end of the scale, so using it for "no data" made an unranked
employee look artificially senior. `seniority_score(emp)` min-max normalizes
`resolved_rank` across the *slot's eligible pool* (0.0 = most senior present,
1.0 = least), mirroring the cost score's normalization for consistency, and
is added as a small-weight term — a tie-break, not a dominant factor,
matching how skill_level already works
(`-e.get("_skill", 0)` in `_pick_employee`).

### New shared module: `backend/scheduling/cost_seniority.py`

Keeps `preferences.py` focused on preferences (matching how
`employee_affinities`, `employee_day_blackouts`, etc. each stay
single-purpose per the existing convention). Exposes:

```python
def resolve_overtime_threshold(company, location) -> float: ...
def resolve_overtime_multiplier(company, location) -> float: ...
def cost_score(emp: dict, eligible_pool: list[dict]) -> float: ...
def overtime_score(emp: dict, hours_committed: float, shift_duration_hrs: float, threshold: float) -> float: ...
def seniority_score(emp: dict, eligible_pool: list[dict]) -> float: ...
```

`_pick_employee` adds these three terms to its existing `score` calculation
in every strategy branch, exactly where `aff` and `pref` are added today.
`prompts.py`'s eligible-list ordering adds the same three terms, and its
per-candidate rendering gains `cost=0.xx`, `hours_committed=X/threshold=Y`,
and `seniority_rank=N` — visible only when the underlying data is non-null
for at least one candidate in that slot, so a company using none of this
sees prompt text identical to today.

## Data model

All new columns nullable, no default beyond NULL — the "manager didn't opt
in" state:

| Table | New columns |
| --- | --- |
| `employees` | `pay_rate` (`Numeric(8,2)`, nullable), `hire_date` (`Date`, nullable), `seniority_rank` (`SmallInteger`, nullable, `CheckConstraint("seniority_rank > 0")`) |
| `companies` | `overtime_threshold_hours` (`Float`, nullable), `overtime_premium_multiplier` (`Float`, nullable, `CheckConstraint("overtime_premium_multiplier >= 1")`) |
| `locations` | `overtime_threshold_hours` (`Float`, nullable, overrides company), `overtime_premium_multiplier` (`Float`, nullable, overrides company) |

One Alembic revision, `op.add_column` with `nullable=True` on all six
columns — no backfill, following the pattern of recent migrations that add
nullable columns to existing tables.

## API

- `PATCH /api/v1/employees/{id}`: accepts `pay_rate` only when the company is
  on a paid plan (`assert_paid_plan(db, company_id, "cost_aware_scheduling")`,
  mirroring `payroll.py`); `hire_date` and `seniority_rank` accepted
  unconditionally, matching the free-tier day/hour-preference precedent.
- `PATCH /api/v1/companies/{id}` and `PATCH /api/v1/locations/{id}`: accept
  `overtime_threshold_hours` / `overtime_premium_multiplier` only under the
  same `assert_paid_plan` gate.
- All four fields are otherwise ordinary nullable columns on existing
  resources — no new REST resource needed, unlike the three dedicated
  preference tables (which needed independent uniqueness/multiplicity per
  employee; these are single scalar fields per row).
- Pydantic validates `pay_rate >= 0`, `seniority_rank > 0`,
  `overtime_threshold_hours > 0`, `overtime_premium_multiplier >= 1` at the
  edge, mirroring the database checks.

## Frontend

- **Employee edit form** (manager pages): new fields for `pay_rate` (shown
  disabled with the standard `createDisabledReason`-style paid-plan message
  on a free plan, following `DataTable`'s existing disabled-with-reason
  pattern), `hire_date` (date picker), `seniority_rank` (optional integer,
  labeled "manual override — leave blank to rank by hire date").
- **Company / Location settings pages**: `overtime_threshold_hours` and
  `overtime_premium_multiplier`, each showing the resolved smart default
  (40 hrs / 1.5x) as placeholder text when NULL, and paid-plan gated the same
  way as the employee pay rate field.
- No sidebar changes — these live on existing pages, not new routes.
- i18n: new field labels and the paid-plan disabled-reason string need all 19
  locale files, per the standing `LanguageContext` requirement.

## Testing

- **No-op regression**: with every new field NULL (including no overtime
  threshold configured anywhere), both schedulers produce byte-identical
  output to today — **this must hold even when an employee's hours exceed
  40 within a location or across locations in one run**, since
  `resolve_overtime_threshold` returns `None`, not a 40.0 fallback, in that
  case. This is the load-bearing test, same reasoning as the preferences
  spec's no-op test — `cost_score` and `seniority_score` must each
  independently return `0.5` (neutral, not `0.0`) for an all-NULL candidate
  pool, and `overtime_score`'s term must be skipped entirely (contributing
  `0.0` to the total score unconditionally) whenever the resolved threshold
  is `None`, never computed against an implicit default.
- **Normalization edge cases**: single eligible candidate (score 0.5, no
  divide-by-zero), all-equal rates, one candidate with `pay_rate` set and
  others NULL (the NULL candidates score `0.5`, neutral — not `0.0`).
- **Overtime threshold resolution**: location override beats company
  default beats `None` (never a 40.0 fallback — the 40.0 constant is a UI
  placeholder only); same shape test for the multiplier, which does fall
  back to its 1.5x constant since it's never used to gate scoring, only
  stored for payroll's future use.
- **Seniority resolution**: manual `seniority_rank` beats `hire_date`
  derivation; an employee with neither is excluded from ranking, not
  treated as least senior.
- **Plan gating**: a free-plan `PATCH` attempting to set `pay_rate` or either
  overtime field is rejected; `hire_date`/`seniority_rank` succeed on free.
- **Both paths**: mirror the preferences spec's dual-path testing — same
  scoring behavior asserted through `local_scheduler` directly and through
  the rendered AI prompt text (candidate ordering and the new inline
  numbers).
- **Multi-tenancy**: unchanged behavior for cross-company data, per standard
  convention.

Error handling follows the pipeline's existing contract: these are inputs
computed in Python before the LLM call, not LLM output, so no new parsing
path is introduced; a missing/NULL field degrades to a neutral `0.5`
contribution for cost/seniority (or no contribution at all for overtime,
when no threshold is configured), never an exception. **A final-review
finding also surfaced that `Employee.pay_rate` loads from its `Numeric(8,2)`
column as `decimal.Decimal`, not `float`, which raised `TypeError` inside
`cost_score`'s arithmetic** — the state-construction boundary in
`graph.py` now coerces to `float` explicitly, and `cost_score` coerces
defensively too, since this class of bug is invisible to `==`-based test
assertions (`Decimal("24.50") == 24.50` is `True`).

## Out of scope

- Holiday/special-date overtime multipliers — stays a manual manager
  adjustment outside this feature, per explicit decision in brainstorming.
- True payroll-cost-minimization as a hard scheduling objective — this
  remains a soft nudge; the scheduler can still choose a more expensive or
  over-threshold candidate when coverage requires it.
- Any payroll/reporting UI that consumes `overtime_premium_multiplier` for
  actual pay calculation — that's `backend/services/` payroll territory
  ([#78](https://github.com/rubinder/wiz_scheduler/issues/78) /
  PR #127), this spec only stores the field for that future consumer.
- Runtime (schedule-generation-time) plan re-checking — write-time gating
  only, matching existing precedent elsewhere in the codebase.
