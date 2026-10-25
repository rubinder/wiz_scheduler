# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WizScheduler is a multi-tenant AI-powered employee scheduling web app. Managers configure companies, locations, employees, and shift templates. A LangGraph agentic pipeline (embedded in the FastAPI backend) generates optimized weekly shift schedules per location, respecting availability, roles, skill levels, and interpersonal affinities.

## Development Commands

Only what the manifests and the Dockerfile don't make obvious:

- **Run the backend from the repo root.** Backend code imports through the
  `backend.` package, so it's `uvicorn backend.main:app --reload` and
  `python -m backend.seed`, not `uvicorn main:app` from inside `backend/`.
  Migrations are the exception: `cd backend && alembic upgrade head`.
- **`backend/.venv` is not committed.** In a fresh clone or worktree:
  `uv venv --python 3.11 backend/.venv && uv pip install -r backend/requirements.txt --python backend/.venv/bin/python`.
- **`pytest` needs Docker running.** `tests/conftest.py` starts Postgres via
  testcontainers for every test; with Docker down the run dies at startup
  with a `DockerException`. The full suite takes about 10 minutes.
- **Frontend tests: `npm test`, not bare `vitest`.** The script pins
  `TZ=Asia/Tokyo`, and the timezone tests fail without it.

## Architecture

### Multi-Tenancy

Every query filters by `company_id` extracted from the authenticated user's JWT. No external auth provider — `get_current_user` decodes the JWT, loads the `User`, and routers receive `current_user.company_id`.

### API Structure

All endpoints prefixed `/api/v1`. Routers in `backend/routers/` — one per domain (auth, company, regions, locations, roles, employees, shift_templates, schedules). Manager routes use `require_manager` dependency; employee routes verify `employee.user_id == current_user.id`.

### LangGraph Scheduling Pipeline (`backend/scheduling/`)

Processes locations serially in a single graph run:

1. **load_location_context** — fetch shift template + eligible employees for current location
2. **build_prompt** — parameterized prompt (zero hardcoded role names anywhere)
3. **call_llm** — Anthropic Claude via async SDK
4. **parse_schedule** — extract JSON; on failure set `status="PARSE_ERROR"`, never raise
5. **validate_and_update_availability** — check `availability_draft` for overlaps; retry once on conflict, then mark `status="CONFLICT"`
6. **emit_result** — yield one NDJSON line per location

Key concept: `availability_draft` (in `SchedulingState`) is a deep copy of employee availability, mutated after each location to prevent cross-location double-booking.

`POST /schedules/generate` returns `StreamingResponse(media_type="application/x-ndjson")`. Frontend consumes via `useScheduleStream` hook using the Fetch Streams API.

### Check-In (`backend/services/check_in.py`)

Employees scan a rotating QR to check in against their scheduled shift. The
QR payload is an HMAC over `slug|location|local_date|counter` keyed by
`CHECKIN_QR_SECRET`; the counter is the count of check-ins that location has
recorded that local day, so recording one rotates the code. Single use is
enforced by a unique constraint on `(location_id, local_date, counter)`, not
by application logic. Paid-only via `assert_paid_plan`, retained for
`RETENTION_CHECKINS_DAYS`.

### Database

SQLAlchemy 2.x `Mapped[...]` / `mapped_column(...)` style. Primary keys are
`String(8)` with `default=generate_short_id` (`backend/utils/id_gen.py`: 8 random
lowercase letters and digits), not UUIDs. `DateTime(timezone=True)` for all timestamps. Migrations via Alembic in `backend/alembic/`.

## Knowledge Graph (RAG)

A pre-built knowledge graph of this codebase lives in `graphify-out/`. Use it as a first-pass lookup before reading files directly — it's 27x more token-efficient than scanning the full codebase.

- **`graphify-out/graph.json`** — entities (functions, classes, models, concepts), relationships (calls, imports, references, inferred connections), and community assignments.
- **`graphify-out/GRAPH_REPORT.md`** — audit report with god nodes, surprising connections, community summaries, and suggested questions.
- **`graphify-out/graph.html`** — interactive browser visualization.

**How to use:** When investigating how parts of the codebase connect, query the graph first via `/graphify query "<question>"`. For architecture questions, check the report's community summaries. For tracing dependencies between two concepts, use `/graphify path "ConceptA" "ConceptB"`.

**Key god nodes** (most connected abstractions): `OwnershipGroup`, `BillingCharge`, `SchedulingState`, `ShiftSchedule`, `LocationResult`, and the `AutoReload*` billing errors.

**Keeping it current:** After significant code changes, run `/graphify . --update` to incrementally re-extract only changed files. Code-only changes don't need LLM calls (AST-only rebuild).

### Read `graphify-out/GRAPH_REPORT.md` whenever you:
- Answer any architecture, cross-module, or "how does X work" questions.
- Need to trace a deep function call chain or class inheritance path across directories.
- Need to locate "god nodes" (high-traffic components) or module "communities".
- Plan to run broad `grep`, `find`, or multi-file text search operations.


## Pre-PR refresh hook

`.claude/settings.json` registers a `PreToolUse` hook on `Bash` that fires whenever a command contains `git push` or `gh pr create`. The hook:

1. Runs `code-review-graph update` to refresh the local `.code-review-graph/` index (used by code-review tooling for token-efficient impact analysis).
2. Prints a reminder to run `/graphify . --update` in-session — the `/graphify` skill can't be invoked from a hook (only Claude can run skills).

