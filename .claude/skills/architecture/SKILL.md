---
name: architecture
description: WizScheduler's bounded contexts and named design patterns. Read before writing or reviewing any app change; review findings must name the pattern they cite. Backed by tests/test_architecture.py and frontend/src/architecture.test.ts.
---

# Architecture

The DDD vocabulary for WizScheduler: eight bounded contexts, eight named
patterns, and the mechanical tests that hold part of them. The pattern ids
below are the only vocabulary architecture findings may use.

## 1. How to use this skill

**Implementers.** Find the context your change lives in (section 2), read
its reference file, and keep the change inside that context's modules. When
you need another context, call one of its listed entry points; do not reach
into its private helpers or its tables. Before you finish, walk the eight
patterns (section 3) and check each one that applies.

**Reviewers.** Every architecture finding cites exactly one pattern id, then
the place, then what is wrong. These two are hypothetical; the paths are
placeholders, not real files:

```
[single-evaluator] frontend/src/pages/manager/<Page>.tsx:<line> evaluates availability; ask the server (POST /schedules/candidates)
[archive-never-delete] backend/routers/<router>.py:<line> deletes a Location row; call services/archive.archive_location
```

A finding that cannot name a pattern is not an architecture finding. Send it
to the correctness or conventions lens instead. If a mechanical test already
fails on the line, cite its rule id as well (`[archive-never-delete]
archived-at-outside-archive ...`).

**Existing code is not precedent.** Several modules predate these patterns
(fat routers, a Stripe call outside billing, see the reference files). New
code follows the pattern even when its neighbour does not. Flag a violation
in a changed line. Do not flag one the diff did not touch.

## 2. Contexts

Paths are relative to `backend/` unless they start with `frontend/`.

| Context | Owns (modules) | Public entry points | Reference |
|---|---|---|---|
| Identity & Tenancy | `tenant.py`, `database.py` (tenant filter, RLS, `cross_tenant_session`), `dependencies.py`, `services/location_scope.py`, `services/manager_permissions.py`, `services/ownership.py`, `services/email_verification.py`, `services/passwords.py`; routers `auth`, `invites`, `manager_invites`, `manager_locations`, `company` | `dependencies.get_current_user`, `require_manager`, `require_admin`, `get_location_scope`; `location_scope.location_scope_for`; `ownership.assert_owned`; `email_verification.assert_email_verified`, `send_verification` | [identity-tenancy](references/identity-tenancy.md) |
| Workforce | `services/archive.py`, `services/availability_check.py`, `services/preference_loader.py`, `services/preference_submissions.py`; routers `employees`, `roles`, `condensed_roles`, `regions`, `locations`, `affinities`, `scheduling_preferences`, `preference_submissions` | `archive.active`, `archive_employee`, `archive_location`, `archive_role`, `restore_*`, `erase_employee`; `availability_check.is_available` | [workforce](references/workforce.md) |
| Scheduling | `scheduling/` (LangGraph graph and local scheduler), `services/schedule_lock.py`, `services/candidates.py`, `services/edit_warnings.py`, `services/signal_weights.py`, `services/special_hours.py`; routers `schedules`, `open_shifts`, `shift_templates`, `special_hours`, `export_schedules` | `graph.run_scheduling_pipeline`, `local_scheduler.local_schedule`, `schedule_lock.acquire`/`release`, `open_shifts.is_open_entry`, `preferences.annotate_preference_violations`, `candidates.rank_candidates` | [scheduling](references/scheduling.md) |
| Billing & Plans | `services/billing.py`, `services/plan.py`, `services/location_quota.py`, `services/operator_alerts.py`, `services/signup_signals.py`, `services/abuse_report.py`; models `ownership_group`, `billing_charge`, `token_usage*`, `storage_snapshot`; routers `billing`, `webhooks`, `ownership_group` | `plan.get_plan_state`, `assert_can_add`, `assert_paid_plan`, `check_can_generate`, `assert_roster_editable`; `billing.check_ai_credits`, `charge_saved_card`, `delete_og_and_dependents` | [billing-plans](references/billing-plans.md) |
| Payroll | `services/time_entries.py`, `services/payroll_export.py`, `services/payroll_archive.py`, `services/archive_storage.py`, `services/data_retention.py`; routers `payroll`, `payroll_archive` | `time_entries.derive_time_entries`, `approve_entries`; `payroll_export.export_approved`, `export_history`; `data_retention.payroll_retention_info`, `run_data_retention` | [payroll](references/payroll.md) |
| Check-In | `services/check_in.py`, `services/check_in_token.py`, model `employee_check_in`; router `check_ins` | `check_in.issue_token`, `record_check_in`, `local_date_for` | [check-in](references/check-in.md) |
| Legal/GDPR | `legal/` (policy text), router `gdpr`, models `consent`, `gdpr_export_log` | `GET /gdpr/export`, `DELETE /gdpr/delete-account`, consent routes; erasure goes through `archive.erase_employee` | [legal-gdpr](references/legal-gdpr.md) |
| Imports | routers `import_7shifts`, `import_deputy`; `services/integration_import_quota.py`, model `integration_import` | `POST /import/7shifts`, `/import/deputy` and their `/availabilities` routes; `integration_import_quota.begin_integration_import` | [imports](references/imports.md) |

