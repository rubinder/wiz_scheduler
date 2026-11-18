# AI Host Call Screening Implementation Plan (step 7 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spam calls are dropped before Lex, Polly or Claude engage. Each
restaurant keeps allow and block number lists, managers can mark a
conversation as spam, and a weekly job ingests the FTC Do Not Call
Reported Calls data into a global blocklist.

**Architecture:**
- **Backend:** a new `reservation_number_rules` table (per location or
  company-wide, with allow/block, expiry and note) and a global
  `spam_numbers` table (reports per number per day, so the 90-day window
  can be computed). A single evaluator, `screening.decide(db, location,
  caller, now)`, answers `answer | block`. It is exposed to the Connect
  flow through `POST /api/v1/voice/screen` on the bridge router, guarded
  by the same secret.
- **Lambda:** a second small Lambda handler in `infra/voice/bridge`,
  `screen_handler`, using stdlib only with a 1 s backend timeout. It
  fails open and returns `{"action": "answer"|"block"}`. The contact
  flow invokes it before the Lex block.
- **FTC ingest:** an ECS scheduled task (`backend/scripts/run_ftc_ingest.py`)
  that downloads every weekday CSV since its last successful file, parses
  it, upserts report counts, and alerts the operator when it downloads
  nothing or parses nothing. The source URL is a setting.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, stdlib `csv` and `urllib`
(no new deps), React and i18n for the number-lists UI, Terraform for the
schedule and the Lambda (count-gated).

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
section 7, and section 12 step 7.

## Global Constraints

- **Order of checks** (the first match wins):
  1. location or company **allow** → answer
  2. **block** → block
  3. FTC blocklist → block
  4. otherwise answer
  A withheld or invalid caller ID → answer. Expired entries don't apply.
- **FTC blocking rule:** a number is blocked when it was reported at least
  `SPAM_MIN_REPORTS = 3` times within `SPAM_WINDOW_DAYS = 90`. Both are
  settings. Rows older than the window are pruned by the ingest.
- **Ingest:**
  - Idempotent per source file date (the `spam_ingest_files` table
    records date, row count and status).
  - Weekly by default (Terraform var `ftc_ingest_schedule`, default
    `cron(0 18 ? * MON *)`), gated on `ftc_ingest_enabled` (default
    false).
  - Source URL template is the setting
    `FTC_DNC_CSV_URL = "https://www.ftc.gov/sites/default/files/DNC_Complaint_Numbers_{date}.csv"`.
    The FTC site refused our scripted fetches with a 404 during planning,
    so the owner checklist verifies the URL live.
  - Downloads carry a descriptive User-Agent with no personal data
    (`WizScheduler-FTC-ingest/1.0`), a 30 s timeout, and at most 20 MB
    per file.
- **Blocked calls are not usage.** The screen endpoint makes no model call
  and creates no agent conversation. It records one `screened_calls` row
  (location, hashed caller via the existing HMAC, decision, reason,
  created_at) for analytics. Retention matches agent events.
- `spam_numbers` is global and not tenant-scoped: no RLS, read-only from
  the app role except for the ingest. Use the existing cross-tenant or
  owner session pattern for writes, and document it.
- **Mark as spam** creates a permanent location `block` entry with the
  note "Marked as spam from conversation <id>". It is idempotent per
  location and number.
- **Managers** see and edit only their scope's locations (LocationScope
  rules). Company-wide entries need admin.
- Phone numbers are stored E.164. Diner numbers in number rules are diner
  data: they are covered by the diner-data export/erase helpers (erase
  removes rules for that number at that company) and retained until a
  manager deletes them.
- English copy is added to all 19 locales.

## Review Focus

1. **A diner on the allow list but also on the FTC list is answered.**
   Tested in Task 1.
2. **A flaky or slow backend answers the call** (fail open inside 1 s),
   and a missing or invalid secret never blocks calls. Tested in Task 3.
3. **A re-run on the same files doesn't double-count reports.** Tested in
   Task 2.
4. **A company-wide block applies to every location, and a location
   allow overrides it.** Tested in Task 1.
5. **A manager scoped to location A can't read or change rules of
   location B or company-wide rules.** Tested in Task 4.

---

### Task 1: Number rules, screening evaluator, screen endpoint

**Files:**
- `backend/reservations/screening.py` (models in `screening_models.py`)
- migration `0063_call_screening.py`
- `backend/routers/voice_bridge.py` (+ `POST /voice/screen`)
- `backend/schemas/voice.py`
- tests

**Interfaces:**
- `NumberRule(id, company_id, location_id nullable (null = all locations), e164, action "allow"|"block", expires_at nullable, note String(200) nullable, created_by_user_id nullable, created_at)`
  - RLS keyed on company.
  - Index on `(company_id, e164)`.
- `SpamNumber(e164, report_date date, reports int)`
  - Primary key `(e164, report_date)`.
  - Index on `e164`.
- `SpamIngestFile(file_date date pk, rows int, status, fetched_at)`
- `ScreenedCall(id, company_id, location_id, caller_key String(64) nullable, decision, reason "allow_list"|"block_list"|"ftc"|"default"|"withheld", created_at)`
  - RLS keyed on company.
- `async screening.decide(db, company_id, location_id, caller: str | None, now) -> tuple[str, str]`
  - Precedence: location allow, company allow, location block, company
    block, FTC.
  - Location entries beat company-wide ones within the same action.
  - Allow beats every block.
- `async screening.is_ftc_blocked(db, e164, now) -> bool`: sums reports
  within the window and compares the total with the setting.
- `POST /api/v1/voice/screen`
  - Request body `{dialed, caller}`.
  - Response `{action: "answer"|"block", reason}`.
  - Same secret, enabled and dialed-number mapping as `/voice/turn`.
  - Unknown number → answer (the turn endpoint then closes politely).
  - Records a `ScreenedCall` row.
  - No rate limit by caller: blocked spammers would just be blocked anyway.

