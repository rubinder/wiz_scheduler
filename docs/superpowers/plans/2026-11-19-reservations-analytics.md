# AI Host Analytics Pipeline Implementation Plan (step 10 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A nightly batch job writes the previous UTC day of agent data to
a partitioned Parquet lake. The lake holds no diner identifiers. Athena can
query it through Glue tables, and SQL models are provided. The same job
writes daily rollups to Postgres for an in-app dashboard. Data-quality
checks fail the job loudly.

**Architecture:**
- `backend/analytics/` has one module per stage:
  - `extract.py`: SQL reads for one UTC day.
  - `redact.py`: removes diner identifiers and redacts transcripts.
  - `lake.py`: writes Parquet with pyarrow to S3, or to a local directory
    in tests and dev. Writing a partition is idempotent: it is replaced,
    never appended to.
  - `metrics.py`: computes the daily rollups into `agent_daily_metrics`.
  - `quality.py`: the data-quality checks.
  - `run.py`: orchestration, plus `--date` and `--backfill`.
- `backend/scripts/run_agent_analytics.py` is the ECS entry point.
- Terraform adds the lake bucket, the Glue database and tables (partition
  projection, so no crawler), an Athena workgroup, and the nightly
  schedule. All of it sits behind `analytics_enabled`, which defaults to
  false.
- Athena SQL models live in `analytics/sql/` as views.
- The dashboard is a manager page that reads `agent_daily_metrics` and
  draws charts with recharts, which is already installed.

**Tech Stack:** Python with pyarrow, a new dependency that the owner
approved with the spec. boto3 (installed), SQLAlchemy and Alembic.
Terraform with AWS provider ~> 5. React with recharts.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
§10, plus §11 (privacy) and §12 step 10.

## Global Constraints

- **New dependency:** `pyarrow` only, pinned to an exact version in
  `backend/requirements.txt`. Pick the latest release that is at least two
  weeks old and has wheels for Python 3.11 on linux/amd64 and on macOS
  arm64.
- **The lake holds no diner names, phone numbers or emails.**
  - Phone numbers and caller numbers are replaced by the existing HMAC
    source key (`source_key_for`). Names and emails are dropped.
  - `user_turn` text is redacted with `redact.text`, which masks phone-like
    digit runs (7 or more digits), emails, and 6-character confirmation
    codes.
  - Assistant text gets the same redaction.
  - Tool call and tool result payloads keep only an allowlist of keys:
    `name`, `party_size`, `start`, `end`, `outcome`, `code_present`
    (a bool), `error`. Every other key is dropped.
  - A test runs every redaction over seeded rows that contain names, phone
    numbers and emails, and asserts that none of them appear in the
    Parquet bytes.
- **Tables landed, one per Parquet dataset, each partitioned by
  `dt=YYYY-MM-DD`:**
  - `conversations`
  - `events`
  - `reservations` (WizScheduler bookings plus mock_external, with a
    `provider` column)
  - `agent_usage`
  - `screened_calls`
  - `eval_results`, loaded from the `evals-runs/*.json` files when they
    exist locally; skipped in ECS. Document this.
- **Idempotency.** Rerunning a date deletes that day's S3 objects (or local
  directory) and its `agent_daily_metrics` rows, then writes them again,
  all inside one job run.
- **Data-quality checks.** Any failure exits 1 and sends an operator alert
  through `operator_alerts`.
  - Landed row counts equal the source counts for each table.
  - Every latency is non-negative.
  - Every conversation with outcome `booked` has a reservation ref that
    resolves through its provider.
  - Billed amounts reconcile: the day's sum of `agent_usage.billed_usd`
    equals the sum of billed amounts in that day's conversations. This is
    an internal reconciliation. The live Stripe comparison stays in the
    step 5 reconcile job; document this.
- **Metrics** (per location per day): see Task 2. The app never queries
  Athena at request time.
- **Terraform:** every new resource is count-gated on
  `var.analytics_enabled`, which defaults to false. With default vars the
  plan is empty, and the ECS task definition must not change.
- **"Today" is UTC** (CLAUDE.md). The job's default date is yesterday in
  UTC.