Not a context: `config.py`, `middleware/`, `utils/`, the platform
services (`rate_limit`, `monitoring`, `failure_logger`, `feedback_quota`,
`email_quota`, `activation*`), and the routers `failure_logs` (admin view
of the failure log) and `feedback` (in-app feedback form). Any context may
use them, and they hold no domain rules of their own.

`routers/public.py` is different. `compliance_check`, the unauthenticated
free compliance checker for the marketing site, reuses Scheduling's rest
rules from `scheduling/rest_rules.py`. It also owns its own logic: pairing
shifts per employee for the clopening scan, and the short-notice rule. That
logic belongs to Scheduling. Review a change there under `single-evaluator`
(no second copy of a Scheduling rule) and `thin-router` (rules move toward
Scheduling, not into the router).

## 3. Patterns

### `aggregate-root`

`OwnershipGroup` is the aggregate root for money and lifecycle. It holds the
Stripe customer and subscription, the AI credit balance, auto-reload
settings, and the cancellation and deletion timestamps. One or more
companies hang off it (`companies.ownership_group_id`). The plan is derived
from it on every read and never stored. Its money fields change only
through `services/billing.py`, and its deletion only through
`delete_og_and_dependents`.

- **Use when** a change touches plan, credits, charges, subscription state,
  or deleting a customer's data. Resolve the group with
  `billing.get_ownership_group_id` and go through Billing & Plans.
- **Example:** `backend/models/ownership_group.py:OwnershipGroup`, read by
  `backend/services/plan.py:get_plan_state`.
- **Anti-pattern it prevents:** a `plan` column, or a per-company credit
  balance, that drifts from the group's real subscription. Also a router
  that writes `og.ai_credits_usd` itself.
- **Mechanical check:** `stripe-outside-billing` (Stripe money calls
  `PaymentIntent.create/confirm`, `Refund.create`, `Charge.create` and
  `Invoice.pay` only in `backend/services/billing*.py`). The rest is
  reviewer only.

### `single-evaluator`

Each domain question has exactly one function that answers it. Every caller,
including the frontend, asks that function, often through the API. A second
copy, even a "faithful" one, is a defect, and so is a second source of a
domain identity, e.g. a role name literal instead of a row from the `roles`
table.

| Question | The evaluator |
|---|---|
| Can this employee work this span? | `services/availability_check.is_available` |
| Which preferences does this shift violate? | `scheduling/preferences.annotate_preference_violations` |
| Is this slot eligible (role, location, hard prefs)? | `scheduling/prompts.eligible_for_slot` |
| May this tenant add a location or employee? | `services/plan.assert_can_add` |
| May this tenant generate? | `services/plan.check_can_generate` |
| Is this draft entry an open shift? | `scheduling/open_shifts.is_open_entry` |
| Is this row active? | `services/archive.active` |

- **Use when** you need any of these answers, or you are about to write a
  new domain rule. Find the evaluator first. If none exists, write one in
  the owning context and make both paths call it.
- **Example:** `backend/services/availability_check.py:is_available`, the
  one definition, shared by `backend/services/candidates.py:rank_candidates`
  and `backend/services/edit_warnings.py:collect_edit_warnings`.
- **Anti-pattern it prevents:** the frontend greying out "unavailable"
  employees by its own rule, or a second limit check that disagrees with
  `plan.py` about who is over the free tier.
- **Mechanical check:** `frontend-availability-eval` (no `isAvailable(`,
  `is_available(` or `checkAvailability(` call in `frontend/src`). Other
  evaluators are reviewer only. `tests/test_eligibility_shared.py` pins
  `eligible_for_slot` structurally.