**Prereq:** `pip install code-review-graph`. If the CLI isn't installed the hook prints a one-line skip notice and continues — pushing is never blocked.

**Personal overrides** live in `.claude/settings.local.json` (gitignored). The committed `.claude/settings.json` is shared across contributors.

## Git Workflow

**All code changes must follow this workflow:**

1. **Create a feature branch** — Never commit directly to `main`
   - Branch naming: `feat/`, `fix/`, `chore/`, `docs/` prefix
   - Example: `feat/manager-billing`, `fix/payment-race-condition`

2. **Push the feature branch** — Push to origin for CI visibility
   - `git push -u origin <branch-name>`

3. **Create a GitHub PR** — Do not merge without review
   - Use `gh pr create --base main --head <branch-name>`
   - Include summary, changes, and test plan in PR description

4. **Wait for CI to pass** — All automated checks must succeed
   - GitHub Actions must pass
   - No merge if CI fails

5. **Merge only after CI passes** — Use the PR to merge
   - Review the CI results before merging
   - Merge via GitHub PR interface, not `git merge` directly

**Exception:** Emergency hotfixes to production may skip PR review with explicit user confirmation, but must still wait for CI to pass before merging to main.

## Conventions

- **Roles are never hardcoded.** No role name string literal may appear outside `seed.py`. Every role reference must come from the `roles` table.
- **Free-plan limits live in `backend/services/plan.py`.** Any new endpoint that
  creates an `Employee` or `Location` must call `assert_can_add` before writing.
  Plan is derived from `ownership_groups`, never stored.
- **AI spend always debits purchased credits.** `INCLUDED_LLM_USD` is `0.00`
  in production and exists only so a demo environment can grant spend. Every
  successful credit charge (pack purchase or auto-reload) goes through
  `services/billing.charge_saved_card`, which is what fires the operator
  alert with the Anthropic-side top-up amount; do not add a second charge
  path. Post-generation billing never raises on a payment outcome — the
  pre-generation gate is the consent point.
- **Any new path that creates a `User` must set `email_verified_at`.** Set it
  when the flow already proves the address (an emailed invite link, a Google
  `email_verified` claim); leave it NULL and mail a token via
  `services/email_verification.send_verification` otherwise. NULL blocks
  `POST /schedules/generate` (403 `email_not_verified`) and nothing else.
- **The weekly abuse report reports, never acts.**
  `backend/scripts/run_abuse_report.py` clusters free ownership groups that
  share signup signals. Nothing downstream may delete or suspend on its
  output — every signal has innocent explanations, and masked IPs are /16
  (an ISP region, not an address).
- **Preference asterisks report, never act.** `preferences.annotate_preference_violations`
  is the only evaluator behind `shifts.preference_violations`; the frontend renders the
  column and never evaluates preferences itself. `shift_schedules.preference_summary`
  is a generation-time observation and is not recomputed on edit. Draft
  re-annotation (`PUT /schedules/{id}/shifts`) restarts cap counts from zero
  for the posted location only, so it can drop a cap asterisk that was only
  over-cap because of another location; the approved-schedule edit path
  re-annotates the whole week instead.
- **An open shift is a `Shift` with `employee_id IS NULL`** (draft JSON:
  `employee_id: null, status: "OPEN"`; legacy drafts may still say
  `"VACANT"` — read them through `scheduling/open_shifts.is_open_entry`).
  Inside the scheduling graph an unfilled slot stays the `VACANT`
  placeholder; `emit_result` is the only place it becomes open. Any new
  reader of shifts must decide explicitly whether it includes open shifts.
  "Available" has one definition, `services/availability_check.is_available`,
  shared by the candidate ranking and the `no_availability` edit warning;
  the frontend never evaluates availability itself.
- **Employees, locations and roles are archived, never deleted** (#126).
  `archived_at` (and `employees.erased_at` for GDPR) marks a row inactive;
  `services/archive.py` owns archiving and restoring, and `active(Model)` is
  the one filter. Only `services/billing.delete_og_and_dependents` hard-deletes
  them. Any new reader must decide explicitly: planning surfaces (scheduler,
  pickers, check-in, employee routes, plan counts) are active-only; history
  (approved schedules, exports, payroll, importers' `external_id` matching)
  includes archived rows. Pay records carry their own employee/location names.
  Archiving a location deletes its future approved shifts but moves its
  drafts to the terminal status `discarded`, never deleting them: the free
  quota and the overage meter count this month's schedule rows.
- **`ownership_groups.signup_*` are observe-only.** Recorded at registration
  by `services/signup_signals.py` to measure serial free-tier signups. Nothing
  reads them to allow or deny; wiring enforcement onto them is a deliberate
  change that needs its own decision, not a drift.
- Use type hints extensively in all Python code.
- All timestamps must carry timezone offsets (derive from `location.timezone` via `zoneinfo.ZoneInfo`).
- **"Today" is always UTC** — `datetime.now(timezone.utc).date()`, never `date.today()`,
  in application code *and* in tests. `date.today()` reads the server's local clock;
  production runs UTC so the two agree there and the mismatch is invisible, while a
  developer west of UTC gets a one-day drift for the last hours of every day.
  `tests/test_utc_today.py` enforces this by AST sweep.
- Parse LLM output defensively — never raise unhandled exceptions inside the graph.
- Do not install new dependencies without explicit instruction.
- Ensure all code changes pass linting and testing before committing.
