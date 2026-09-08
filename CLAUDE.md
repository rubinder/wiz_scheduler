# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WizScheduler is a multi-tenant AI-powered employee scheduling web app. Managers configure companies, locations, employees, and shift templates. A LangGraph agentic pipeline (embedded in the FastAPI backend) generates optimized weekly shift schedules per location, respecting availability, roles, skill levels, and interpersonal affinities.

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x (Async), PostgreSQL, Alembic
- **AI Scheduling**: LangGraph + Anthropic Claude (lives inside `backend/scheduling/`)
- **Auth**: JWT via python-jose + passlib[bcrypt]
- **Frontend**: React 18+, TypeScript, Vite, Tailwind CSS
- **Packaging**: `uv` (Python), `npm` (frontend)
- **Testing**: pytest + pytest-asyncio + httpx

## Development Commands

```bash
# Backend
cd backend && uv pip install -r requirements.txt
uvicorn main:app --reload                 # dev server on :8000
alembic upgrade head                      # apply migrations
python seed.py                            # insert demo data (idempotent)

# Frontend
cd frontend && npm install
npm run dev                               # dev server on :5173
npm run build                             # production build to dist/

# Tests (from repo root)
pytest tests/
pytest tests/test_auth.py                 # single test file
pytest tests/test_auth.py::test_login -v  # single test

# Docker
docker-compose up --build
```

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

### Frontend Structure

- `src/api/` — typed fetch wrappers (one file per domain), shared `apiFetch` with Bearer token
- `src/pages/manager/` — manager CRUD pages + Schedule page (AI generation + per-location review)
- `src/pages/employee/` — employee availability self-service
- `src/components/shared/DataTable.tsx` — reusable inline-editable table (used on Roles, Locations, Regions). Supports optional `createDisabled` / `createDisabledReason` props to render the "+ Add" control disabled with an explanatory message (e.g. free-plan limit reached) instead of omitting it.
- `src/hooks/useAuth.ts` — JWT in localStorage, login/logout state
- `src/hooks/useScheduleStream.ts` — NDJSON stream consumer

### Database

SQLAlchemy 2.x `Mapped[...]` / `mapped_column(...)` style. UUID primary keys with `server_default=text("gen_random_uuid()")`. `DateTime(timezone=True)` for all timestamps. Migrations via Alembic in `backend/alembic/`.

## Knowledge Graph (RAG)

A pre-built knowledge graph of this codebase lives in `graphify-out/`. Use it as a first-pass lookup before reading files directly — it's 27x more token-efficient than scanning the full codebase.

- **`graphify-out/graph.json`** — 3,535 nodes, 5,789 edges across 330 communities. Contains entities (functions, classes, models, concepts), relationships (calls, imports, references, inferred connections), and community assignments.
- **`graphify-out/GRAPH_REPORT.md`** — audit report with god nodes, surprising connections, community summaries, and suggested questions.
- **`graphify-out/graph.html`** — interactive browser visualization.

**How to use:** When investigating how parts of the codebase connect, query the graph first via `/graphify query "<question>"`. For architecture questions, check the report's community summaries. For tracing dependencies between two concepts, use `/graphify path "ConceptA" "ConceptB"`.

**Key god nodes** (most connected abstractions): `OwnershipGroup`, `BillingCharge`, `SchedulingState`, `ShiftSchedule`, `LocationResult`, and the `AutoReload*` billing errors.

**Keeping it current:** After significant code changes, run `/graphify . --update` to incrementally re-extract only changed files. Code-only changes don't need LLM calls (AST-only rebuild).

## Pre-PR refresh hook

`.claude/settings.json` registers a `PreToolUse` hook on `Bash` that fires whenever a command contains `git push` or `gh pr create`. The hook:

1. Runs `code-review-graph update` to refresh the local `.code-review-graph/` index (used by code-review tooling for token-efficient impact analysis).
2. Prints a reminder to run `/graphify . --update` in-session — the `/graphify` skill can't be invoked from a hook (only Claude can run skills).

**Prereq:** `pip install code-review-graph`. If the CLI isn't installed the hook prints a one-line skip notice and continues — pushing is never blocked.

**Personal overrides** live in `.claude/settings.local.json` (gitignored). The committed `.claude/settings.json` is shared across contributors.

## Conventions

- **Roles are never hardcoded.** No role name string literal may appear outside `seed.py`. Every role reference must come from the `roles` table.
- **Free-plan limits live in `backend/services/plan.py`.** Any new endpoint that
  creates an `Employee` or `Location` must call `assert_can_add` before writing.
  Plan is derived from `ownership_groups`, never stored.
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
- **`ownership_groups.signup_*` are observe-only.** Recorded at registration
  by `services/signup_signals.py` to measure serial free-tier signups. Nothing
  reads them to allow or deny; wiring enforcement onto them is a deliberate
  change that needs its own decision, not a drift.
- Use type hints extensively in all Python code.
- Prefer functional components and hooks in React.
- **Use logical, not physical, direction utilities in Tailwind** — `text-start`
  not `text-left`, `ms-2` not `ml-2`, `border-s` not `border-l`, `start-0` not
  `left-0`. `ar` and `ur` are RTL and `LanguageContext` sets `document.dir`, so
  a physical utility looks right in 17 locales and wrong in 2.
  `frontend/src/utils/logicalDirection.test.ts` enforces this.
- All timestamps must carry timezone offsets (derive from `location.timezone` via `zoneinfo.ZoneInfo`).
- **"Today" is always UTC** — `datetime.now(timezone.utc).date()`, never `date.today()`,
  in application code *and* in tests. `date.today()` reads the server's local clock;
  production runs UTC so the two agree there and the mismatch is invisible, while a
  developer west of UTC gets a one-day drift for the last hours of every day.
  `tests/test_utc_today.py` enforces this by AST sweep.
- Parse LLM output defensively — never raise unhandled exceptions inside the graph.
- Do not install new dependencies without explicit instruction.
- Ensure all code changes pass linting and testing before committing.