### `anti-corruption-layer`

External models (7shifts, Deputy, Stripe) are translated into domain terms
at the edge. Their field names, shapes and quirks never reach a service,
model or the frontend. Inside the app there are only `Employee`,
`Location`, availability as location wall-clock, and `BillingCharge`.

- **Use when** reading anything from a third-party API or webhook.
- **Example:** `backend/routers/import_deputy.py:_deputy_location_timezone`
  turns Deputy's four possible timezone fields into one IANA zone.
  `backend/services/payroll_archive.py:stripe_field` reads a Stripe object
  whether it arrives as a dict or an SDK object.
- **Anti-pattern it prevents:** a service that branches on
  `deputy_location["_DPMetaData"]`, or a model column named after a vendor
  field. A model that changes when the vendor's API does.
- **Mechanical check:** reviewer only. `stripe-outside-billing` covers the
  money calls at the Stripe edge.

### `thin-router`

A router parses the request, authorizes it (dependencies, `LocationScope`,
`assert_owned`), calls a service, and maps the service's domain exception to
an HTTP status. Rules, queries with business meaning, and multi-step writes
live in services. Routers never import other routers. Models never import
services or routers. The frontend has the same layering: pages and hooks
call `frontend/src/api/`, never the network.

- **Use when** adding or changing any endpoint, or any frontend data load.
- **Example:** `backend/routers/check_ins.py:create_check_in` authorizes,
  then calls `backend/services/check_in.py:record_check_in` and maps
  `CheckInRejected` to 409. In the frontend, `frontend/src/api/client.ts:apiFetch`.
- **Anti-pattern it prevents:** business rules duplicated across two
  routers. A rule you can only test through HTTP. A `fetch(` in a page that
  skips the shared auth and error handling.
- **Mechanical check:** `router-imports-router`, `model-imports-service`,
  `fetch-outside-api`. Router size and where the rules sit are reviewer
  only. `schedules.py`, `import_7shifts.py` and `gdpr.py` are fat today. Do
  not grow them: put the new rule in a service.

### `archive-never-delete`

