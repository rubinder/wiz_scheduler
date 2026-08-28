# Editing Approved Schedules — Design Spec

Issue: [#84](https://github.com/rubinder/wiz_scheduler/issues/84)
Status: approved, not yet implemented

Part 1 of #84 — viewing an approved schedule in a calendar — shipped as
[#91](https://github.com/rubinder/wiz_scheduler/pull/91). This spec covers part 2: making
approved schedules editable within a month of `created_date`.

## Ships in two stages

The work divides at a natural seam and lands as two PRs.

**Stage 1 — availability holds.** Approving stops destroying availability;
consumption is derived from the shifts themselves. Independently valuable: it
fixes a live bug today, unrelated to editing.

**Stage 2 — the edit path.** Lifts the block at `schedules.py:356` and adds the
warning system. Small once stage 1 exists, because stage 1 removes the reason
editing was unsafe.

---

# The obstacle

Editing an approved schedule is not blocked because nobody built it. It is
blocked because two things make a naive implementation actively wrong.

## The edit endpoint writes to the wrong place

`PUT /schedules/{id}/shifts` (`backend/routers/schedules.py:339`) writes
`schedule.raw_llm_output` and nothing else. That is correct for a draft, where
the JSON blob is the source of truth.

But approving **materialises** that blob into real `Shift` rows, and from then
on every consumer reads the table, not the blob:
`export_schedules.py` (3 query sites), `gdpr.py`, and check-ins via
`employee_check_ins.shift_id`.

So lifting the guard at line 356 alone produces a **silent no-op**: the blob
changes, the manager sees a success response, and nothing they can observe
actually changes.

## Approving destroys availability irreversibly

`_subtract_availability_for_shifts` (`schedules.py:24`), called once at
`:471`, does what its docstring says:

> *"find the availability window that covers it on the same day. **Delete that
> window** and insert replacement windows for any remaining time
> before/after the shift."*

Measured on the seeded demo: an employee with `09:00–17:00` availability who is
scheduled `13:00–21:00` ends the approve with **no availability rows at all**
for that date. Not flagged as used — deleted.

Nothing restores it. So removing a shift frees the shift but not the employee:
they become invisible to `eligible_for_slot` on that date and are silently
never scheduled again until availability is re-entered by hand. Every edit that
removes or shortens a shift would leak a little more of the roster.

**This is already a live bug**, independent of editing. Rejecting a schedule,
or deleting a shift by any means, permanently consumes availability today.

Restoring it after the fact is not viable: the original window was deleted, so
there is nothing to restore *to*. Reconstruction would have to graft a span
back and merge with surviving neighbours, and a wrong merge silently makes
someone available when they are not — a quieter and worse failure than the
leak.

---

# Stage 1 — availability holds

## A shift row IS the hold

No new table. `shifts` already carries `company_id`, `employee_id`, `date`,
`start_time` and `end_time` — everything a hold needs. A dedicated
`employee_availability_holds` table was considered and rejected: every column
would duplicate one already in `shifts`, giving two sources of truth that can
drift, for no capability that is needed today.

**Approve stops mutating availability.** Delete the
`_subtract_availability_for_shifts` call and the function. Approving
materialises `Shift` rows and does nothing else.

**The pipeline subtracts shifts at load time.** `graph.py:462` is the single
place availability is loaded. Alongside that query, load `Shift` rows for the
same company and week, group by employee, and carve them out of each
employee's windows with `_subtract_consumed` — the interval subtraction built
for #85, which already splits a window when a shift sits in the middle and
compares wall-clock faces rather than instants.

Because `graph.py:462` is the only load point, **both scheduling paths inherit
this with no per-path work.**

## Releasing a hold is deleting a shift

There is no separate release operation, so the two cannot disagree. The
non-releasable triggers are enforced by constraints that already exist:

| Trigger | Enforced by |
| --- | --- |
| Employee checked in | `employee_check_ins_shift_id_fkey` — Postgres refuses the delete |
| Edit window closed | The edit endpoint refuses; the shift persists, so the hold persists |

Verified empirically: the FK is `NO ACTION` (`confdeltype = 'a'`), and deleting
a shift with a check-in fails with
`Key (id)=(...) is still referenced from table "employee_check_ins"`.

No flag, no background job, no state machine, nothing to fall out of sync.

## Reassignment falls out for free

Changing `shifts.employee_id` from Alice to Bob moves the hold: Alice's
vanishes because nothing references her for that span, Bob's appears because
his shift now occupies it. Neither requires code. The hold *is* the row.

## Composition with `availability_draft`

These look similar and are not.

- `availability_draft` handles **within one generation run** — stopping
  location B double-booking someone location A just took. It starts empty each
  run (`graph.py:612`).
- Shifts-as-holds handles **across runs** — stopping a regeneration from
  ignoring what was already approved.

They stack: availability arrives already minus committed shifts, then the draft
carves further within the run.

## Existing data

Approved schedules have already carved their availability and that damage is
not recoverable. It also does not double-count: the carved-out span no longer
overlaps the shift being subtracted, so subtracting again is a no-op. No
migration, no backfill.

## Duplicate approved schedules are tolerated

There is no unique constraint on `(location_id, week_start_date)`. The
load-test data holds **32 approved schedules for one location and week**, and a
manager who regenerates and re-approves produces the same.

Holds are unaffected: subtracting the same span twice is a no-op, so
availability stays correct however many overlapping schedules exist. This was
checked specifically, because it would otherwise be a quiet disaster.

Editing *is* affected — which of the 32 is authoritative? That is a product
question (does approving supersede a prior approval for the same location and
week?) and gets **its own issue**. Out of scope here.

---

# Stage 2 — the edit path

## What an edit may do

Change a shift's employee, times, or role; add a shift; remove a shift. Allowed
while the schedule is within a month of `created_date` — the basis specified in
#84.

Edits write to the `shifts` table, not `raw_llm_output`. The blob stays as the
historical record of what was generated.

## Three response classes

**Refused.** The edit does not apply.

- A shift with a check-in — refused outright, not warned. The database already
  refuses it, and the attendance record is factual: rewriting the shift beneath
  it would make the record describe something that never happened.
- Past a month from `created_date` — the schedule is read-only.

**Warned, but applied.** Three warnings, each a structured code so the UI can
style them differently and tests can assert on them:

| Code | Trigger | Meaning |
| --- | --- | --- |
| `no_availability` | No availability window covers the span | A record-keeping gap. The employee may well have agreed verbally. |
| `already_booked` | The employee has another shift **overlapping** the span | A physical conflict — one person, two places. |
| `already_exported` | `shifts.exported_at` is set | The external system (7shifts) now disagrees with the schedule, and nothing re-exports automatically. |

`already_booked` tests **overlap**, not identical times: a 12:00–20:00 shift
genuinely conflicts with 13:00–21:00, and an exact-match test would miss most
real conflicts.

All three are overridable. A manager frequently knows something the
availability table does not — a verbal swap, an emergency cover — and refusing
outright would make the feature useless in exactly the situations it is needed.

**Clean.** No warnings; the edit applies.

## One definition of "free"

Availability for the warning check is computed exactly as the pipeline computes
it: the employee's windows minus their existing shifts, via
`_subtract_consumed`. One definition used by both the scheduler and the editor,
so a hand-edited schedule is judged by the same standard as a generated one.

## Locking

Approve takes `acquire_lock` (`schedules.py:383`) so two managers cannot
approve concurrently. Editing takes the same lock, with the same `LockHeld` →
409 handling. Without it, two managers editing one week silently overwrite each
other.

## Timezone

There is no single blanket rule here — the treatment depends on what kind of
aware datetime is in hand, and conflating the two is exactly the Critical a
fix round during implementation caught (see `_shift_local_face`,
`backend/scheduling/graph.py:246-302`, which documents this distinction in
full):

- **Availability** (`EmployeeAvailability`, and an edit's own incoming
  `start_time`/`end_time` payload) is local wall-clock *falsely tagged* UTC —
  the #61 contract. It is not a true instant, so `.astimezone()`ing it would
  move the face it represents. It stays a plain tag-strip via `_wall_clock`.
- **`Shift` timestamps read back from the database** are true instants stored
  in a `timestamptz` column. Postgres normalises them on storage, so a shift
  written `09:00-04:00` reads back as `13:00+00:00`; stripping the tag at
  that point reads the wrong face. Converting the instant into its own
  location's zone via `.astimezone()` *recovers* the intended face rather
  than moving it, and is required — see `_shift_local_face`.

The two columns are both plain "aware datetime" attributes and look
interchangeable; they are not. Any code path that reads a `Shift` row's
`start_time`/`end_time` back from the database — including stage 2's
`no_availability`/`already_booked` checks over an employee's *other* shifts —
must go through `_shift_local_face`, not `_wall_clock`.

---

# Testing

## Stage 1's equivalence gate

**With no editing, generation output is unchanged.** Approve no longer carves
availability and the pipeline now subtracts shifts instead; the net result must
be identical, or the change has altered scheduling behaviour rather than
relocating where it is computed.

This was originally going to be gated by "removing consumption turns existing
tests red, restoring it turns them green" — the same shape of gate used for
the eligibility extraction in #83. That gate turned out to be **vacuous** for
this change: deleting the consumption subtraction entirely broke zero existing
tests, because no test at the time drove approve → regenerate through a real
database. A red/green check against a suite that doesn't exercise the path
proves nothing.

What actually established equivalence:

- A **Postgres-shaped unit test**, not SQLite. SQLite's `DateTime(timezone=True)`
  columns drop tzinfo on read but preserve the original local digits, so a
  naive tag-strip happens to still return the right face there — SQLite is
  structurally incapable of reproducing the bug. Postgres preserves the aware
  tag and normalises the stored instant to UTC, actually moving the face, so
  only a Postgres-backed test can catch a regression here.
- Targeted tests for the specific behaviours in the Stage 1 list below
  (release-on-delete, hold-survives-check-in, no double-subtraction,
  reassignment moves the hold, malformed-timestamp degrade).
- A live end-to-end run against the real deployment, exercising approve →
  regenerate manually.

Record this plainly rather than the red/green claim: this branch did not meet
that original gate, and the substitute above is what actually backs the
equivalence claim.

## Stage 1

- A released hold reappears as availability: approve, delete the shift,
  regenerate, and the employee is offered those hours again.
- A hold survives a check-in: the delete is refused, so the hold persists.
- Two overlapping approved schedules do not double-subtract.
- Reassigning a shift moves the hold — the old employee is freed, the new one
  committed — with no explicit release step.
- A malformed shift timestamp degrades rather than raising; the scheduling
  graph never throws.

## Stage 2

- Each warning fires on its own trigger and not on the others.
- `already_booked` fires on a partial overlap, not only an exact match.
- A checked-into shift is refused, not warned.
- An edit past the window is refused; one inside it is allowed.
- Two concurrent edits: the second gets 409, not a silent overwrite.
- An edit is visible to `export_schedules.py` and to the calendar — proving it
  wrote to `shifts` and not just the blob.

# Out of scope

- Which of several approved schedules for one location and week is
  authoritative — its own issue.
- Re-exporting to 7shifts after an edit. The `already_exported` warning tells
  the manager; automatic re-export is a separate feature.
- Notifying employees whose shifts changed. Worth deciding later; an explicit
  omission rather than an oversight.
- Restoring availability destroyed by approvals that already happened.