## Review Focus

1. **A diner phone number in free text** (for example "call me on 415 555
   0100") never reaches the lake. Tested in Task 1.
2. **Rerunning the same date never doubles any row**, in either the lake or
   `agent_daily_metrics`. Tested in Tasks 1 and 2.
3. **A day with no conversations** writes empty partitions and zero
   metrics rows without errors, and the quality checks pass. Tested in
   Task 2.
4. **Conversations that span midnight** belong to the day they started on,
   both for extraction and for metrics. Events belong to their own
   timestamp's day, and are joined back by conversation id. Tested in
   Task 1.
5. **The dashboard shows only the manager's own locations**, following the
   LocationScope rules. Tested in Task 4.

---

### Task 1: Extract, redact, land

**Files:**
- `backend/analytics/__init__.py`
- `backend/analytics/extract.py`
- `backend/analytics/redact.py`
- `backend/analytics/lake.py`
- `backend/requirements.txt` (pyarrow)
- tests

**Interfaces:**
- `async extract.day(db, day: date) -> dict[str, list[dict]]`
  - Runs as the table owner (no RLS binding), across tenants, read-only.
  - Rows in the result are already redacted.
- `redact.text(s: str | None) -> str | None`
- `redact.payload(d: dict) -> dict`
- `lake.write_partition(table: str, day: date, rows: list[dict], schema: pa.Schema, *, sink: Sink) -> int`
  - `Sink` is either `S3Sink(bucket, prefix, client)` or
    `LocalSink(root: Path)`.
  - Writes `table/dt=YYYY-MM-DD/part-0.parquet`, replacing what was there.
  - Uses fixed, explicit pyarrow schemas per table, with timestamps in UTC
    and money as `decimal128(12, 6)`.
- [ ] Write tests:
  - Redaction cases: phone-like numbers, emails, codes, names absent.
  - The no-identifiers byte scan.
  - Schemas are stable.
  - Writing the same partition again replaces it.
  - A conversation spanning midnight lands on its start day.
  - Mock bookings land with `provider`.
- [ ] Commit `feat(analytics): extract, redact and land agent data as partitioned Parquet (#259)`

### Task 2: Daily metrics, quality checks, job

**Files:**
- `backend/analytics/metrics.py`
- `backend/analytics/quality.py`
- `backend/analytics/run.py`
- `backend/scripts/run_agent_analytics.py`
- migration `0066_agent_daily_metrics.py`
- tests

**Interfaces:**
- `AgentDailyMetric(id, company_id, location_id, day date, conversations int, resolved int, booked int, handed_off int, abandoned int, failed int, handoff_reasons JSONB, llm_p50_ms, llm_p95_ms, turn_p50_ms, turn_p95_ms, cost_usd Numeric(12,6), billed_usd Numeric(12,2), screened int, blocked int, covers_by_period JSONB, created_at)`
  - Unique on `(location_id, day)`.
  - RLS keyed on company (copy 0063).
  - Retained for the same window as reservations.
- Definitions:
  - `resolved` = booked + modified + cancelled.
  - Resolution rate = resolved / conversations.
  - Booking conversion = booked / conversations with a booking intent
    (any conversation that called `find_slots`).
  - Spam-blocked share = blocked / screened.
  - Covers by period = the sum of party sizes of booked reservations that
    start that day, grouped by rules service period name.
  - Percentiles use the nearest rank method.
- `quality.check(day, extracted, landed_counts, db) -> list[str]`. An empty
  list means pass.
- `run.run(day, sink, db)`:
  1. Extract.
  2. Land.
  3. Check quality.
  4. Delete and reinsert that day's metrics in one transaction.
  5. Log a summary with no PII.
  6. On a quality failure: send an operator alert and exit 1. Write
     nothing to metrics for that day.
- Script options:
  - `--date YYYY-MM-DD` (default: yesterday UTC)
  - `--backfill START END`
  - `--local PATH` (uses `LocalSink`)
  - Otherwise uses `S3Sink` with `settings.ANALYTICS_BUCKET`. If that is
    empty and `--local` is not given, exit 2.
- [ ] Write tests:
  - Each metric on a seeded day.
  - An empty day.
  - A rerun is idempotent.
  - Each quality check fails on a crafted defect, sends an alert and
    exits 1.
  - Backfill iterates the dates.
- [ ] Commit `feat(analytics): daily agent metrics, data-quality checks and the nightly job (#259)`

### Task 3: Terraform, Glue, Athena models

**Files:**
- `terraform/analytics.tf`
- `terraform/variables.tf` (`analytics_enabled` = false, `analytics_schedule` = `"cron(30 6 * * ? *)"`)
- `analytics/sql/*.sql` (Athena views)
- `analytics/README.md`
- `tests/infra` gating test
- ECS env (conditional concat, so the task definition is unchanged when off)

**Resources** (all count-gated):
- S3 lake bucket: private, SSE-S3, bucket-owner-enforced, public access
  block, lifecycle that expires objects after `var.analytics_retention_days`
  (default 365).
- S3 bucket for Athena results: private, 30-day lifecycle.
- `aws_glue_catalog_database`.
- One `aws_glue_catalog_table` per landed table, with explicit columns
  matching the pyarrow schemas, Parquet SerDe, and partition projection on
  `dt` (type date, range from 2026-11-01 to NOW).
- `aws_athena_workgroup`, with the result location and enforced
  configuration.
- IAM:
  - ECS task role: put/delete on the lake prefix only.
  - An analyst role or policy for Athena and Glue read access. Document
    how the owner assumes it.
- Nightly ECS schedule, cloned from the reconcile schedule, with the
  command `python -m backend.scripts.run_agent_analytics`.

**SQL models** (one view each):
- `daily_resolution_rate`
- `booking_conversion`
- `handoff_reasons`
- `latency_percentiles` (Athena `approx_percentile`)
- `cost_per_booking`
- `spam_blocked_share`
- `covers_by_service_period`

**Tests:**
- [ ] `fmt` and `validate` pass with analytics off and on.
- [ ] The gating test is extended.
- [ ] A test checks that the Glue column lists match the pyarrow schemas by
  name and type. Parse the Terraform locals, or generate the columns from
  one Python source and check the file in.
- [ ] Commit `feat(analytics): lake bucket, Glue tables, Athena workgroup and SQL models, off by default (#259)`

### Task 4: In-app dashboard

**Files:**
- `GET /api/v1/reservations/insights?location_id=&from=&to=` (default the
  last 30 days, at most 180), reading `agent_daily_metrics`, scope-checked.
- `frontend/src/pages/manager/ReservationsInsights.tsx`
- nav entry "Insights" in the Reservations group
- api module
- recharts charts:
  - resolution and conversion lines
  - handoff reasons bar
  - p50/p95 latency lines
  - cost and billed per booking
  - spam-blocked share
  - covers by period stacked bar
- KPI tiles: last 7 days compared with the previous 7.
- i18n ×19.
- Empty state: "Insights appear the day after the AI host's first
  conversations".

**Tests:**
- [ ] Backend:
  - scope (a manager scoped to location A can't read location B)
  - range bounds
  - empty response
- [ ] Frontend helpers: the 7-day deltas, percentage formatting, and gap
  filling for missing days.
- [ ] `npm test`, `tsc`, and the build pass.
- [ ] Commit `feat(analytics): AI host insights dashboard (#259)`

### Task 5: Docs

- Decisions log entries:
  - Lake privacy: no identifiers, HMAC keys, the payload allowlist.
  - Idempotent partitions.
  - Internal billed reconciliation, with Stripe checked in step 5.
  - Partition projection instead of crawlers.
  - The app never queries Athena.
  - Eval results land only from local runs.
- Owner checklist: set `analytics_enabled`, set `ANALYTICS_BUCKET`, run a
  backfill from the first day reservations were enabled, and try one
  Athena query.
- Architecture reference: entry points for `backend/analytics/`. This is a
  new bounded context, so add a row to SKILL.md's table. That is a
  protected path, so the owner reviews it.
- [ ] Commit `docs(analytics): pipeline decisions and switch-on steps (#259)`