Employees, locations and roles are archived (`archived_at`), and an
employee is erased for GDPR (`erased_at`). They are never deleted.
`services/archive.py` alone sets and clears `archived_at`, and `active(Model)`
is the one filter. Planning surfaces (scheduler, pickers, check-in, employee
routes, plan counts) read active rows only. History (approved schedules,
exports, payroll, importers' `external_id` matching) includes archived rows.
Only `services/billing.delete_og_and_dependents` hard-deletes these rows.

- **Use when** removing, hiding or restoring any of the three, or adding a
  new reader of them. A new reader must decide active-only or with-history
  on purpose.
- **Example:** `backend/services/archive.py:archive_location`. It deletes the
  location's future approved shifts, moves its drafts to `discarded`, and
  never deletes a schedule row, because the free quota and the overage meter
  count them.
- **Anti-pattern it prevents:** a deleted employee breaking past shifts and
  pay records. An archive-then-recreate loop that resets the free quota.
- **Mechanical check:** `archived-at-outside-archive`. Hard deletes and
  reader choices are reviewer only.

### `location-local-time`

Availability, blackouts, preferences and shift times are the location's
local wall clock. They are never converted per employee (#171), and
employees have no timezone. Stored availability is wall-clock tagged UTC
(#61): read the face, never `.astimezone()` it. Conversions use
`ZoneInfo(location.timezone)`. In the frontend a date-only value stays a
`YYYY-MM-DD` string.

- **Use when** reading or writing any time a person entered or will see,
  deriving a "local day", or importing times.
- **Example:** `backend/services/check_in.py:local_date_for` gives the
  calendar date at the location. `frontend/src/utils/availabilityDates.ts:dateParts`
  splits a date string without a timezone conversion.
- **Anti-pattern it prevents:** availability shifted by the server's or the
  browser's offset, so the scheduler produces zero shifts. `new
  Date("2026-11-04")` reading back as the previous day west of UTC.
- **Mechanical check:** `date-only-string-parse`. Backend conversions are
  reviewer only. `tests/test_avail_local_wallclock.py` and
  `tests/test_availability_location_local_integration.py` pin the contract.

### `fail-closed`

When money, deletion or legal records are at stake and the check cannot
reach a confident yes, the answer is no: refuse, raise, or do nothing. Gates
raise a typed HTTP error before the side effect. Unknown states block.
Missing configuration blocks. Signals that only suggest abuse never delete or
suspend anything. One deliberate exception keeps its shape: post-generation
billing never raises on a payment outcome, because the pre-generation gate is
the consent point.

- **Use when** writing any gate in front of a charge, a refund, a delete, an
  erasure, a paid feature, or a legal record.
- **Example:** `backend/services/payroll_archive.py:verify_archive_session`
  rejects a Checkout session unless kind, owner, customer and paid status all
  match. `backend/services/plan.py:check_can_generate` refuses with 402
  before any LLM spend.
- **Anti-pattern it prevents:** `except Exception: pass` around a charge.
  Defaulting to "allowed" when a lookup fails. A cleanup job that deletes
  on a heuristic.
- **Mechanical check:** reviewer only.

### `utc-today`

"Today" is `datetime.now(timezone.utc).date()`, never `date.today()`, in
application code and tests. A location's "today" is a different question.
Answer it with `location-local-time`.

- **Use when** computing today, this month, a cutoff, or a default `now`.
- **Example:** `backend/services/billing.py:utc_month_start`.
- **Anti-pattern it prevents:** a one-day drift on a developer machine west
  of UTC that production, running UTC, never shows.
- **Mechanical check:** `tests/test_utc_today.py` (AST sweep of `backend/`
  and `tests/`). It is not part of `test_architecture.py`.

## 4. Ratchet

The mechanical rules run in the normal suites: `pytest
tests/test_architecture.py` and `npm test` (`frontend/src/architecture.test.ts`).
Violations that already exist sit on allowlists:

- `tests/architecture_allowlist.json` has one entry: `stripe-outside-billing`
  in `backend/services/payroll_archive.py` (`Refund.create`).
- `frontend/src/architecture_allowlist.json` has three `fetch-outside-api`
  entries: `components/FeedbackForm.tsx`, `hooks/useScheduleStream.ts`,
  `pages/manager/ExportSchedules.tsx`.

Each entry is `{rule, file, detail, issue}`. `issue` is the cleanup issue
number, or `null` until one is filed. The factory files a cleanup proposal
for each. The tests fail both ways:

- A violation that is not on the list fails `test_no_new_layering_violations`
  or "has no new violations". **New code may not add entries.** The
  architecture gate always reports a new allowlist entry as a blocking
  finding, and only the owner clears it, by merging the PR. The factory's
  architecture gate also fails mechanically when either allowlist grows
  relative to `origin/main`.
- A listed violation that no longer exists fails
  `test_allowlist_only_shrinks` or "allowlist only shrinks". **Fixing a
  violation means deleting its entry in the same PR.**

The list only shrinks. Moving a violation to another file is a new
violation, not a fix.

## 5. CLAUDE.md conventions mapped to patterns

| CLAUDE.md convention | Pattern |
|---|---|
| Roles are never hardcoded; every role comes from the `roles` table | `single-evaluator` (the `roles` table is the one source of role identity; reviewer only) |
| New Employee/Location creators call `plan.assert_can_add`; the plan is derived from `ownership_groups` | `single-evaluator`, `aggregate-root` |
| Every credit charge goes through `billing.charge_saved_card`; no second charge path | `aggregate-root` (`stripe-outside-billing`) |
| Post-generation billing never raises; the pre-generation gate is the consent point | `fail-closed` (the documented exception) |
| A new path that creates a `User` sets `email_verified_at` or mails a token; NULL blocks generation | `fail-closed` |
| The weekly abuse report reports and never acts | `fail-closed` (deletion needs certainty, not a signal) |
| Preference asterisks: `annotate_preference_violations` is the only evaluator; the frontend renders only | `single-evaluator` |
| An open shift is `employee_id IS NULL`; legacy `VACANT` is read through `is_open_entry`; "available" is `is_available` | `single-evaluator` (`frontend-availability-eval`) |
| Employees, locations and roles are archived, never deleted; `active(Model)` is the one filter | `archive-never-delete` (`archived-at-outside-archive`) |
| `ownership_groups.signup_*` are observe-only | `fail-closed` (like the abuse report, a signal never allows, denies, suspends or deletes) |
| Availability is always the location's local time; timestamps carry the location's offset | `location-local-time` (`date-only-string-parse`) |
| "Today" is always UTC | `utc-today` (`tests/test_utc_today.py`) |

Not mapped: "parse LLM output defensively, never raise inside the graph" is a
Scheduling invariant (`references/scheduling.md`), not a cross-context
pattern.
