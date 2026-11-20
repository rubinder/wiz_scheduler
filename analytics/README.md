# AI host analytics lake (#259 step 10)

The nightly job `python -m backend.scripts.run_agent_analytics` lands one
Parquet partition per table per UTC day at
`s3://<lake bucket>/lake/<table>/dt=YYYY-MM-DD/part-0.parquet` and writes
`agent_daily_metrics` rows in Postgres. With no arguments it runs D-1 and D-2
(UTC). The app reads only `agent_daily_metrics` and never queries Athena at
request time; Athena is for the owner's ad hoc analysis.

The lake is anonymous: no diner names, phone numbers, emails, confirmation
codes, free text or pseudonymous diner keys (see `backend/analytics/schemas.py`).

## Infrastructure

Everything is in `terraform/analytics.tf`, off by default behind
`analytics_enabled` (see `terraform/README.md`, "Analytics lake"). With it
on, Terraform creates the lake and Athena results buckets, the Glue database
`wizscheduler_analytics` with one table per lake table (partition projection
on `dt`, so new days need no crawler or `MSCK REPAIR`), the Athena workgroup
`wizscheduler-analytics`, the MFA-only `wizscheduler-analytics-reader` IAM
role and the nightly schedule.

The Glue columns are generated from the pyarrow schemas. After changing
`backend/analytics/schemas.py`:

    python -m backend.analytics.glue_columns > terraform/analytics_columns.tf.json

`tests/analytics/test_glue_columns.py` fails while the checked-in file is
stale.

## Tables

`conversations`, `events`, `reservations`, `agent_usage`, `screened_calls`
and `eval_results`. `eval_results` is loaded from local `evals-runs/*.json`
files only, so the ECS job never lands it; run the script with `--local`
(or against the bucket) on a machine that has run evals.

## Views

One file per model in `analytics/sql/`, Athena (Trino) SQL against the Glue
tables. They are not managed by Terraform. Apply (or re-apply after editing)
with:

    analytics/apply_views.sh [workgroup] [database]

which runs each file through `aws athena start-query-execution` and waits for
it. Every file is `CREATE OR REPLACE VIEW`, so rerunning is safe.

| View | What |
|------|------|
| `daily_resolution_rate` | resolved (booked, modified, cancelled) / conversations |
| `booking_conversion` | booked / conversations with a `find_slots` tool call |
| `handoff_reasons` | handoffs by reason code |
| `latency_percentiles` | model and turn latency p50/p95 (`approx_percentile`) |
| `cost_per_booking` | model cost and billed amount per booked conversation |
| `spam_blocked_share` | blocked / screened calls |
| `covers_by_service_period` | covers by UTC start date and hour |

Notes:

- Filter on `dt` when querying a view; projection prunes partitions only
  when the predicate reaches the table. `booking_conversion` looks intent
  up in events partitions at or after the conversation's `dt` (events never
  predate the conversation); Athena's dynamic filtering can skip earlier
  partitions, but later ones are still read.
- `latency_percentiles` is approximate; `agent_daily_metrics` uses nearest
  rank, so the two can differ slightly.
- The lake holds no location time zone or rules, so
  `covers_by_service_period` buckets by UTC hour. Named service periods in
  the location's local time are `agent_daily_metrics.covers_by_period`.
- A reservation lands on the day it was created, with its status as of that
  run; later changes are not reflected in the lake.
- Billed amounts reconcile internally (`agent_usage.billed_usd` against the
  conversations) in the job's data-quality checks. The comparison with live
  Stripe stays in the daily agent reconcile job (step 5).

## Access

Query as the `wizscheduler-analytics-reader` role, in the
`wizscheduler-analytics` workgroup. It is a role rather than a policy to
attach because the CI user that applies Terraform may create
`wizscheduler-*` roles but not IAM policies. Its trust policy admits this
account's principals, only with MFA, and only those whose own IAM policy
allows `sts:AssumeRole` on it (an administrator already does).

With a CLI profile in `~/.config/aws` or `~/.aws/config`:

    [profile wiz-analytics]
    role_arn       = arn:aws:iam::<account-id>:role/wizscheduler-analytics-reader
    source_profile = default
    mfa_serial     = arn:aws:iam::<account-id>:mfa/<owner-mfa-device>
    region         = us-east-1

then `AWS_PROFILE=wiz-analytics analytics/apply_views.sh`, or any
`aws athena ...` call with `--profile wiz-analytics` (the CLI prompts for
the MFA code). Without a profile:

    aws sts assume-role \
      --role-arn arn:aws:iam::<account-id>:role/wizscheduler-analytics-reader \
      --role-session-name analytics \
      --serial-number arn:aws:iam::<account-id>:mfa/<owner-mfa-device> \
      --token-code <6-digit code>

and export the returned credentials. In the console, use Switch role with
the account id and role name.

The role grants Athena on the workgroup and the AwsDataCatalog metadata
calls, Glue reads on the database and its tables, Glue create/update/delete
on the seven view tables only (so the views can be replaced, never a lake
table), S3 reads and listing under the lake prefix, and writes to the
results bucket. Results expire after 30 days.

## Go-live

After the first apply with `analytics_enabled = true` and the first nightly
run (or a manual `run_agent_analytics` task):

1. Run `analytics/apply_views.sh` as the reader role once. This doubles as
   a dry run: the views were written against the schemas but never run on
   Athena before, so fix any file that fails and rerun.
2. Query each view for the landed days, e.g.
   `SELECT * FROM daily_resolution_rate WHERE dt = '<yesterday>'`, and
   compare `daily_resolution_rate`, `booking_conversion`, `handoff_reasons`,
   `cost_per_booking` and `spam_blocked_share` with that day's
   `agent_daily_metrics` rows (latency percentiles are approximate, covers
   are by UTC hour, so those two differ by design).
