# Employee Preference Submissions (#189) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Employees submit their own scheduling preferences (six sections). Managers of the employee's locations review, edit and then approve or deny them. Only approved values reach the live tables the scheduler reads. A company admin turns the feature on per company.

**Architecture:** A new `employee_preference_submissions` table holds one JSON payload per submission: the employee's full desired state of the six sections. Approval replaces that employee's rows in the six live tables in one transaction, using the manager-edited payload, which must carry an explicit weight and level for every row. A per-company boolean gates everything. The scheduler and `preferences.annotate_preference_violations` are untouched.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, pytest + testcontainers Postgres; React + TS, Vite, vitest; i18n in `frontend/src/i18n/*.ts` (19 locales).

**Spec:** GitHub issue #189. A copy is at `/private/tmp/claude-501/-Users-robran-IdeaProjects-wiz-scheduler/dcbaac4c-97e6-440f-8714-613f304bf874/scratchpad/s189/spec-issue-189.md`.

## Global Constraints

- CLAUDE.md conventions apply:
  - Every query filters by `company_id`.
  - "Today" is `datetime.now(timezone.utc).date()`.
  - `DateTime(timezone=True)`.
  - `String(8)` ids with `generate_short_id`.
  - Archived employees are filtered with `services/archive.active(Employee)`.
  - No new dependencies.
  - Roles are never hardcoded.
- An employee never sets `weight` or affinity `level`. Employee payload schemas use `extra="forbid"`, so sending `weight` returns 422.
- Day Preferences: **at most 3** in an employee submission. This is checked on submit and again on approve. No DB constraint, and managers' direct edits are not capped.
- The feature is off by default: `companies.employee_preference_submissions_enabled BOOLEAN NOT NULL DEFAULT false`.
- Only admin managers can toggle it (`require_admin`). Regular managers and employees get 403.
- When it is disabled:
  - Employee submit returns 403 with detail code `employee_preference_submissions_disabled`.
  - Manager list returns `[]`.
  - Manager detail, approve and deny return 404.
  - Pending rows are untouched, never deleted or auto-decided.
