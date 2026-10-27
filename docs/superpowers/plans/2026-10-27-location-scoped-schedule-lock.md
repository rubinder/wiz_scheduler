# Location-scoped Schedule Lock (#180) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Two managers scheduling locations that share no active employee no longer block each other. Operations whose locations share an employee still conflict (409 `schedule_locked`, now naming the busy location).

**Architecture:** `schedule_locks` changes from one row per company to one row per `(company_id, location_id)`. Each operation computes a lock set: the locations it touches, plus every location that shares an active employee with them. It then inserts one row per location in sorted order, in one transaction, grouped by a `lock_group` id so the whole set can be released at once. Serialisation still relies on the unique index blocking a concurrent inserter until the first transaction finishes; sorted insertion order prevents deadlocks. The whole-company quota checks, which the company lock used to serialise, are re-checked at write time under the ownership-group row lock.

**Spec:** GitHub issue #180 (`/private/tmp/claude-501/-Users-robran-IdeaProjects-wiz-scheduler/dcbaac4c-97e6-440f-8714-613f304bf874/scratchpad/s180/spec-issue-180.md`). Code map: `/private/tmp/claude-501/-Users-robran-IdeaProjects-wiz-scheduler/dcbaac4c-97e6-440f-8714-613f304bf874/scratchpad/s180/survey.md`. Read both.

