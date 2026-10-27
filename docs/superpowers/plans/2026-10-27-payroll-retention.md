# Payroll Record Retention (#172, core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Pay records (`time_entries`) get a defined end of life: a 7-year retention sweep. Before that, admins can download every retained pay record and see a warning 30 days before the next sweep deletes any of them.

**Scope (owner decision, 2026-10-27):** core only.
- The paid $18 archive becomes a separate issue.
- DPA wording ships in its own PR that is left unmerged for legal review. Termination deletion is stated as 90 days, to match `SUBSCRIPTION_GRACE_DAYS`.
- This plan covers only the code PR.

**Spec:** GitHub issue #172 (body plus comments). The code map is `/private/tmp/claude-501/-Users-robran-IdeaProjects-wiz-scheduler/dcbaac4c-97e6-440f-8714-613f304bf874/scratchpad/s172/survey.md`. Read it.

## Global Constraints
- CLAUDE.md conventions apply: type hints, "today" is UTC, no new dependencies, `company_id` on every query.
- Payroll endpoints are admin-only (`require_admin`).
- `RETENTION_TIME_ENTRIES_DAYS: int = 2557` (7 × 365 + 2 leap days), in `backend/config.py`'s retention block, in the existing comment style (`# Pay records (#172). ...`).
- The sweep deletes `time_entries` whose `pay_date < (now_utc - RETENTION_TIME_ENTRIES_DAYS days).date()`. It is one bulk delete, like the other steps.
  - Summary key: `time_entries_deleted`, present even when 0.
  - Replace the "never swept" comment in `data_retention.py`.
  - Nothing has a foreign key into `time_entries`.
  - Archived and erased employees' rows are swept by date like any other.
- The sweep runs monthly at 03:00 UTC on the 1st (`terraform/retention_schedule.tf`). The next sweep is the 1st of next month (UTC).
- **Records due in the next sweep:** pay dates before the cutoff at the next sweep date: `next_sweep - RETENTION_TIME_ENTRIES_DAYS`.

## Endpoints (`backend/routers/payroll.py`)
- **`GET /payroll/retention`** returns:
  ```
  {retention_days, cutoff_date (today's cutoff), next_sweep_date,
   next_sweep_cutoff_date, oldest_pay_date | null,
   due_count (entries with pay_date < next_sweep_cutoff),
   warn: bool (due_count > 0 and next_sweep_date - today <= 30 days)}
  ```
  - Scoped to `current_user.company_id`.
  - Gate: same as the history export (below).
- **`POST /payroll/export/history`** returns CSV of EVERY retained time entry for the company: all locations, approved or not, already exported or not.
  - Reuse `render_csv` and `PayrollCsvRow`, including the formula guard. Add an `approved` column (`yes`/`no`) at the end of the CSV header for this export only. Use a parameter or a second header constant; keep the existing export byte-identical.
  - No side effects on time entries: no `exported_at` stamping.
  - Insert one `PayrollExport` audit row, `range_start=min(pay_date)`, `range_end=max(pay_date)`, `location_id=NULL`, with `entry_count` and `paid_minutes_total`. It is commented as a full-history export.
  - Filename `payroll_history_{slug}_{min}_{max}.csv`.
  - 409 `nothing_to_export` when there are no entries.
  - Order by `pay_date`, `start_time`, `id`.
- **Gate for both:** a new `assert_payroll_history_access(db, company_id)` in `services/plan.py`.
  - Passes when the ownership group is on a paid plan, OR is canceled and still inside the grace period (`canceled_at` set and the group not yet deleted). A customer who canceled must still be able to extract their records before deletion; this is the reason for the issue.
  - Free groups that never paid get 402, like the other payroll endpoints.
- Add both endpoints to `tests/test_payroll_api_integration.py` `_GATED_ENDPOINTS`, or the equivalent parametrised admin-only and paid-only checks. Adjust the paid-only test, because canceled-in-grace is allowed for these two only.

## Frontend (`pages/manager/Payroll.tsx`, `api/payroll.ts`)
- `getRetention()` and `downloadPayrollHistoryCsv()`, the latter following the existing `downloadPayrollCsv` blob and filename pattern.
- A "Download full history" button near the existing Download CSV action. It has a short description: "Every pay record we hold, for your records".
- When `warn` is true, show a warning banner at the top of the page: "{count} pay records dated before {cutoff} will be permanently deleted on {date} under the 7-year retention policy. Download your full history to keep a copy." It includes the download button.
- **Canceled-in-grace:** the page currently shows only `paidPlanOnly` on a 402. For a canceled-in-grace group:
  - Show the history download and the warning.
  - Hide the rest: those endpoints still return 402 from `assert_paid_plan`.
  - Implement this by calling `getRetention()` first: if it succeeds while the other calls 402, render the history-only view.
- i18n keys go in the `payroll` section. All 19 locales need real translations.
- vitest for any pure helper, such as building the banner text.

## Tasks

### Task 1: Retention setting and sweep
**Files:** `backend/config.py`, `backend/services/data_retention.py`, `tests/test_payroll_retention_integration.py` (extend; follow its `_seed` pattern).

**Tests:**
- An entry with `pay_date` older than the window is deleted, and one inside it is kept.
- The boundary day is kept.
- The summary key is present at 0.
- Pin the default: `assert settings.RETENTION_TIME_ENTRIES_DAYS == 2557`.
- An archived employee's old entry is also deleted.
- Existing retention tests still pass.

Commit.

### Task 2: Retention info, history export, gate, frontend
**Files:**
- `backend/services/plan.py`
- `backend/services/payroll_export.py`
- `backend/routers/payroll.py`
- `backend/schemas/payroll.py`
- `tests/test_payroll_api_integration.py`
- `tests/test_payroll_export_csv.py`
- `frontend/src/api/payroll.ts`
- `frontend/src/pages/manager/Payroll.tsx`
- `frontend/src/i18n/*.ts`
- helper and test

**Tests:**
- **History export:**
  - It includes unapproved and already-exported entries across locations.
  - It writes no `exported_at` and adds one `PayrollExport` row.
  - Formula injection is neutralised.
  - It returns 409 when empty.
  - A regular manager gets 403. Another company's entries are excluded.
  - A canceled-in-grace group gets 200; a never-paid free group gets 402.
- **Retention info:** `due_count`, `warn` and the dates computed correctly. Freeze "now" by monkeypatching the module's clock helper, or pass `now` in. Cover:
  - warn true within 30 days of the sweep when old entries exist
  - warn false when none exist
  - warn false when the sweep is more than 30 days away
- **Frontend:** npm test plus build. Check in a browser if feasible.

Commit.

## Review Focus
1. **Off-by-one at the cutoff:** the sweep and `due_count` must use the same comparison (`<`).
2. **Byte-identical existing export:** the existing export's CSV must be byte-identical. Existing `render_csv` tests pin this.
3. **Canceled-in-grace:** the group can reach exactly these two endpoints and nothing else under payroll.
4. **Multi-company ownership group:** the history export covers only the caller's company. That matches the per-company export today; note it in the report.
5. **Large history:** 7 years can be about 360k rows. Build the CSV without loading ORM objects twice, and stream if simple (`StreamingResponse`). Otherwise note the size bound.
