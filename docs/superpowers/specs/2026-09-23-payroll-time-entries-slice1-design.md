# Payroll Time Entries, Slice 1 — Design Spec

Issue: [#78](https://github.com/rubinder/wiz_scheduler/issues/78)
Builds on: [#63](https://github.com/rubinder/wiz_scheduler/issues/63) (check-in),
[#44](https://github.com/rubinder/wiz_scheduler/issues/44) (integration audit rows)
Status: approved, not yet implemented

## Purpose and Scope

Issue #78 is a six-vendor payroll integration with OAuth credential storage and
per-vendor sandbox spikes. It is labelled "backlog / not scheduled", and the
per-vendor table in it is explicitly a set of unverified assumptions. None of
that ships here.

What ships here is the **provider-agnostic core** the issue itself defines as
Phase 0 plus Phase 1: the hours model, the exception queue, manager
attestation, approval, and a CSV download. CSV is the one "provider" every
payroll system on the list accepts today, it needs no partnership, and it
validates that our hours are correct before any live write can embarrass us.

### The issue's decisions, restated

These were settled in #78 and are not reopened here.

1. **Paid hours are the scheduled hours.** `Shift.start_time` → `Shift.end_time`.
   The #63 check-in is a **gate, not a stopwatch**: it decides *whether* the
   shift is payable, never *how many* hours it pays. Someone who stays late or
   leaves early is paid the scheduled hours.
2. **Lateness is surfaced, never docked.** The check-in timestamp versus the
   scheduled start travels with the entry and appears in the export, and no
   code path subtracts it from pay.
3. **A missed scan does not strand a shift.** An approved shift in the pay
   range with no matching check-in lands in a **manager exception queue**, and
   a manager can attest that the person worked it. Phones die; nobody misses a
   paycheque over a QR scan.
4. **Attestation carries a real audit trail.** `source = manager_attested`,
   the attesting user, the timestamp, an optional reason. It is a pay-affecting
   action one person takes on another's behalf.
5. **Nothing exports unapproved.** A manager approves a set of entries for a
   date range first.
6. **Paid plans only**, via `assert_paid_plan`. Free-plan ownership groups do
   not see the feature.
7. **Attestation rate is reporting, not blocking.** It appears per location in
   the existing `GET /check-ins/report`, consistent with the monitoring posture
   of #43/#49 rather than a hard cap that withholds pay.

### Explicitly out of scope for this slice

- Every vendor API: Gusto Embedded, ADP RUN, ADP Workforce Now, Finch, Merge,
  QuickBooks/QuickBooks Time, Paychex Flex.
- OAuth flows, refresh tokens, webhooks, and the encrypted-at-rest credential
  store. Nothing here stores a third-party secret.
- `WorkerLink` (employee ↔ provider worker id). No provider, no link.
- `PayPeriod` pulled from a provider. The manager picks a date range.
- Check-out, actual duration, unpaid-break capture. #63 records arrivals only,
  and a break model with nothing to populate it is a column that lies.
- Fair Workweek change premiums and earnings-code mapping (#62 tie-in).
- The `PayrollProvider` protocol. The CSV exporter is a plain function. See
  *Future Adapter Seam*.

Unpaid breaks appear in the issue's `TimeEntry` sketch. They are dropped here
deliberately: there is no source of break data in the product today, so the
column would be constant zero, and a payroll file that reports a zero break for
an eight-hour shift is worse than one that reports no break field at all.

---

## Data Model

Two new tables in Alembic migration `0036`.

### `time_entries`

One row per payable shift. Derived, never hand-authored.

| Column | Type | Notes |
|---|---|---|
| `id` | `String(8)` PK | `generate_short_id`, per repo convention |
| `company_id` | `String(8)` FK → `companies.id`, indexed | Every query filters on it |
| `location_id` | `String(8)` FK → `locations.id` | Copied from the shift |
| `employee_id` | `String(8)` FK → `employees.id` | Copied from the shift |
| `shift_id` | `String(8)` FK → `shifts.id`, **unique** | The shift this pays for |
| `role_id` | `String(8)` FK → `roles.id` | Copied from the shift |
| `role_name` | `String` | Snapshot of `Shift.role_name`, itself sourced from the `roles` table. No role string literal appears in this feature's code. |
| `pay_date` | `Date` | Location-local date the shift **started**. See below. |
| `start_time` | `DateTime(timezone=True)` | Copied from `Shift.start_time`, offset intact |
| `end_time` | `DateTime(timezone=True)` | Copied from `Shift.end_time`, offset intact |
| `paid_minutes` | `Integer` | Derived once at creation. See below. |
| `source` | `String(16)` | `checked_in` or `manager_attested` |
| `check_in_id` | `String(8)` FK → `employee_check_ins.id`, nullable | The gating scan. Nulled by the check-in retention sweep. |
| `checked_in_at` | `DateTime(timezone=True)`, nullable | Denormalised from the check-in; NULL for attested entries |
| `lateness_minutes` | `Integer`, nullable | Signed, negative early. Denormalised from `EmployeeCheckIn.minutes_from_start`; NULL for attested entries |
| `attested_by_user_id` | `String(8)` FK → `users.id`, nullable | |
| `attested_at` | `DateTime(timezone=True)`, nullable | |
| `attestation_reason` | `String(500)`, nullable | Free text, optional |
| `approved_at` | `DateTime(timezone=True)`, nullable | NULL means not approved |
| `approved_by_user_id` | `String(8)` FK → `users.id`, nullable | |
| `exported_at` | `DateTime(timezone=True)`, nullable | Idempotency marker, mirroring `Shift.exported_at` |
| `payroll_export_id` | `String(8)` FK → `payroll_exports.id`, nullable | Which export carried it. Nulled by the export-log retention sweep. |
| `created_at` | `DateTime(timezone=True)` | `server_default=text("now()")` |

Constraints:

- `UniqueConstraint("shift_id", name="uq_time_entries_shift")` — **one entry per
  shift, enforced by the database.** This is what makes derivation idempotent
  and what stops an attestation racing a derivation into two payable rows for
  the same shift. Same posture as `uq_employee_check_ins_location_date_counter`:
  application logic can be forgotten by a future caller, a constraint cannot.
- `CheckConstraint("source IN ('checked_in', 'manager_attested')",
  name="time_entries_source_check")`.
- `CheckConstraint("source <> 'manager_attested' OR (attested_by_user_id IS NOT
  NULL AND attested_at IS NOT NULL)", name="time_entries_attested_check")` —
  an attested row without an attester is an audit trail with a hole in it.
- `CheckConstraint("paid_minutes > 0", name="time_entries_paid_minutes_check")`.

Indexes:

- `ix_time_entries_company_id` on `company_id`
- `ix_time_entries_company_pay_date` on `(company_id, pay_date)` — the range list
- `ix_time_entries_company_location_pay_date` on `(company_id, location_id, pay_date)` — the per-location filter and the attestation rate
- `ix_time_entries_company_employee_pay_date` on `(company_id, employee_id, pay_date)`

### `payroll_exports`

The audit row per export, in the pattern of `integration_imports` (#44) and
`gdpr_export_log` (#45). This slice has no cooldown — a CSV download costs
nothing and hits no third party — so the table is audit only, and the quota
shape is available later without a schema change (see *Future Adapter Seam*).

| Column | Type | Notes |
|---|---|---|
| `id` | `String(8)` PK | |
| `company_id` | `String(8)` FK → `companies.id`, indexed | Not nullable |
| `ownership_group_id` | `String(8)` FK → `ownership_groups.id`, **nullable** | NULL only for the seed/dev company with no OG, which `get_plan_state` treats as unlimited and which therefore passes `assert_paid_plan` |
| `exported_by_user_id` | `String(8)` FK → `users.id` | |
| `format` | `String(32)` | `csv` in this slice; `CheckConstraint("format IN ('csv')", name="payroll_exports_format_check")` |
| `location_id` | `String(8)` FK → `locations.id`, nullable | NULL = every location in range |
| `range_start` | `Date` | |
| `range_end` | `Date` | Inclusive |
| `entry_count` | `Integer` | |
| `paid_minutes_total` | `Integer` | |
| `created_at` | `DateTime(timezone=True)` | `server_default=text("now()")` |

Index: `ix_payroll_exports_company_created` on `(company_id, created_at)`.

Both models are registered in `backend/models/__init__.py` (`TimeEntry` from
`backend/models/time_entry.py`, `PayrollExport` from
`backend/models/payroll_export.py`) and added to `__all__`.

### The midnight-crossing rule

**The whole shift's hours land on the location-local calendar date the shift
started.** No shift is ever split across two pay dates.

```
pay_date = start_time.astimezone(ZoneInfo(location.timezone)).date()
```

Derived from `location.timezone` via `zoneinfo`, never from `Shift.date` (which
is LLM-supplied and can drift) and never from the UTC instant.

A 22:00 Sunday → 06:00 Monday shift is paid **entirely on Sunday**. If the pay
range ends Sunday it is included in full; if the range starts Monday it is
excluded entirely.

Three reasons this is the right rule rather than splitting at midnight:

1. It agrees with `EmployeeCheckIn.local_date`, which is the local date of the
   *scan* — and the scan is at the start of the shift. A split would make the
   gate and the payment disagree about which day the shift belongs to.
2. It agrees with `Shift.date`, which the pipeline sets to the start date, so
   the exception queue, the schedule UI, and the export all name the same day.
3. Splitting is only correct if you also know the jurisdiction's overtime day
   boundary, which we do not model. Reporting a single unambiguous day beats
   reporting two plausible-looking halves.

### The matching rule between shift and check-in

**We do not re-derive the match. We read the one `record_check_in` already
recorded.**

`_match_shift` runs at scan time inside `record_check_in`: it selects shifts
for that employee whose `start_time` is within `CHECKIN_MATCH_WINDOW_HOURS` of
the scan, prefers the ones at the scanned location, and picks the nearest by
start time. The chosen shift's id is persisted on `EmployeeCheckIn.shift_id`,
and the signed offset is persisted on `minutes_from_start`.

Payroll therefore matches by a single join:

```
EmployeeCheckIn.shift_id == Shift.id
AND EmployeeCheckIn.status IN ('matched', 'duplicate')
```

taking the **earliest `checked_in_at`** when more than one row points at the
same shift.

Three consequences, all deliberate:

- **`duplicate` counts as evidence of arrival.** The `duplicate` status answers
  "should this scan own the punctuality number for the day", not "did this
  person turn up". On a split-shift day the second shift's genuine arrival is
  recorded as `duplicate`; refusing it would push a real shift into the
  exception queue for no reason. Taking the earliest row means a `matched` row
  always wins over a later `duplicate` for the same shift, so the 17:00 re-scan
  that `record_check_in` deliberately keeps out of the punctuality figure never
  becomes the lateness figure here either.
- **`no_shift` and `wrong_location` never match**, because `_match_shift`
  leaves `shift_id` NULL on those rows. The join simply cannot see them.
- **Midnight crossing needs no special case here either**, for exactly the
  reason `_match_shift` documents: the window is around `start_time`, and no
  calendar date enters the query.

Re-deriving the match at payroll time would be a second implementation of
`_match_shift` that can disagree with the first — and it would silently produce
a *different* answer for any shift edited after the scan, because the window is
evaluated against the shift's current start time rather than the one the
employee actually arrived for.

### Denormalisation, and why

`checked_in_at` and `lateness_minutes` are copied onto the entry rather than
read through `check_in_id`. Check-ins are swept at `RETENTION_CHECKINS_DAYS`
(180); a pay record must still be able to say what it was based on after that.
Same reasoning `EmployeeCheckIn.minutes_from_start` is itself denormalised.

`role_name` is likewise a snapshot. A role renamed in March must not silently
rewrite January's payroll export.

---

## API

New router `backend/routers/payroll.py`, `APIRouter(prefix="/payroll",
tags=["payroll"])`, registered in `backend/main.py` after `check_ins.router`.

Every endpoint: `Depends(require_manager)`, then
`await assert_paid_plan(db, company_id, "payroll")`, then every query filtered
by `current_user.company_id`. Schemas live in `backend/schemas/payroll.py`.

### Shared validation

`range_start` and `range_end` are `date`, inclusive. A range is rejected with
`400 {"code": "invalid_range"}` when `range_end < range_start` or when the span
exceeds 62 days — two monthly pay periods, which bounds every query on the page
and is far beyond any real payroll cadence.

`location_id`, where accepted, is optional; omitted means every location in the
caller's company.

### `POST /api/v1/payroll/entries/derive`

Creates the `TimeEntry` rows for a range. Idempotent — safe to call on every
page load, which is what the UI does.

Request: `{"range_start": "2026-09-14", "range_end": "2026-09-20",
"location_id": "ab12cd34"}` (`location_id` optional).

Response `200`:
`{"created": 12, "existing": 31, "exception_count": 3}`

Behaviour: for every `Shift` belonging to a `ShiftSchedule` with
`status == "approved"`, whose derived `pay_date` falls in the range, which has
a matching check-in and no existing `TimeEntry`, insert one with
`source = "checked_in"`. Shifts whose `start_time` is still in the future are
skipped entirely — they are neither entries nor exceptions yet.

This is a POST rather than a GET-with-side-effects because it writes rows.

### `GET /api/v1/payroll/entries`

Query: `range_start`, `range_end`, `location_id?`, `approved?`
(`true` / `false`, omitted = both). Read-only; writes nothing.

Response `200` `PayrollEntriesResponse`:

```json
{
  "rows": [
    {
      "id": "e1f2g3h4",
      "shift_id": "s1s2s3s4",
      "employee_id": "m1m2m3m4",
      "employee_name": "Dana Okafor",
      "location_id": "ab12cd34",
      "location_name": "Flatbush Ave",
      "role_id": "r1r2r3r4",
      "role_name": "Line Cook",
      "pay_date": "2026-09-15",
      "start_time": "2026-09-15T22:00:00-04:00",
      "end_time": "2026-09-16T06:00:00-04:00",
      "paid_minutes": 480,
      "source": "checked_in",
      "checked_in_at": "2026-09-15T21:56:00-04:00",
      "lateness_minutes": -4,
      "attested_by_name": null,
      "attested_at": null,
      "attestation_reason": null,
      "approved_at": null,
      "exported_at": null
    }
  ],
  "total_entries": 43,
  "total_paid_minutes": 19320,
  "approved_entries": 0
}
```

Timestamps are serialised exactly as stored, offset intact. Nothing on this
path calls `.astimezone()` on a shift timestamp — the frontend reads the
wall-clock face off the string (`utils/shiftTime.ts`), per #92.

### `GET /api/v1/payroll/exceptions`

The exception queue. Query: `range_start`, `range_end`, `location_id?`.

Response `200` `PayrollExceptionsResponse`:

```json
{
  "rows": [
    {
      "shift_id": "s9s8s7s6",
      "employee_id": "m1m2m3m4",
      "employee_name": "Dana Okafor",
      "location_id": "ab12cd34",
      "location_name": "Flatbush Ave",
      "role_id": "r1r2r3r4",
      "role_name": "Line Cook",
      "pay_date": "2026-09-16",
      "start_time": "2026-09-16T09:00:00-04:00",
      "end_time": "2026-09-16T17:00:00-04:00",
      "paid_minutes": 480
    }
  ],
  "total": 3
}
```

A shift is an exception when all of: its schedule is `approved`; its derived
`pay_date` is in range; its `start_time` has passed (`datetime.now(timezone.utc)`,
never `date.today()`); it has no matching check-in; and it has no `TimeEntry`.
Attesting a shift removes it from this list on the next load, because it then
has an entry.

### `POST /api/v1/payroll/attest`

Request: `{"shift_id": "s9s8s7s6", "reason": "Phone battery died"}` — `reason`
optional, max 500 characters.

Response `201`: the created entry, same row shape as
`GET /payroll/entries`, with `source = "manager_attested"`,
`attested_by_user_id = current_user.id`, `attested_at = datetime.now(timezone.utc)`,
and `checked_in_at` / `lateness_minutes` NULL — there is no scan to report, and
inventing a lateness of zero would put a fact in the export that nobody
observed.

Errors:

| Status | Code | When |
|---|---|---|
| 404 | `shift_not_found` | The shift does not exist, or belongs to another company |
| 409 | `shift_not_approved` | The shift's `ShiftSchedule.status != "approved"` |
| 409 | `shift_not_started` | `start_time` is still in the future — the issue's rule, and the one that stops a manager pre-attesting a week that has not happened |
| 409 | `entry_exists` | The shift already has a `TimeEntry` (checked-in or attested) |

`entry_exists` is also raised by catching the `IntegrityError` on
`uq_time_entries_shift` and rolling back, so the check-then-insert race resolves
the same way whichever side loses — the pattern `record_check_in` uses for the
counter constraint.

### `POST /api/v1/payroll/approve`

Request: `{"range_start": "2026-09-14", "range_end": "2026-09-20",
"location_id": null, "entry_ids": null}`.

When `entry_ids` is a non-empty list, only those entries are approved (and each
must belong to the caller's company and fall inside the range, or the call
fails `400 invalid_entry_ids`). When it is null or omitted, every unapproved
entry in the range is approved.

Response `200`: `{"approved": 43, "already_approved": 0}`

Sets `approved_at = datetime.now(timezone.utc)` and `approved_by_user_id`.
Already-approved entries are counted and left untouched, not re-stamped: the
approval timestamp is an audit fact about when a human said yes, and a second
click must not rewrite it.

### `POST /api/v1/payroll/export`

Request: `{"range_start": "2026-09-14", "range_end": "2026-09-20",
"location_id": null, "include_exported": false}`.

Response `200`, `media_type="text/csv"`, header
`Content-Disposition: attachment; filename="payroll_{company_slug}_{range_start}_{range_end}.csv"`.

Selects `TimeEntry` rows in the company and range with `approved_at IS NOT NULL`
and — unless `include_exported` is true — `exported_at IS NULL`. Stamps
`exported_at = datetime.now(timezone.utc)` and `payroll_export_id` on every row
it emits, and writes one `payroll_exports` audit row. `include_exported=true`
re-downloads previously exported rows for the same range, leaves their original
`exported_at` in place, and still writes an audit row — a re-download is a
thing that happened and the log should say so.

Errors:

| Status | Code | When |
|---|---|---|
| 409 | `nothing_to_export` | No approved, unexported entries in the range. The message names how many approved-but-already-exported rows were skipped, so "nothing happened" is never mysterious |

A POST rather than a GET despite being a download: it mutates `exported_at` and
writes an audit row, and a browser prefetch of a GET must not silently consume
a pay period. It also keeps the download on the authenticated `fetch` path —
`get_current_user` reads the `Authorization` header only, so a plain
`<a href>` download would arrive unauthenticated (the `token` query parameter
on `GET /export/jsonl/{id}` is accepted and never consulted).

### `GET /api/v1/check-ins/report` — attestation block

The existing endpoint gains one field. `CheckInReportResponse` in
`backend/schemas/check_in.py` grows:

```python
class AttestationRateRow(BaseModel):
    location_id: str
    location_name: str
    entries: int
    attested: int
    rate: float  # attested / entries, 0.0 when entries == 0


class CheckInReportResponse(BaseModel):
    rows: list[CheckInReportRow]
    retention_days: int
    attestation: list[AttestationRateRow]
```

Computed over `time_entries` with `pay_date` inside the same
`RETENTION_CHECKINS_DAYS` window the punctuality rows already use, grouped by
location, ordered by `rate` descending so the locations worth looking at sort
to the top.

**Reporting only.** Nothing reads `rate` to block an attestation, refuse an
export, or cap anything. A location attesting most of its shifts has either a
broken QR flow or something worth a conversation; withholding pay is not the
response to either. Same posture as the weekly abuse report and
`ownership_groups.signup_*`.

---

## Services

### `backend/services/time_entries.py`

Pure functions first — no session, no I/O, directly unit-testable:

```python
def paid_minutes(start: datetime, end: datetime) -> int
def pay_date_for(timezone_name: str, start: datetime) -> date
```

`paid_minutes` works on the **wall-clock faces**, dropping tzinfo, and treats
`end < start` as crossing midnight by adding 24 hours — the same convention as
`_shift_duration_hours` in `backend/scheduling/local_scheduler.py` and
`preferences.py`. A 22:00 → 06:00 shift is 480 minutes whether or not the
generator wrote the end date as the next day, and whether or not a DST
transition falls inside it. That last part is the deliberate choice: scheduled
hours are a wall-clock contract between an employer and an employee, not an
elapsed-instant measurement, and a payroll file that reports 7 hours for a
"22:00–06:00" shift because the clocks went forward is the kind of surprise
that generates a support ticket per location per year.

It differs from `_shift_duration_hours` in one place: **equal faces raise
`ValueError` rather than returning 24 hours.** No shift template produces a
24-hour shift, the schedule update handlers already reject
`start_time == end_time` with a 422, and silently paying someone for a full day
because two timestamps matched is the worst available failure mode. The caller
treats the exception as a malformed shift, logs it, and skips the row — which
is why the `paid_minutes > 0` check constraint can never be the thing a manager
discovers.

`pay_date_for` is the midnight-crossing rule above.

Session-bound functions:

```python
async def derive_time_entries(db, company_id, range_start, range_end,
                              location_id=None) -> DeriveResult
async def list_time_entries(db, company_id, range_start, range_end,
                            location_id=None, approved=None) -> list[TimeEntryRow]
async def list_exceptions(db, company_id, range_start, range_end,
                          location_id=None) -> list[ExceptionRow]
async def attest_shift(db, company_id, shift_id, user, reason) -> TimeEntry
async def approve_entries(db, company_id, range_start, range_end,
                          user, location_id=None, entry_ids=None) -> ApproveResult
async def attestation_rates(db, company_id, since) -> list[AttestationRate]
```

`AttestationRate` is a plain dataclass local to the service; the router maps it
to the `AttestationRateRow` schema, keeping schema imports out of services the
way `backend/routers/check_ins.py` already does for the report rows.

`derive_time_entries` loads the candidate shifts and their locations once, maps
each to its check-in with a single left join, and bulk-inserts. It commits once.
An `IntegrityError` on `uq_time_entries_shift` (a concurrent derive) is caught,
rolled back, and retried once with the already-present shift ids excluded;
a second failure returns what it managed, because a duplicate-key collision here
means the row exists, which is the outcome the caller wanted.

`attest_shift` raises the four documented errors as `HTTPException`s with
`detail={"code": ..., "message": ...}`, matching `CheckInRejected`'s shape as
translated by `backend/routers/check_ins.py`.

### `backend/services/payroll_export.py`

```python
CSV_HEADER = [
    "employee_id", "employee_name", "pay_date",
    "location_id", "location_name", "role_id", "role_name",
    "start_time", "end_time", "paid_hours",
    "source", "checked_in_at", "lateness_minutes",
]

def render_csv(rows: list[PayrollCsvRow]) -> str
async def export_approved(db, company_id, user, range_start, range_end,
                          location_id=None, include_exported=False
                          ) -> tuple[str, PayrollExport]
```

`PayrollCsvRow` is a frozen dataclass in the same module with one field per
entry in `CSV_HEADER`, all typed as the domain value (`date`, `datetime | None`,
`int`, `str`) rather than pre-formatted text — the formatting rules below live
in `render_csv` so they are testable in one place.

`render_csv` is pure. It writes through `csv.writer` into an `io.StringIO` with
`lineterminator="\r\n"` (RFC 4180, which is what Excel and every payroll import
template expect) and prefixes a UTF-8 BOM (`"﻿"`). The BOM is not
decoration: we ship in 19 locales including Arabic, Bengali, Tamil and Telugu,
employee names are entered in those scripts, and Excel on Windows renders a
BOM-less UTF-8 CSV as mojibake — which a payroll clerk will read as our bug.

Column rules:

- `pay_date` — `YYYY-MM-DD`.
- `start_time`, `end_time` — ISO 8601, rendered in the location's zone with the
  offset intact (same instant, local faces).
- `paid_hours` — `paid_minutes / 60` formatted to two decimal places
  (`"8.00"`, `"7.50"`). Hours rather than minutes because that is what every
  payroll import template on the issue's list takes.
- `source` — `checked_in` or `manager_attested`, verbatim. Provenance travels
  with every exported hour, per the issue.
- `checked_in_at` — ISO 8601, or empty for an attested row.
- `lateness_minutes` — signed integer, or empty for an attested row. Empty, not
  `0`: we did not observe an on-time arrival, we observed nothing.

`export_approved` selects, renders, stamps `exported_at` and
`payroll_export_id`, inserts the `payroll_exports` row, and commits once so the
audit row and the stamps cannot disagree. It resolves `ownership_group_id` via
`backend.services.billing.get_ownership_group_id`, which returns `None` for the
OG-less dev company — hence the nullable column.

The attestation reason is deliberately **not** a CSV column. It is an internal
audit note about one employee, written by their manager, and it does not belong
in a file that gets emailed to a payroll bureau. It is visible in the UI and in
`GET /payroll/entries`.

### Retention

One new step in `backend/services/data_retention.py`, appended as step 9 after
the signup-signals sweep:

```python
# 9. Payroll export audit rows past their window (#78). The entries they
#    describe are NOT deleted — exported_at is the idempotency marker and
#    outliving the log is the point. Only the link is cleared.
cutoff_payroll_logs = now - timedelta(days=settings.RETENTION_PAYROLL_EXPORT_LOGS_DAYS)
```

It runs an `UPDATE time_entries SET payroll_export_id = NULL` for entries whose
export row is about to go, then the `DELETE`, and reports
`summary["payroll_export_logs_deleted"]`. Order matters: the FK would otherwise
block the delete.

`exported_at` survives. Deleting an audit log must never un-export a pay period
and make it eligible for a second push.

Step 7 (check-ins) gains one line for the same reason in the other direction:
before deleting expired `employee_check_ins`, null `time_entries.check_in_id`
for the rows about to be removed. `checked_in_at` and `lateness_minutes` stay —
they are denormalised precisely so the pay record outlives the scan.

`time_entries` and their `exported_at` are **not** swept. They are pay records,
and no retention period for them was decided in #78; picking one here would be
scope creep with a compliance edge. Their growth is bounded by the roster and
the calendar, and `StorageSnapshot` already measures per-tenant footprint.

### Config

One setting in `backend/config.py`, in the retention block after
`RETENTION_SIGNUP_SIGNALS_DAYS`:

```python
# Payroll export audit rows (#78). A year, matching
# RETENTION_REVOKED_CONSENTS_DAYS: an export is a pay-affecting action and
# the question "who pulled that file, when, covering which dates" is one a
# customer asks during an audit, which is an annual cycle. Deleting the log
# does NOT clear time_entries.exported_at — that marker is what stops a pay
# period being exported twice and it outlives the log deliberately.
RETENTION_PAYROLL_EXPORT_LOGS_DAYS: int = 365
```

No new environment variable is required in Terraform; `Settings` supplies the
default and the value is not a secret.

---

## Frontend

### One page

`frontend/src/pages/manager/Payroll.tsx`, routed at `path="payroll"` inside the
manager `<Route>` block in `frontend/src/App.tsx`, and added to
`frontend/src/components/layout/Sidebar.tsx` in the existing `groupCheckIn`
group after `checkInReport`:

```ts
{ to: "/manager/payroll", labelKey: "payroll" },
```

It sits with check-in rather than with scheduling because it consumes check-ins
and because the attestation rate it produces is read on the report next to it.

Layout, top to bottom:

1. **Date range** — two `<input type="date">` controls, defaulting to the
   Monday–Sunday week that ended most recently, computed from `new Date()` in
   the browser and formatted as `YYYY-MM-DD`. The browser's local date is the
   right default here precisely because a manager picking "last week" means
   their own week; the *backend* never infers a range, so the "today is UTC"
   rule — which governs Python application code and tests — is not in tension
   with it. An optional location `<select>` populated from `listLocations()`.
2. **Exception queue** — table of `PayrollExceptionRow`, each with an
   **Attest** button opening a small inline form (optional reason, confirm).
   Empty state: "Every approved shift in this range has a check-in."
3. **Entries** — table of `TimeEntryRow`: employee, date, location, role,
   start, end, paid hours, source badge, check-in time, lateness. Rows already
   exported are muted and carry their `exported_at`. A header checkbox and
   per-row checkboxes select the subset passed as `entry_ids` to approve.
4. **Actions** — **Approve selected** / **Approve all in range**, then
   **Download CSV**, disabled with an explanatory line when there is nothing
   approved and unexported.

On mount and on every range or location change the page calls
`deriveTimeEntries` first, then `listTimeEntries` and `listPayrollExceptions`
in parallel. A `402` from any of them renders the paid-plan upsell copy rather
than an error toast — free-plan managers reach the page only by typing the URL,
and the honest answer is that this is a paid feature.

Times are rendered through `formatTime` / `extractTime` from
`frontend/src/utils/shiftTime.ts`. No `new Date(...)` is constructed from a
shift timestamp anywhere on this page — that is the #92 bug that shows a London
manager a New York 9am shift as 2pm.

All spacing, alignment and border utilities are **logical**: `text-start`,
`ms-*`, `me-*`, `border-s`, `start-0`. `ar` and `ur` are RTL and
`LanguageContext` sets `document.dir`;
`frontend/src/utils/logicalDirection.test.ts` fails the build otherwise.

### API wrapper

`frontend/src/api/payroll.ts`, matching the shape of
`frontend/src/api/checkIns.ts`:

```ts
export function deriveTimeEntries(req: PayrollRangeRequest): Promise<PayrollDeriveResult>
export function listTimeEntries(range, locationId?, approved?): Promise<PayrollEntriesResponse>
export function listPayrollExceptions(range, locationId?): Promise<PayrollExceptionsResponse>
export function attestShift(shiftId: string, reason?: string): Promise<TimeEntryRow>
export function approveTimeEntries(req: PayrollApproveRequest): Promise<PayrollApproveResult>
export function downloadPayrollCsv(req: PayrollExportRequest): Promise<{ blob: Blob; filename: string }>
```

The first five go through `apiFetch`. `downloadPayrollCsv` cannot: `apiFetch`
ends in `res.json()`. It uses `fetch` directly with the same
`Authorization: Bearer` header, reads `res.blob()`, parses the filename out of
`Content-Disposition`, and throws `ApiError` on a non-OK status so the page's
error handling is unchanged. The page then creates an object URL, clicks a
synthetic anchor, and revokes the URL. This is the reason export is a POST — it
keeps the credential in a header instead of a query string.

Types go in `frontend/src/types/index.ts` beside the check-in block:
`TimeEntrySource` (`"checked_in" | "manager_attested"`), `TimeEntryRow`,
`PayrollExceptionRow`, `PayrollEntriesResponse`, `PayrollExceptionsResponse`,
`PayrollDeriveResult`, `PayrollApproveResult`, `AttestationRateRow`, and
`attestation: AttestationRateRow[]` added to the existing `CheckInReport` type.

### i18n — all 19 locales

`TranslationKeys` is `typeof import("./en").default`, so a locale file missing a
key is a **TypeScript error and `npm run build` fails**. The keys below must
therefore be added, translated, to **every one of the 19 locale files**:
`en`, `zh`, `hi`, `ar`, `fr`, `es`, `pt`, `bn`, `ru`, `ur`, `id`, `de`, `pcm`,
`te`, `tr`, `ta`, `vi`, `ja`, `mr`. Not just `en`. Not English placeholders in
the other 18.

New nav key, beside `checkInReport` in the nav block of each file:

- `payroll` — "Payroll"

New `payroll` block in each file:

| Key | English |
|---|---|
| `title` | "Payroll Hours" |
| `desc` | "Approved shifts with a check-in become payable hours. Shifts with no check-in wait below for a manager to confirm them." |
| `rangeStart` | "From" |
| `rangeEnd` | "To" |
| `location` | "Location" |
| `allLocations` | "All locations" |
| `exceptionsTitle` | "Needs confirmation" |
| `exceptionsDesc` | "These shifts were scheduled and approved, but nobody checked in. Confirm the ones that were worked so they get paid." |
| `exceptionsEmpty` | "Every approved shift in this range has a check-in." |
| `attest` | "Confirm worked" |
| `attestReason` | "Reason (optional)" |
| `attestConfirm` | "Confirm" |
| `attestCancel` | "Cancel" |
| `attestFailed` | "Could not confirm that shift. Reload and try again." |
| `entriesTitle` | "Payable hours" |
| `entriesEmpty` | "No payable hours in this range yet." |
| `columnEmployee` | "Employee" |
| `columnDate` | "Date" |
| `columnLocation` | "Location" |
| `columnRole` | "Role" |
| `columnStart` | "Start" |
| `columnEnd` | "End" |
| `columnPaidHours` | "Paid hours" |
| `columnSource` | "Source" |
| `columnCheckedInAt` | "Checked in" |
| `columnLateness` | "vs. start" |
| `sourceCheckedIn` | "Checked in" |
| `sourceAttested` | "Confirmed by manager" |
| `attestedBy` | "Confirmed by {name} on {date}" |
| `latenessEarly` | "{minutes} min early" |
| `latenessLate` | "{minutes} min late" |
| `latenessOnTime` | "On time" |
| `approveSelected` | "Approve selected" |
| `approveAll` | "Approve all in range" |
| `approved` | "Approved" |
| `approvedCount` | "{count} entries approved." |
| `download` | "Download CSV" |
| `downloadDesc` | "Approved hours only. Each entry exports once." |
| `nothingToExport` | "Nothing new to export. Approve some hours first." |
| `alreadyExported` | "Exported {date}" |
| `totalHours` | "{hours} hours across {count} shifts" |
| `paidPlanOnly` | "Payroll export is a paid-plan feature. Upgrade to enable it." |
| `loadFailed` | "Could not load payroll hours. Try again." |

Plus two keys in the existing `checkIn` block of every locale, for the
attestation panel on the report page:

| Key | English |
|---|---|
| `attestationTitle` | "Manager-confirmed shifts" |
| `attestationDesc` | "Share of payable hours confirmed by a manager instead of a check-in, per location, over the last {days} days. High is worth a look, not a penalty." |

---

## Testing

Backend tests are `pytest` + `pytest-asyncio` under `tests/`, one file per
concern, following the `test_check_in_*.py` split.

**`tests/test_time_entry_model.py`**
- `uq_time_entries_shift` rejects a second entry for the same shift.
- `time_entries_source_check` rejects an unknown `source`.
- `time_entries_attested_check` rejects `source='manager_attested'` with a NULL
  `attested_by_user_id`.
- `time_entries_paid_minutes_check` rejects zero.

**`tests/test_time_entries_service.py`** — the pure functions, no DB:
- `paid_minutes` for an ordinary 09:00→17:00 day = 480.
- `paid_minutes` for 22:00→06:00 written with the **same** date = 480 (the
  `end <= start` branch).
- `paid_minutes` for 22:00→06:00 written with the **next** date = 480.
- `paid_minutes` across a spring-forward date in `America/New_York` still = 480
  — the wall-clock contract, asserted so a future "fix" to instant arithmetic
  fails loudly.
- `paid_minutes` raises `ValueError` on equal faces.
- **Timezone case, location west of UTC:** `pay_date_for("America/New_York",
  datetime(2026, 1, 4, 22, 0, tzinfo=ZoneInfo("America/New_York")))` returns
  `date(2026, 1, 4)` even though the UTC instant is `2026-01-05T03:00Z`. The
  naive `.date()` on a UTC value returns the 5th, so this test is the one that
  fails if anyone drops the `astimezone`.
- **Second timezone case:** `pay_date_for("America/Los_Angeles",
  datetime(2026, 3, 1, 23, 0, tzinfo=ZoneInfo("America/Los_Angeles")))` returns
  `date(2026, 3, 1)`; the UTC instant is on the 2nd.
- `pay_date_for("Asia/Tokyo", ...)` for an 08:00 local start whose UTC instant
  is the previous day, to cover the east-of-UTC direction too.

**`tests/test_time_entries_derive.py`** — DB-bound derivation:
- A `matched` check-in on an approved shift produces one entry with
  `source='checked_in'`, `checked_in_at` and `lateness_minutes` copied.
- A `duplicate` check-in carrying the shift's id also produces an entry.
- A shift with both `matched` and `duplicate` rows takes the **earliest**
  `checked_in_at`, so the 17:00 re-scan never becomes the lateness figure.
- A `no_shift` / `wrong_location` check-in produces nothing (its `shift_id` is
  NULL).
- A shift on a `draft` schedule produces nothing.
- A future shift is neither an entry nor an exception.
- Re-running `derive_time_entries` creates zero new rows.
- A shift in another company is invisible.
- A midnight-crossing shift at a `America/New_York` location lands on the
  start date's range and is excluded from a range beginning the next morning.

**`tests/test_payroll_attest.py`**
- Attesting a started, approved, un-checked-in shift creates an entry with the
  attesting user, timestamp and reason.
- Attesting before `start_time` → 409 `shift_not_started`.
- Attesting a shift that already has an entry → 409 `entry_exists`.
- Attesting a draft-schedule shift → 409 `shift_not_approved`.
- Attesting another company's shift → 404 `shift_not_found`.
- The attested entry has NULL `checked_in_at` and NULL `lateness_minutes`.

**`tests/test_payroll_api.py`**
- Every endpoint returns 402 `payroll_requires_paid_plan` for a free OG.
- Every endpoint returns 403 for a non-manager user.
- `range_end < range_start` → 400 `invalid_range`; a 90-day span → 400.
- Export with no approved entries → 409 `nothing_to_export`.
- Approve → export returns CSV, stamps `exported_at` and `payroll_export_id`,
  writes exactly one `payroll_exports` row with matching `entry_count` and
  `paid_minutes_total`.
- A second export of the same range → 409 `nothing_to_export`.
- `include_exported=true` returns the rows again, leaves the original
  `exported_at` unchanged, and writes a second audit row.
- Approving twice reports `already_approved` and does not move `approved_at`.
- `entry_ids` naming another company's entry → 400 `invalid_entry_ids`.

**`tests/test_payroll_export_csv.py`** — `render_csv` only, pure:
- Header row matches `CSV_HEADER` exactly, in order.
- Output starts with the BOM and uses `\r\n`.
- `paid_hours` renders `"8.00"` for 480 and `"7.50"` for 450.
- An attested row has empty `checked_in_at` and empty `lateness_minutes` — the
  literal empty string, not `"0"` and not `"None"`.
- A negative `lateness_minutes` renders `"-4"`.
- An employee name containing a comma and one in Devanagari both round-trip
  through `csv.reader`.
- `attestation_reason` appears nowhere in the output.

**`tests/test_payroll_retention.py`**
- A `payroll_exports` row older than `RETENTION_PAYROLL_EXPORT_LOGS_DAYS` is
  deleted and counted in the summary.
- Its entries keep `exported_at` and have `payroll_export_id` nulled.
- A newer row survives.
- A check-in swept by step 7 nulls `time_entries.check_in_id` while
  `checked_in_at` and `lateness_minutes` remain.

**`tests/test_check_in_report_attestation.py`**
- `GET /check-ins/report` returns an `attestation` row per location with the
  correct `attested` / `entries` / `rate`.
- A location with zero entries reports `rate == 0.0` and does not divide by
  zero.
- A 100% attestation rate blocks nothing: attest, approve and export all still
  succeed afterwards. This is the test that fails if somebody later wires
  enforcement onto a reporting number.

**`tests/test_utc_today.py`** needs no change but constrains the code: every
"now" in this feature is `datetime.now(timezone.utc)`, in application code and
in the tests, and the AST sweep fails the build otherwise.

### A SQLite caveat the tests must respect

The suite runs on `sqlite+aiosqlite`, which ignores `DateTime(timezone=True)`
and strips tzinfo on round-trip — the trap documented at length in
`_as_utc` in `backend/services/check_in.py`. A shift genuinely written as
`22:00-05:00` reads back naive as `22:00` and is then read as `22:00Z`, four
hours early.

Two rules follow, and they are why `pay_date_for` takes a timezone name and a
datetime rather than a `Shift` and a `Location`:

1. The timezone assertions above call `pay_date_for` **directly**, on datetimes
   constructed in the test with `ZoneInfo(...)` tzinfo. They never go through a
   SQLite round-trip, so they test the rule rather than the driver.
2. Shift fixtures that *do* go through the DB are built with
   `tzinfo=timezone.utc` directly, exactly as `tests/test_check_in_service.py`
   already does, and never from an offset-bearing ISO string. Their assertions
   are about counts, statuses and constraints, not about which local date a
   `-05:00` timestamp lands on.

### Frontend tests

Unit tests only where a pure helper exists, per the house pattern
(`preferenceText.test.ts`, `shiftTime.test.ts`).

`frontend/src/utils/payrollHours.ts` holds the two formatters the page needs:

```ts
export function formatPaidHours(minutes: number): string
export function formatLateness(minutes: number | null): string
```

`frontend/src/utils/payrollHours.test.ts` covers: 480 → `"8.00"`; 450 →
`"7.50"`; 0 → `"0.00"`; `null` lateness → `""`; `0` → on-time; `-4` → early
branch; `7` → late branch. No component test for `Payroll.tsx` — there is no
component test harness in this repo and adding one is not this slice's job.

---

## Migration and Retention

**Migration `0036_add_time_entries_and_payroll_exports.py`**, `revision = "0036"`,
`down_revision = "0035"`, matching the numbering and the
`from typing import Sequence, Union` header style of `0030` and `0035`.

`upgrade()` creates `payroll_exports` first (`time_entries.payroll_export_id`
references it), then `time_entries`, then the four `time_entries` indexes and
the one `payroll_exports` index. Named constraints throughout, so `downgrade()`
and any future `alter_column` have something to name.

`downgrade()` drops the indexes, then `time_entries`, then `payroll_exports`.

No backfill. Every entry is derived on demand from approved shifts and existing
check-ins, so history becomes available the moment a manager picks a past range
— bounded by `RETENTION_CHECKINS_DAYS`, since a swept check-in can no longer
gate anything. That bound is worth stating to customers: **payable hours can
only be derived for the last 180 days**, after which the shift is an exception
and needs attestation.

Retention additions are the two changes to
`backend/services/data_retention.py` and the one setting in
`backend/config.py` described under *Services*. `backend/scripts/run_retention.py`
needs no change — it prints whatever `run_data_retention` returns.

---

## Future Adapter Seam

Nothing in this slice introduces the `PayrollProvider` protocol, and nothing
depends on its absence either. The reason the seam is cheap later is that the
data model is already provider-agnostic: a `TimeEntry` knows what was worked,
when, by whom, under which role, how it was verified, who approved it and
whether it has been sent. None of those facts are CSV-shaped. The one column
that even mentions transport is `payroll_exports.format`, which is a
constrained string with `'csv'` as its only current value, and
`time_entries.payroll_export_id`, which points at an export event rather than a
file. Adding `'finch'` or `'gusto'` to the check constraint is a one-line
migration.

When an adapter layer does arrive it sits **behind the same approval and export
path, not beside it**. `POST /payroll/export` grows an optional `provider`
field defaulting to `csv`; `export_approved` keeps its current job of selecting
approved, unexported entries in a range and stamping them, and hands the
selected rows to a strategy chosen by that field — `render_csv` today, a
`PayrollProvider.push_time_entries` implementation tomorrow. The approval gate,
the `exported_at` idempotency marker, the audit row and the retention sweep are
all upstream or downstream of that single swap and do not move. What a vendor
adapter genuinely adds is new tables next to these, not changes inside them: a
credential store keyed by ownership group, a `worker_links` table mapping
`employee_id` to a provider worker id, a `pay_periods` table cached from the
provider, and `payroll_exports.provider_export_id` for the remote receipt. The
per-OG cooldown from `integration_import_quota.py` becomes relevant at the same
moment, because that is the first version that makes a paid third-party call —
which is why `payroll_exports` is already shaped like `integration_imports`
with `ownership_group_id` and `created_at` in place, ready to be counted.

---

## Open Questions Resolved

The issue left six open questions of its own, and building the core surfaced
several more that it did not ask. Everything a shippable slice had to decide is
decided below; everything that only becomes a real question once a provider
exists is listed after it, undecided on purpose.

**Resolved here:**

- **Which pay date do midnight-crossing hours land on?** The location-local
  date the shift *started*, whole and unsplit. It agrees with
  `EmployeeCheckIn.local_date`, with `Shift.date`, and with the one day a human
  would name if you asked them when that shift was.
- **Does a `duplicate` check-in count as arrival evidence?** Yes. `duplicate`
  answers a punctuality question, not an attendance one, and on a split-shift
  day the second shift's real arrival is recorded as `duplicate`. The earliest
  scan for a shift owns the lateness figure, so the late re-scan that #63
  deliberately keeps out of the report stays out of payroll too.
- **Are unpaid breaks in the model?** No. There is no break data in the product,
  and a column that is always zero is a claim we cannot support.
- **Immutability after export** (the issue's question, in its Phase-1 form):
  `exported_at` is a one-way marker and a re-export of the same range is refused
  with `nothing_to_export`. `include_exported=true` exists for re-downloading a
  file a manager lost, and never re-stamps. Reversal entries wait for a
  provider that can accept one.
- **Where does the attestation reason go?** UI and API, not the CSV. It is an
  internal note about one employee and the CSV leaves the building.

**Deliberately not answered, because this slice cannot:** push versus pull,
rate ingestion, one connection per company or per ownership group, partial-push
failure handling, and whether payroll is in the base paid tier or its own
add-on against #64's credits split. Each needs a provider to be a real question;
`assert_paid_plan(db, company_id, "payroll")` gates on the paid plan today and
its feature string is the hook if a separate entitlement is decided later.