## Global Constraints
- The 409 response shape is unchanged: `{"code": "schedule_locked", "locked_by", "expires_at"}`. It gains `location_id` and `location_name`, so the frontend's `ScheduleLockedError` keeps working.
- TTLs are unchanged (config `SCHEDULE_LOCK_TTL_*`). Expired rows are still swept on acquire.
- Archived employees never widen a lock set (`services/archive.active`). Employee `location_ids` is a JSON column, so filter in Python as the rest of the codebase does.
- Migration `0054` (down_revision `"0053"`, which is #189's migration):
  - delete the existing rows (they are short-lived)
  - drop `uq_schedule_locks_company_id`
  - add `location_id String(8) NOT NULL` FK `locations.id` ON DELETE CASCADE, plus `lock_group String(8) NOT NULL`
  - add `UNIQUE(company_id, location_id)` named `uq_schedule_locks_company_location`
  - add an index on `lock_group`
  - RLS already covers the table (0044), so no RLS change.
- No new dependencies. Type hints. "Today" is UTC.
- Concurrency tests run against Postgres with separate sessions, not the shared `client` session. #189 added an `isolated_client` pattern in `tests/test_preference_submissions_review_integration.py`; reuse or generalise it into `tests/conftest.py`.

## Lock set rules
- `expand_lock_set(db, company_id, seed: set[str] | None) -> list[str]`:
  - When seed is None ("whole company"): every location of the company, archived included. The result is sorted.
  - Otherwise: the seed, plus every `location_id` of every active employee whose `location_ids` intersect the seed. One hop, not transitive: the issue accepts the neighbour scheme and its false positives. Sorted.
- **generate** seed:
  - Resolve the locations the pipeline will run, from the templates, exactly as `graph.py:604-627` does: `template_ids` if given (after `_scoped_template_ids`), else all recurring templates (restricted to scope for a regular manager).
  - Factor that resolution into one shared helper used by both the router and the graph, so the two can't drift.
  - Generate without `template_ids`/`location_ids` from an admin means the seed is None, which locks the whole company (acceptance criterion 3).
- **approve** seed: `{schedule.location_id}`.
- **edit_approved** seed: `{schedule.location_id}`, plus every `location_id` named in the edits, plus the locations of every employee being assigned.

## Acquire and release (`services/schedule_lock.py`)
- `acquire(db, *, company_id, user_id, operation, location_ids: list[str], ttl_seconds=None) -> LockHandle`:
  - `LockHandle` is a small dataclass `{group: str, location_ids: list[str], expires_at}`.
  - Steps:
    1. Sweep expired rows for the company.
    2. Select live rows for the company with `location_id IN set`. If there are any, raise `LockHeld(name, expires_at, location_id, location_name)` naming the first conflicting location in sorted order.
    3. Insert rows in sorted `location_id` order, each in its own `begin_nested` + flush. On IntegrityError, re-select the holder of that location. If the holder has vanished (released meanwhile), retry the insert once; then raise LockHeld with the "another manager" fallback, never a 500.
  - `location_ids` must be non-empty. With an empty seed (no templates), callers skip locking entirely, as generate already returns an empty stream.
- `release(db, group: str)` deletes rows by `lock_group`. It is idempotent.
- `LockHeld` gains `location_id: str | None` and `location_name: str | None`. The holder name lookup is unchanged.

## Races the company lock used to cover
- **Free-plan location cap, demo pooled count, paid row count:** in generate's per-location write (`event_stream`, before inserting `ShiftSchedule`):
  - Take `SELECT ... FROM ownership_groups WHERE id = :og FOR UPDATE`.
  - Re-run the per-location check (`resolve_location_quota` for that one location and week, or a narrower helper) inside the same transaction as the insert.
  - If the check now fails, skip the insert and emit that location's NDJSON result with the same status the pipeline uses for quota exhaustion (`QUOTA_EXCEEDED`).
  - `deduct_credits_for_schedule_overage` already locks the ownership group FOR UPDATE; take the lock once and reuse it.
  - Keep the route-level pre-checks as they are; they are a fast path.
- **Pool widened mid-run:** before each per-location write in generate, recompute `expand_lock_set` for that location.
  - If it now includes locations not in the handle, try to acquire them under the same group.
  - On LockHeld, don't write that location's schedule; emit a `CONFLICT` result for it with a clear message.
  - Do NOT add locking to the employee-assignment paths. Note this decision in the PR.
- **AI burst cap and the daily cost breaker:** these stay per company and are unchanged.
- **`lock_employees` paths:** confirm (by reading the code) that archive and assignment paths need nothing from the schedule lock, and say so in the report.

## Frontend
- The 409 detail's `location_name` shows in the toast and the edit modal: "{name} is scheduling {location} until …". Fall back to the current text when it's absent.
- Update `approvedSchedules.scheduleLockedBody` (and the Schedule.tsx toast string) in all 19 locales with real translations.
- Add a new key, e.g. `scheduleLockedBodyLocation` with `{name}` and `{location}`, rather than changing the existing key's placeholders.

## Tasks

### Task 1: Lock model, migration, service
**Files:**
- `backend/models/schedule_lock.py`
- `backend/alembic/versions/0054_location_scoped_schedule_locks.py`
- `backend/services/schedule_lock.py`
- `tests/test_schedule_lock_integration.py` (update)
- `tests/test_models_team_and_locking_integration.py` (update the uniqueness test)

**Tests (write first):**
- disjoint sets acquire independently
- an overlapping set raises LockHeld naming the location
- expired rows are swept
- release removes the whole group and is idempotent
- the TTL tests still pass
- `expand_lock_set`:
  - the seed plus the locations of employees sharing it
  - archived employees ignored
  - None means all locations
  - the result is sorted
- the vanished-holder retry path does not 500

Commit.

### Task 2: Routers, quota re-check, pool re-check, frontend
**Files:**
- `backend/routers/schedules.py`
- the shared template→location helper (`backend/scheduling/...` or `services/...`)
- `graph.py` (use the helper)
- `services/location_quota.py` / `billing.py` (helper for the write-time re-check)
- frontend lock message plus i18n
- tests: `tests/test_schedules_integration.py`, `tests/test_edit_approved_schedule_integration.py`, and a new `tests/test_schedule_lock_concurrency_integration.py`

**Tests:**
- Existing 409, release-after-stream and release-on-exception tests adapted to rows with a location.
- Generate on location A while a lock is held on unrelated location B succeeds. With B sharing an employee with A, it returns 409 and names B.
- Generate with no location or template filter conflicts with any held lock.
- Approve and edit_approved on an overlapping location return 409 while a generate holds the lock.
- **Concurrency against Postgres:**
  - Two real concurrent generates (separate sessions, a monkeypatched fast pipeline) on disjoint locations both succeed.
  - Two on locations sharing an employee give one 200 stream and one 409.
- **Double-booking check:** two overlapping runs cannot both commit schedules assigning the same employee in the same week. The second gets 409.
- **Free-plan quota re-check:**
  - Simulate a second location's schedule being committed between the route pre-check and the write.
  - The write-time re-check refuses with `QUOTA_EXCEEDED` instead of exceeding `FREE_PLAN_MAX_LOCATIONS`.
- Frontend: a vitest test for formatting the lock message with and without `location_name`.

Commit.

## Review Focus
1. **Deadlock:** two operations with overlapping sets acquired in different orders. Sorted insertion must make this impossible. Add a test that acquires {A,B} and {B,C} concurrently.
2. **Lock leaks:** every exit path of generate's stream releases the whole group: client disconnect, exception, empty result.
3. **Restricted manager:** generating with no filter seeds only their in-scope template locations, never the whole company.
4. **Archived location in a set:** the FK and the lock must not fail when a seed contains an archived location.
5. **Multi-company ownership group:** locks are per company, while quotas are per ownership group. The ownership-group FOR UPDATE re-check covers sibling companies too.