- **Reviewer scope (revised in review, matching #201/#206):** a regular manager reviews only employees they could edit directly (`location_scope.require_employee_editable`: every one of the employee's locations is in scope). A shared employee's submission goes to an admin. The spec's original rule was "any location", but that would let a regular manager change a shared employee's preferences through approval, which the direct API refuses. Adding, changing or removing an association with a coworker the reviewer can't edit is refused (404). Associations left exactly as they are live are fine. Admins see everything. Out of scope → 404.
- Concurrent decisions: the submission row is locked `FOR UPDATE`. A decision on a non-pending row returns 409.
- **Notification:**
  - Approve or deny sends an email to the employee, metered by `email_quota.check_and_log_email(db, og_id, "preference_decision")`.
  - The email and the in-app decision payload list the approved values and contain **no** `weight`, `level` or `employee_importance`.
  - It goes only to the submitting employee.
- New UI copy is translated (real translations) in all 19 locale files. Use logical Tailwind utilities for RTL.
- Migration style: copy `0052` (STATEMENTS list) and the RLS block for a company_id table (below). `tests/test_migrations.py` must keep passing.

## Decisions (spec defaults, recorded)

- **Day-preference cap:** the 3-day cap binds employee submissions only, never managers' direct edits.
- **While disabled:** pending submissions are hidden and frozen. Re-enabling shows them again unchanged.
- **Affinity privacy:** the decision notification goes only to the submitter. The employee's own view shows only their *outgoing* associations (`employee_id == self`) as a relationship with no level: `prefer_with` if level > 0, else `prefer_apart`, or `never_together` if level == -1.
- **What a submission covers:** a submission is the employee's complete desired state of the six sections. The editor starts from a copy of the current approved values. Approval replaces all six sections for that employee.
- **Prefills:** the review UI pre-fills each weight from the matching live row (same day, or same start/end range), else 0.7. It pre-fills affinity level from the matching live row (same target), else `prefer_with` → 0.5, `prefer_apart` → -0.5, `never_together` → -1.0. The manager can change any of these. The server always requires the explicit values.

## Data model

`employee_preference_submissions`:

| column | type |
|---|---|
| id | String(8) PK, generate_short_id |
| company_id | String(8) FK companies.id NOT NULL, indexed |
| employee_id | String(8) FK employees.id ON DELETE CASCADE NOT NULL, indexed |
| status | String(16) NOT NULL; `pending` / `approved` / `denied` / `superseded` |
| submitted_payload | JSON NOT NULL |
| approved_payload | JSON NULL |
| submitted_at | DateTime(tz) NOT NULL server_default now |
| reviewed_at | DateTime(tz) NULL |
| reviewed_by | String(8) FK users.id ON DELETE SET NULL, NULL |
| denial_reason | Text NULL |

- Partial unique index `uq_pref_submission_one_pending` on `(employee_id) WHERE status = 'pending'`.
- Check constraint on `status`.

`companies` gains:
- `employee_preference_submissions_enabled` Boolean NOT NULL server_default false
- `employee_preference_submissions_toggled_at` DateTime(tz) NULL
- `employee_preference_submissions_toggled_by` String(8) FK users.id ON DELETE SET NULL, NULL

RLS for the new table, inside migration `0053` after `create_table`, with `T` = `employee_preference_submissions`:
```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON T TO app_role;
GRANT SELECT ON T TO analytics_role;
GRANT SELECT, UPDATE ON T TO admin_role;
ALTER TABLE T ENABLE ROW LEVEL SECURITY;
CREATE POLICY app_tenant_isolation_T ON T FOR ALL TO app_role
  USING (company_id = ANY(current_setting('app.company_ids', true)::text[]))
  WITH CHECK (company_id = ANY(current_setting('app.company_ids', true)::text[]));
CREATE POLICY analytics_read_all_T ON T FOR SELECT TO analytics_role USING (true);
CREATE POLICY admin_access_T ON T FOR ALL TO admin_role USING (true) WITH CHECK (true);
```
(Check the exact policy names and the `app.company_ids` setting against `0044_add_rls_policies.py`, and copy its wording exactly.)

## Payload schemas (`backend/schemas/preference_submission.py`)

Importance is `Literal["low","medium","high"]`. Relationship is `Literal["prefer_with","prefer_apart","never_together"]`. Times are `"HH:MM"`, validated with the existing helpers in `schemas/employee.py` (`_validate_hhmm`, the zero-length-range rule; overnight is allowed for hour ranges and caps). Blackouts follow the existing blackout rule: `end_time > start_time`.

Employee-side (`model_config = ConfigDict(extra="forbid")` on every model):
- `SubmittedAffinity {target_employee_id: str, relationship, entry_date: date|None, expiration_date: date|None}`
- `SubmittedBlackout {day_of_week: int 0..6, start_time, end_time}`
- `SubmittedDayPreference {day_of_week: int 0..6, employee_importance}`
- `SubmittedHourRangePreference {start_time, end_time, employee_importance}`
- `SubmittedCap {start_time, end_time, max_per_week: int 0..168, employee_importance}`
- `PreferenceSubmissionPayload {max_hours_per_week: float|None (ge 0, le 168), affinities: list[SubmittedAffinity] = [], blackouts: [...] = [], day_preferences: [...] = [] (max 3, unique day_of_week), hour_range_preferences: [...] = [] (unique start/end), caps: [...] = [] (unique start/end)}`. A model validator enforces the uniqueness and the 3-day cap. No self-affinity, and no duplicate targets.

Manager-side (approve body). Each `Approved*` row extends the submitted row and adds a required value:
- `weight: float` (0..1, one decimal; reuse `_validate_weight_step`) on day, hour-range and cap rows.
- `level: float` (-1..1, step 0.5) on affinities.
- `employee_importance` becomes optional.

`ApprovedPayload` mirrors `PreferenceSubmissionPayload` with these row types. The approve endpoint re-checks the 3-day cap. Affinity `entry_date` defaults to today (UTC) when None.

Employee-facing read model (`MyPreferencesView`): the same six sections from the live tables. Day, hour-range and cap rows have **no** weight and **no** importance. Affinities are outgoing only, with `relationship` derived as in Decisions, plus `target_name`.

## Endpoints (`backend/routers/preference_submissions.py`, prefix `/preference-submissions`, mounted in `backend/main.py`)

Employee (uses `get_current_user`; the caller must be `user_role == "employee"` with an active Employee where `user_id == current_user.id` and `company_id == current_user.company_id`, else 404 "No employee profile found"):
- `GET /me` → `{enabled: bool, current: MyPreferencesView, pending: {id, submitted_at, payload}|null, last_decision: {status, reviewed_at, denial_reason, approved: ApprovedSummary|null}|null}`.
  - `ApprovedSummary` is the redacted approved payload: no weight, level or importance; affinities as `{target_name, relationship? NO, just target_name}`.
  - **Approved coworker associations show coworker names only, with no relationship.** This keeps them non-scale. Spec: "coworkers".
- `PUT /me` body `PreferenceSubmissionPayload`:
  - 403 when disabled.
  - Marks an existing pending row `superseded` and inserts a new `pending` row in the same transaction.
  - Validates affinity targets: an active employee in the same company sharing at least one location with the submitter. Otherwise 422.
  - Returns the `GET /me` body.
- `GET /me/coworkers` → `[{id, full_name}]`: active employees, not self, sharing a location with the submitter.

Manager (`require_manager` + `get_location_scope`):
- `GET /` → pending submissions the caller can see: `[{id, employee_id, employee_name, location_ids, submitted_at}]`.
  - Returns `[]` when the caller's company has the feature disabled.
  - Excludes archived employees.
  - Ordered by `submitted_at`.
- `GET /{id}` → `{id, status, employee: {id, full_name, location_ids}, submitted_payload, current: live values WITH weights and levels (manager view), submitted_at}`.
  - 404 if not found, wrong company, out of scope, archived employee, or disabled.
- `POST /{id}/approve` body `ApprovedPayload`:
  - `SELECT ... FOR UPDATE` the row; if status != pending, 409 `{code: "submission_already_decided"}`.
  - Validate the affinity targets the same way as on submit, but relative to the employee.
  - Replace the live rows:
    - Delete the employee's rows in `employee_day_blackouts`, `employee_day_preferences`, `employee_hour_range_preferences`, `employee_hour_range_caps`, and `employee_affinities WHERE employee_id = emp`.
    - Insert the approved ones with explicit weight and level.
    - Set `employees.max_hours_per_week`.
  - Set `status=approved`, `approved_payload`, `reviewed_by`, `reviewed_at`.
  - Commit, then notify.
- `POST /{id}/deny` body `{reason: str|None (max 500)}`: same lock and 409 rule. Sets `denied`, `denial_reason`, `reviewed_*`, commits, then notifies.

Admin toggle (`backend/routers/company.py`):
- `PUT /company/settings/employee-preference-submissions` body `{enabled: bool}`, `require_admin`.
- Sets the flag plus `toggled_at` and `toggled_by`. Returns `CompanyResponse`.
- `CompanyResponse` gains `employee_preference_submissions_enabled: bool`, so `GET /company` exposes it to employees too.

Housekeeping:
- `services/archive.erase_employee` deletes the employee's submissions (add the model to its tuple).
- `services/billing.delete_og_and_dependents` deletes submissions before employees and companies. Check the FK order there.
- The model is exported from `backend/models/__init__.py`.

## Notification (`backend/services/preference_decision_email.py`)

- `render_decision_email(*, employee_name, decision, approved: dict|None, denial_reason, coworker_names: dict[str,str], group_name) -> tuple[str, str]` (subject, html). It is a pure function: html-escape everything and list the approved values.
  - Day names in English (emails are English today).
  - Time ranges.
  - Max hours.
  - Caps as "at most N shifts per week between HH:MM–HH:MM".
  - Blackouts.
  - Coworker names only.
  - **Never** weights, levels, importance or relationships.
- `async send_preference_decision_email(to, subject, html)`: copy `manager_invite_email.send_manager_invite_email` (lazy `resend`, no-op without `RESEND_API_KEY`, log exceptions).
- **Call site:** after the commit in approve and deny.
  - Get the recipient from `Employee.email`, else the linked `User.email`. If there is neither, skip.
  - Then `if await check_and_log_email(db, og_id, "preference_decision"): await db.commit(); await send(...)`.

## Frontend

- `frontend/src/api/preferenceSubmissions.ts`: `getMyPreferences`, `submitMyPreferences`, `listMyCoworkers`, `listSubmissions`, `getSubmission`, `approveSubmission`, `denySubmission`, `setEmployeePreferenceSubmissions(enabled)`. Add the types in `frontend/src/types`.
- **`pages/employee/MyPreferences.tsx`** (route `/employee/preferences`):
  - Status banner: pending, last approved, or denied with reason.
  - An editor for the six sections, starting from `pending.payload` if present, else from `current`.
  - Low/Medium/High selects for the three weighted sections. **No** weight control.
  - At most 3 day preferences; the Add button is disabled at 3.
  - A coworker picker from `/me/coworkers` with relationship options, plus a Submit button.
  - If `enabled` is false, show a short "not enabled" message.
- **Employee nav** (`navItems.ts` `employeeNav`): add `{to: "/employee/preferences", labelKey: "myPreferences"}`. Hide it when the company flag is false (the Sidebar fetches `GET /company` for employees, or reuses an existing company fetch).
- **`pages/manager/PreferenceReviews.tsx`** (route `/manager/preference-reviews`):
  - A queue list, then a review panel showing current vs submitted side by side.
  - Each weighted row gets a weight input, pre-filled per Decisions, with the employee's Low/Medium/High shown as a hint. Each affinity gets a level select (-1, -0.5, 0.5, 1).
  - Approve, and Deny with an optional reason.
  - Show the API error message inline (for example 409).
  - Manager nav entry (under the employees/preferences group). Hide it when the flag is false. It is not admin-only.
- **`pages/manager/Company.tsx`:** an admin-only switch with one line of explanation, calling the toggle endpoint.
- **i18n:**
  - New sections `myPreferences` and `preferenceReviews`, plus the `company` toggle keys and `nav.myPreferences` / `nav.preferenceReviews`.
  - All 19 locales get real translations.
  - Add `myPreferences` and `preferenceReviews` to `CHECKED_SECTIONS` in `appStringsTranslated.test.ts`.

## Tasks

### Task 1: Data model, migration, admin toggle
**Files:**
- Create `backend/models/preference_submission.py` and `backend/alembic/versions/0053_employee_preference_submissions.py`.
- Modify `backend/models/__init__.py`, `backend/models/company.py`, `backend/schemas/company.py`, `backend/routers/company.py`.
- Test: `tests/test_preference_submissions_toggle_integration.py`.

**Steps:**
- [ ] Write failing tests:
  - the toggle defaults to false on `GET /company` (manager and employee tokens)
  - an admin PUT sets it and records `toggled_at` and `toggled_by`
  - a regular manager (`regular_token`) gets 403
  - an employee gets 403
- [ ] Implement the model, migration (columns, table, partial unique index, status check, RLS block) and the toggle endpoint.
- [ ] Run `tests/test_migrations.py` and the new tests. Commit.

### Task 2: Employee submission API
**Files:**
- Create `backend/schemas/preference_submission.py`, `backend/services/preference_submissions.py` (query and view helpers), `backend/routers/preference_submissions.py` (employee routes).
- Modify `backend/main.py`.
- Test: `tests/test_preference_submissions_employee_integration.py`.

**Tests:**
- [ ] `GET /me` returns `enabled=false` while disabled, and a PUT returns 403 with the code.
- [ ] With the feature enabled, submitting all six sections creates a pending row, and `GET /me` shows it.
- [ ] Resubmitting supersedes the old row, leaving exactly one pending.
- [ ] `weight` in any weighted row returns 422, and `level` in an affinity returns 422.
- [ ] 4 day preferences return 422. Duplicate days return 422.
- [ ] Affinity targets: self, another company's employee, an archived employee, or an employee who shares no location each return 422.
- [ ] An archived submitter gets 404, and so does a manager token calling `/me`.
- [ ] `current` never contains `weight`, `level` or `employee_importance` (assert on the JSON keys recursively). Its affinities are outgoing only.
- [ ] Submitting changes nothing in the six live tables.

**Steps:**
- [ ] Write failing tests.
- [ ] Implement.
- [ ] Run. Commit.

### Task 3: Manager review API
**Files:**
- Modify `backend/routers/preference_submissions.py` (manager routes), `backend/services/preference_submissions.py`, `backend/services/archive.py` (`erase_employee`), `backend/services/billing.py` (`delete_og_and_dependents`).
- Test: `tests/test_preference_submissions_review_integration.py`.

**Tests:**
- [ ] The list shows the pending submission to an admin, and to a regular manager whose location the employee is at (an employee at both locations is visible to a manager of either).
- [ ] A regular manager of the other location gets `[]` from the list and 404 from detail, approve and deny.
- [ ] Another company's manager gets 404.
- [ ] Approve writes exactly the approved rows, with the manager's weights and levels, into all six live tables and `max_hours_per_week`, and replaces the previous rows. The row is `approved` with `approved_payload`, `reviewed_by` and `reviewed_at` set.
- [ ] Approve with a missing weight or level returns 422. Approve with 4 days returns 422.
- [ ] Deny changes no live rows and stores the reason.
- [ ] A second decision returns 409. Also add a concurrency test: two approves via `asyncio.gather` on separate sessions; exactly one succeeds and the other gets 409.
- [ ] While disabled: the list returns `[]`, detail returns 404, the pending row is still pending. After re-enabling, it is visible again.
- [ ] An archived employee's pending submission is hidden.
- [ ] `erase_employee` removes the submissions.
- [ ] `delete_og_and_dependents` still succeeds with a submission present. Extend an existing purge test, or add one.

**Steps:**
- [ ] Write failing tests.
- [ ] Implement.
- [ ] Run, including `tests/test_location_scope*`, `tests/test_gdpr*` and the billing purge tests. Commit.

### Task 4: Decision notification
**Files:**
- Create `backend/services/preference_decision_email.py`.
- Modify the approve and deny handlers.
- Test: `tests/test_preference_decision_email.py` (unit) plus integration assertions in the Task 3 file.

**Tests:**
- [ ] `render_decision_email` for an approval lists days, ranges, max hours, caps' `max_per_week`, blackouts and coworker names. Its subject and html contain none of the weight values, level values, the words low/medium/high, or the relationship labels. Use distinctive weights such as 0.3 and 0.9 and assert those strings are absent.
- [ ] A denial includes the escaped reason.
- [ ] Integration: with `RESEND_API_KEY` monkeypatched and a fake `resend` module (pattern in `tests/test_billing_integration.py` ~2037), approve sends exactly one email to the employee's address, and an `OgEmailSendLog` row of kind `preference_decision` exists. With the quota exhausted, no email is sent and the approval still succeeds.
- [ ] The `GET /me` `last_decision.approved` contains no weight, level or importance keys.

**Steps:**
- [ ] Write failing tests.
- [ ] Implement.
- [ ] Run. Commit.

### Task 5: Frontend
**Files:**
- Create `frontend/src/api/preferenceSubmissions.ts`, `frontend/src/pages/employee/MyPreferences.tsx`, `frontend/src/pages/manager/PreferenceReviews.tsx`, plus pure helpers with vitest tests: `frontend/src/utils/preferenceSubmissions.ts` and `.test.ts` (draft from current, the 3-day limit, weight and level prefill rules, building the approve payload).
- Modify `frontend/src/types`, `App.tsx` routes, `components/layout/navItems.ts` and its test, the Sidebar gating, `pages/manager/Company.tsx`, and all 19 `i18n/*.ts` files plus `appStringsTranslated.test.ts`.

**Steps:**
- [ ] Write failing helper tests.
- [ ] Implement the helpers, API client, pages, nav and toggle.
- [ ] Translate every new key in all 18 non-English locales.
- [ ] Run `npm test`, the typecheck and the lint/build scripts. Commit.

## Review Focus

1. **Weight leakage:** any response an employee can read (`/me`, `last_decision`, coworkers) or the email exposing a weight, level, importance or another employee's affinity. Test: a recursive key check plus value-absence assertions (Tasks 2 and 4).
2. **Scope:** a regular manager of location B reviewing an employee only at A. Expect 404 everywhere (Task 3).
3. **Replace semantics:** approve must replace, not append. Test that a pre-existing live row not in the approved payload is gone afterwards (Task 3).
4. **Disabled company:** the flag is per company. A manager whose ownership group has two companies sees only submissions from companies with the flag on (Task 3, if the fixture supports a second company; otherwise a note).
5. **A superseded or decided submission cannot be approved:** approving a superseded id returns 409 (Task 3).
