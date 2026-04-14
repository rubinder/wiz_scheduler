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

### Frontend Structure

- `src/api/` — typed fetch wrappers (one file per domain), shared `apiFetch` with Bearer token
- `src/pages/manager/` — manager CRUD pages + Schedule page (AI generation + per-location review)
- `src/pages/employee/` — employee availability self-service
- `src/components/shared/DataTable.tsx` — reusable inline-editable table (used on Employees, Shift Templates, Schedule)
- `src/hooks/useAuth.ts` — JWT in localStorage, login/logout state
- `src/hooks/useScheduleStream.ts` — NDJSON stream consumer

### Database

SQLAlchemy 2.x `Mapped[...]` / `mapped_column(...)` style. UUID primary keys with `server_default=text("gen_random_uuid()")`. `DateTime(timezone=True)` for all timestamps. Migrations via Alembic in `backend/alembic/`.

## Knowledge Graph (RAG)

A pre-built knowledge graph of this codebase lives in `graphify-out/`. Use it as a first-pass lookup before reading files directly — it's 27x more token-efficient than scanning the full codebase.

- **`graphify-out/graph.json`** — 1,160 nodes, 1,831 edges across 155 communities. Contains entities (functions, classes, models, concepts), relationships (calls, imports, references, inferred connections), and community assignments.
- **`graphify-out/GRAPH_REPORT.md`** — audit report with god nodes, surprising connections, community summaries, and suggested questions.
- **`graphify-out/graph.html`** — interactive browser visualization.

**How to use:** When investigating how parts of the codebase connect, query the graph first via `/graphify query "<question>"`. For architecture questions, check the report's community summaries. For tracing dependencies between two concepts, use `/graphify path "ConceptA" "ConceptB"`.

**Key god nodes** (most connected abstractions): `SchedulingState` (83 edges), `ShiftAssignment` (42), `LocationResult` (39), `Base` (34), `OwnershipGroup` (22), `CondensedRole` (21).

**Keeping it current:** After significant code changes, run `/graphify . --update` to incrementally re-extract only changed files. Code-only changes don't need LLM calls (AST-only rebuild).

## Conventions

- **Roles are never hardcoded.** No role name string literal may appear outside `seed.py`. Every role reference must come from the `roles` table.
- Use type hints extensively in all Python code.
- Prefer functional components and hooks in React.
- All timestamps must carry timezone offsets (derive from `location.timezone` via `zoneinfo.ZoneInfo`).
- Parse LLM output defensively — never raise unhandled exceptions inside the graph.
- Do not install new dependencies without explicit instruction.
- Ensure all code changes pass linting and testing before committing.