- [ ] Write the tests:
  - every precedence case from the Global Constraints
  - expired entries are ignored
  - a withheld caller is answered
  - FTC threshold at 2 vs 3 reports, inside and outside the window
  - a company-wide block with a location allow is answered
  - the endpoint enforces the secret
  - the endpoint makes no model call and creates no conversation
  - each decision records a `ScreenedCall`
- [ ] Commit `feat(screening): number rules, FTC lookup and the screen endpoint (#259)`

### Task 2: FTC ingest job

**Files:**
- `backend/reservations/ftc_ingest.py`
- `backend/scripts/run_ftc_ingest.py`
- tests with fixture CSVs in `tests/fixtures/ftc/`

**Interfaces:**
- `parse(csv_bytes) -> dict[str, int]` (e164 → report count for that file)
  - Find the phone column by header name, case-insensitive. The
    candidates are `Company_Phone_Number` and anything containing "phone".
    Otherwise fail.
  - Normalise US 10-digit numbers to `+1…`. Skip invalid rows and count
    them.
- `async ingest(db, today, fetch=default_fetch) -> dict`
  - Download each weekday date from the last OK file + 1 (or `today − 14`
    on the first run) through `today`.
  - A 404 on today's file isn't an error; it isn't published until noon
    Eastern.
  - Upsert `spam_numbers` rows `(e164, file_date, count)`. Re-processing
    the same file replaces its counts rather than adding to them.
  - Record `SpamIngestFile`.
  - Prune rows older than the window.
  - Returns the summary.
- Script: run `ingest` and log the summary. Alert the operator
  (`operator_alerts`) when nothing has been fetched for 8 days, a parse
  yields 0 valid rows, or the run raises.
- [ ] Write the tests:
  - parsing, including header variants and junk rows
  - idempotent re-run
  - weekday iteration including holidays (missing files are skipped)
  - pruning
  - alert conditions
- [ ] Commit `feat(screening): weekly FTC Do Not Call reported-numbers ingest (#259)`

### Task 3: Screening Lambda, contact flow, Terraform

**Files:**
- `infra/voice/bridge/screen.py` (handler `screen.handler`; shares the
  secret loader from `handler.py`)
- `terraform/voice.tf` (second Lambda function from the same zip, IAM,
  permission for Connect to invoke it, `aws_connect_lambda_function_association`)
- `terraform/voice/contact_flow.json.tftpl` (InvokeLambdaFunction before
  the Lex block: result `block` → disconnect; error or timeout → continue
  to Lex)
- `terraform/ftc_ingest_schedule.tf` (cloned from the reconcile schedule,
  gated on `ftc_ingest_enabled`)
- `tests/infra`

**Interfaces:**
- `screen.handler(event, context)`
  - Input is the Connect Lambda event: `Details.ContactData.SystemEndpoint.Address`
    and `CustomerEndpoint.Address`.
  - Output is `{"action": "answer"|"block"}`. On any failure, timeout,
    or a backend response that isn't 200 → `answer`.
  - Backend timeout 1.0 s. Lambda timeout 3 s (Connect allows up to 8 s).
  - Logs the decision and contact id only.
- [ ] Write the tests:
  - fail-open on timeout, 5xx, 404 and malformed responses
  - block/answer mapping
  - Terraform gating test extended to the new resources
  - `fmt` and `validate` with voice on and off
- [ ] Commit `feat(screening): screening Lambda in the contact flow and the weekly ingest schedule (#259)`

### Task 4: Manager UI, mark-as-spam, diner data

**Files:**
- `backend/routers/reservations.py` (number-rules CRUD:
  `GET/POST /reservations/number-rules`, `PATCH/DELETE /reservations/number-rules/{id}`;
  `POST /reservations/conversations/{id}/mark-spam`)
- `backend/reservations/diner_data.py` (export and erase cover number rules)
- frontend: `pages/manager/ReservationsNumbers.tsx` and an api module,
  a nav entry under Reservations, a "Mark as spam" button in the
  conversation view, and i18n ×19
- tests

**Interfaces:**
- Rules list, filterable by location. Columns:
  - number
  - action
  - scope (this location / all locations)
  - expires (date, or "Never")
  - note
  - created by
  - status (active / expired)
- Create and edit form fields:
  - number, E.164-validated, with a hint showing the format
  - action
  - scope
  - expires: never, or a date in the location's local time, stored as
    the end of that day
  - note (≤ 200 chars)
- Mark as spam:
  - Needs a voice conversation with a caller number. Otherwise 409
    `no_caller_number`.
  - Idempotent.
  - Returns the rule.
- Screened-calls count for the last 7 days is shown at the top of the page
  (blocked by FTC / by list).
- [ ] Write the tests:
  - CRUD with scope enforcement: a regular manager can't touch other
    locations or company-wide rules
  - mark-spam idempotency and the 409
  - erase removes the rules for a number and export includes them
  - frontend helper tests for status and expiry formatting
  - `npm test`, `tsc`, build
- [ ] Commit `feat(screening): number lists, mark as spam, and screening stats for managers (#259)`

### Task 5: Docs

- Decisions log:
  - Allow beats block, and location beats company.
  - Fail open in 1 s.
  - FTC threshold 3 in 90 days, with reports stored per file date so
    re-runs replace rather than add.
  - Weekly ingest is gated.
- Owner checklist:
  - Verify the FTC URL live, or switch to the API.
  - Set `ftc_ingest_enabled`.
  - Make a test call from a number on the block list.
- Architecture reference entry points.
- [ ] Commit `docs(screening): decisions and switch-on steps (#259)`
