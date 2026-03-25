# WizScheduler — Claude Code Task Specification

> **Read `CLAUDE.md` before starting.** All commands, conventions, and directory layout below are derived from it.

---

## Project Overview

A multi-tenant AI-powered employee scheduling web application. Managers configure their company, locations, employees, and shift templates. The app uses a **LangGraph** agentic pipeline — embedded in the FastAPI backend — to generate optimized weekly shift schedules one location at a time, respecting employee availability, roles, skill levels, and interpersonal affinities.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy (Async) |
| Database | PostgreSQL |
| Migrations | Alembic |
| Python packaging | `uv` |
| Auth | JWT (python-jose + passlib) — issued by FastAPI, returned as Bearer token |
| AI Scheduling | LangGraph + Anthropic Claude (lives inside `backend/`) |
| Frontend | React 18+, TypeScript, Vite, Tailwind CSS |
| Testing | pytest (backend) |
| Containerization | Docker + docker-compose |

---

## Directory Structure

Follow this layout exactly. Do not create top-level directories other than those listed here.

```
wiz_scheduler/
├── backend/
│   ├── main.py                        ← FastAPI app factory & router registration
│   ├── config.py                      ← Settings loaded from .env via pydantic-settings
│   ├── database.py                    ← Async SQLAlchemy engine + session factory
│   ├── dependencies.py                ← get_db, get_current_user, require_manager
│   ├── models/                        ← SQLAlchemy ORM models (one file per domain)
│   │   ├── __init__.py
│   │   ├── company.py
│   │   ├── user.py
│   │   ├── region.py
│   │   ├── location.py
│   │   ├── role.py
│   │   ├── employee.py
│   │   ├── shift_template.py
│   │   └── schedule.py
│   ├── schemas/                       ← Pydantic v2 request/response schemas
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── company.py
│   │   ├── region.py
│   │   ├── location.py
│   │   ├── role.py
│   │   ├── employee.py
│   │   ├── shift_template.py
│   │   └── schedule.py
│   ├── routers/                       ← One APIRouter per domain
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── company.py
│   │   ├── regions.py
│   │   ├── locations.py
│   │   ├── roles.py
│   │   ├── employees.py
│   │   ├── shift_templates.py
│   │   └── schedules.py               ← triggers LangGraph; streams NDJSON
│   ├── scheduling/                    ← LangGraph scheduling pipeline
│   │   ├── __init__.py
│   │   ├── graph.py                   ← LangGraph graph definition
│   │   ├── nodes.py                   ← Individual node functions
│   │   ├── state.py                   ← SchedulingState TypedDict
│   │   └── prompts.py                 ← Parameterized prompt builder
│   ├── seed.py                        ← Inserts demo company, roles, employees, availability
│   ├── requirements.txt
│   ├── alembic.ini
│   └── alembic/
│       └── versions/
│           └── 0001_initial_schema.py
├── frontend/
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/                       ← Typed fetch wrappers (one file per domain)
│       │   ├── auth.ts
│       │   ├── company.ts
│       │   ├── regions.ts
│       │   ├── locations.ts
│       │   ├── roles.ts
│       │   ├── employees.ts
│       │   ├── shiftTemplates.ts
│       │   └── schedules.ts
│       ├── components/
│       │   ├── layout/
│       │   │   ├── Sidebar.tsx
│       │   │   └── TopBar.tsx
│       │   └── shared/
│       │       ├── DataTable.tsx      ← Reusable inline-editable table
│       │       ├── WeekPicker.tsx
│       │       └── StatusBadge.tsx
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Register.tsx
│       │   ├── manager/
│       │   │   ├── Dashboard.tsx
│       │   │   ├── Company.tsx
│       │   │   ├── Regions.tsx
│       │   │   ├── Locations.tsx
│       │   │   ├── Roles.tsx
│       │   │   ├── Employees.tsx
│       │   │   ├── ShiftTemplates.tsx
│       │   │   └── Schedule.tsx       ← AI generation + per-location review UI
│       │   └── employee/
│       │       └── Availability.tsx
│       ├── hooks/
│       │   ├── useAuth.ts
│       │   └── useScheduleStream.ts   ← Consumes NDJSON stream from /schedules/generate
│       └── types/
│           └── index.ts               ← TypeScript interfaces mirroring Pydantic schemas
├── tests/
│   ├── conftest.py                    ← pytest fixtures: async DB session, test client, seeded company
│   ├── test_auth.py
│   ├── test_employees.py
│   ├── test_shift_templates.py
│   └── test_schedule_pipeline.py      ← Tests LangGraph graph with mocked LLM
├── docker-compose.yml                 ← Services: postgres, backend, frontend (dev)
├── Dockerfile                         ← Multi-stage: frontend build + backend runtime
├── .env.example
└── README.md
```

---

## Development Commands

These commands must work exactly as written and match `CLAUDE.md`.

```bash
# ── Backend ──────────────────────────────────────────────
cd backend
uv pip install -r requirements.txt
uvicorn main:app --reload                 # dev server on :8000
alembic upgrade head                      # apply migrations
python seed.py                            # insert demo data

# ── Tests ────────────────────────────────────────────────
# From repo root:
pytest tests/

# ── Frontend ─────────────────────────────────────────────
cd frontend
npm install
npm run dev                               # dev server on :5173
npm run build                             # production build to dist/

# ── Docker ───────────────────────────────────────────────
docker-compose up --build
```

---

## Database Schema

Multi-tenancy is enforced at the **application layer**: every SQLAlchemy query filters by `company_id`, which is extracted from the authenticated user's JWT. There is no external auth provider — the `get_current_user` dependency decodes the JWT, loads the `User`, and all routers receive `current_user.company_id` as a parameter.

Use SQLAlchemy 2.x `Mapped[...]` / `mapped_column(...)` style throughout. UUID primary keys with `server_default=text("gen_random_uuid()")`. `DateTime(timezone=True)` for all timestamps.

### `companies`
```
id            UUID PK
name          String NOT NULL
slug          String UNIQUE NOT NULL      -- e.g. "abc123"
created_at    DateTime(tz) server_default now()
```

### `users`
```
id             UUID PK
company_id     UUID FK → companies
email          String UNIQUE NOT NULL
hashed_password String NOT NULL           -- bcrypt via passlib
full_name      String
user_role      String CHECK IN ('manager', 'employee')
```

### `regions`
```
id          UUID PK
company_id  UUID FK → companies NOT NULL  -- index this column
name        String NOT NULL
geo_bounds  JSON                           -- polygon or bounding box
```

### `locations`
```
id          UUID PK
company_id  UUID FK → companies NOT NULL
region_id   UUID FK → regions NOT NULL
name        String NOT NULL
address     String
geo_coord   JSON                           -- { "lat": ..., "lng": ... }
timezone    String NOT NULL                -- e.g. "America/New_York"
```

### `roles`
```
id          UUID PK
company_id  UUID FK → companies NOT NULL
name        String NOT NULL               -- USER-DEFINED. Never hardcoded anywhere.
description String
```

### `employees`
```
id           UUID PK
company_id   UUID FK → companies NOT NULL
user_id      UUID FK → users NULLABLE     -- set when employee has a login
full_name    String NOT NULL
email        String
location_ids ARRAY(UUID)                  -- locations this employee can work at
```

### `employee_roles`
```
id           UUID PK
company_id   UUID FK
employee_id  UUID FK → employees NOT NULL
role_id      UUID FK → roles NOT NULL
skill_level  Integer CHECK 1–5
```

### `employee_affinities`
```
id                 UUID PK
company_id         UUID FK
employee_id        UUID FK → employees NOT NULL
target_employee_id UUID FK → employees NOT NULL
level              Numeric CHECK −1.0 to 1.0
-- level =  1: must be scheduled together (hard)
-- level = -1: must NOT be scheduled together (hard)
-- Manager-only write
```

### `employee_availability`
```
id           UUID PK
company_id   UUID FK
employee_id  UUID FK → employees NOT NULL
year         Integer NOT NULL
month        Integer NOT NULL
day          Integer NOT NULL
start_time   DateTime(tz) NOT NULL
end_time     DateTime(tz) NOT NULL
-- Employees write their own rows. Managers write any row in their company.
```

### `shift_templates`
```
id              UUID PK
company_id      UUID FK
location_id     UUID FK → locations NOT NULL
name            String NOT NULL
weekly_schedule JSON NOT NULL              -- see shape below
```

**`weekly_schedule` JSON shape** — role names sourced from `roles` table, never hardcoded:
```json
[
  {
    "day_of_week": 1,
    "roles": [
      { "role_id": "<uuid>", "role_name": "Floor Associate", "required_headcount": 3, "start_time": "09:00", "end_time": "17:00" },
      { "role_id": "<uuid>", "role_name": "Team Lead",       "required_headcount": 1, "start_time": "09:00", "end_time": "17:00" }
    ]
  }
]
```

### `shift_schedules`
```
id              UUID PK
company_id      UUID FK
location_id     UUID FK → locations NOT NULL
week_start_date Date NOT NULL
status          String CHECK IN ('draft', 'approved', 'rejected')
raw_llm_output  Text                       -- stored for debugging
created_at      DateTime(tz)
```

### `shifts`
```
id                UUID PK
company_id        UUID FK
shift_schedule_id UUID FK → shift_schedules NOT NULL
location_id       UUID FK → locations NOT NULL
employee_id       UUID FK → employees NOT NULL
role_id           UUID FK → roles NOT NULL
role_name         String NOT NULL          -- denormalized for display
date              Date NOT NULL
start_time        DateTime(tz) NOT NULL
end_time          DateTime(tz) NOT NULL
```

---

## Authentication

Implement inside `backend/routers/auth.py` using `python-jose` and `passlib[bcrypt]`.

| Endpoint | Description |
|---|---|
| `POST /api/v1/auth/register` | Creates `User` + `Company` (manager self-reg); sends welcome email |
| `POST /api/v1/auth/login` | Returns `{ access_token, token_type }` |
| `GET /api/v1/auth/me` | Returns current user profile |

JWT payload: `{ sub: user_id, company_id, user_role, exp }`

`dependencies.py` must expose:
- `get_db` → yields async SQLAlchemy session
- `get_current_user` → validates JWT, returns `User` ORM object
- `require_manager` → calls `get_current_user`, raises HTTP 403 if `user_role != 'manager'`

All manager CRUD endpoints depend on `require_manager`. Employee availability write endpoints depend on `get_current_user` and verify `employee.user_id == current_user.id` before allowing the write.

---

## REST API Endpoints

All endpoints prefixed `/api/v1`. Use Pydantic v2 schemas for request bodies and response models. Use type hints on every function signature (per `CLAUDE.md`).

| Router | Prefix | Endpoints |
|---|---|---|
| `auth.py` | `/auth` | POST `/register`, POST `/login`, GET `/me` |
| `company.py` | `/company` | GET `/`, PUT `/` |
| `regions.py` | `/regions` | GET `/`, POST `/`, PUT `/{id}`, DELETE `/{id}` |
| `locations.py` | `/locations` | GET `/`, POST `/`, PUT `/{id}`, DELETE `/{id}` |
| `roles.py` | `/roles` | GET `/`, POST `/`, PUT `/{id}`, DELETE `/{id}` |
| `employees.py` | `/employees` | GET `/`, POST `/`, PUT `/{id}`, DELETE `/{id}`, POST `/bulk-upload` |
| `shift_templates.py` | `/shift-templates` | GET `/`, POST `/`, PUT `/{id}`, DELETE `/{id}` |
| `schedules.py` | `/schedules` | POST `/generate` (streaming NDJSON), POST `/{id}/approve`, POST `/{id}/reject`, GET `/week/{week_start_date}` |

### Bulk Employee Upload (`POST /employees/bulk-upload`)
Accepts a multipart CSV with columns: `full_name, email, role_names (pipe-separated), skill_levels (pipe-separated), location_names (pipe-separated)`. Role names are matched case-insensitively against the `roles` table for the authenticated company. Returns a summary of created/skipped rows.

---

## LangGraph Scheduling Pipeline (`backend/scheduling/`)

The pipeline runs inside the same FastAPI process. `routers/schedules.py` calls it and returns a `StreamingResponse(media_type="application/x-ndjson")` — one JSON line per completed location.

### Graph Topology

```
[START]
   │
   ▼
load_location_context
   │  Fetch current location's shift_template and all eligible employees
   │  (those whose location_ids includes this location_id).
   │  Filter each employee's availability against availability_draft
   │  to surface only unconsumed windows.
   ▼
build_prompt
   │  Call prompts.build_schedule_prompt().
   │  All role names and headcounts come from shift_template — nothing hardcoded.
   ▼
call_llm
   │  Invoke Claude via the Anthropic Python SDK (async).
   │  Store full raw response text in state for debugging.
   ▼
parse_schedule
   │  Extract the JSON array from the LLM response.
   │  On parse failure: add to state.errors, set status="PARSE_ERROR",
   │  proceed to emit_result — never raise unhandled exception.
   ▼
validate_and_update_availability
   │  For each proposed shift, verify the employee's window is not
   │  already consumed in availability_draft.
   │  Conflict + retry_count == 0 → increment retry_count,
   │    add conflict note to prompt, route back to call_llm.
   │  Conflict + retry_count >= 1 → mark shift status="CONFLICT",
   │    continue (no more retries).
   │  No conflict → mark windows consumed in availability_draft.
   ▼
emit_result
   │  Yield one NDJSON line: { location_id, location_name, shifts, errors, status }
   │  Append LocationResult to state.draft_schedules.
   │
   ▼
[conditional edge]
   ├── more locations remain → increment current_location_index,
   │                           reset retry_count to 0,
   │                           loop back to load_location_context
   └── all locations done → [END]
```

### State Schema (`scheduling/state.py`)

```python
from typing import TypedDict, List

class ShiftAssignment(TypedDict):
    employee_id: str
    employee_name: str
    role_id: str
    role_name: str           # from shift template; never hardcoded
    location_id: str
    date: str                # "YYYY-MM-DD"
    start_time: str          # ISO 8601 with tz offset
    end_time: str            # ISO 8601 with tz offset
    status: str              # "ok" | "CONFLICT"

class LocationResult(TypedDict):
    location_id: str
    location_name: str
    shifts: List[ShiftAssignment]
    errors: List[str]
    status: str              # "ok" | "PARSE_ERROR" | "CONFLICT"

class SchedulingState(TypedDict):
    # ── Input (set once before graph runs) ──────────────
    company_id: str
    week_start_date: str
    locations: List[dict]        # ordered list of all locations to process
    shift_templates: dict        # keyed by location_id
    employees: List[dict]        # all employees with roles, affinities, availability

    # ── Mutable availability draft ───────────────────────
    # Maps employee_id → list of consumed {"start": str, "end": str} dicts.
    # Deep-copied from employee_availability at run start.
    # Updated after each location to prevent cross-location double-booking.
    availability_draft: dict

    # ── Progress ─────────────────────────────────────────
    current_location_index: int
    completed_location_ids: List[str]
    retry_count: int             # reset to 0 after each location

    # ── Output ───────────────────────────────────────────
    draft_schedules: List[LocationResult]
    errors: List[str]
```

### Key Node: `validate_and_update_availability` (`scheduling/nodes.py`)

1. For each `ShiftAssignment` in the just-parsed schedule, look up `availability_draft[employee_id]` and check for time overlap with any already-consumed window.
2. If overlap found:
    - `retry_count == 0`: increment `retry_count`, add a conflict description to state (used by `build_prompt` on the retry), route edge back to `call_llm`.
    - `retry_count >= 1`: mark that shift `status="CONFLICT"`. Do not retry again — continue to `emit_result`.
3. No overlap: append each assigned window to `availability_draft[employee_id]` before moving on.

This guarantees an employee working Location X on Monday 9–5 cannot appear in Location Y's schedule for an overlapping slot.

### Prompt Builder (`scheduling/prompts.py`)

```python
from typing import List

def build_schedule_prompt(
    location: dict,
    shift_template: dict,
    employees: List[dict],    # availability already filtered against availability_draft
    week_start_date: str,
    conflict_notes: str = "",
) -> str:
    """
    Fully parameterized — no hardcoded role names anywhere in this function.
    Role names, headcounts, and time ranges are injected from
    shift_template["weekly_schedule"]. Employee role assignments come from
    their employee_roles records passed in via the employees list.
    """
    role_requirements = _format_role_requirements(shift_template)
    employee_list = _format_employees(employees)

    conflict_section = (
        f"CONFLICT NOTES FROM PREVIOUS ATTEMPT\n"
        f"=====================================\n{conflict_notes}\n\n"
        if conflict_notes else ""
    )

    return f"""You are a scheduling assistant for {location['name']} ({location['timezone']}).

SHIFT REQUIREMENTS
==================
{role_requirements}
Format per line: Day | Role Name | Required headcount | Time range
(Role names and times are read from the shift template — they are not fixed values.)

EMPLOYEE ROSTER
===============
{employee_list}
Each entry: id, name, roles ([{{role_name, skill_level}}]),
affinities ([{{target_id, level}}]),
available_windows (pre-filtered; do not schedule outside these windows).

AFFINITY RULES
==============
level =  1.0  → MUST schedule together on every shared shift (hard constraint)
level = -1.0  → MUST NOT share any shift (hard constraint)
|level| < 1   → soft preference; best effort

{conflict_section}INSTRUCTIONS
============
1. Schedule the week of {week_start_date} using only days and roles in SHIFT REQUIREMENTS.
2. Only assign employees to roles listed in their "roles" array.
3. Only schedule employees within their available_windows.
4. Honour all hard affinity constraints. Optimise for soft ones.
5. Distribute hours fairly among employees of the same role.
6. Prefer higher skill_level employees for demanding roles.

OUTPUT FORMAT
=============
Return ONLY a valid JSON array — no prose, no markdown, no code fences.
Each element:
{{
  "employee_id": "<uuid>",
  "employee_name": "<name>",
  "role_name": "<role name from shift template>",
  "date": "YYYY-MM-DD",
  "start_time": "YYYY-MM-DDTHH:MM:SS+HH:MM",
  "end_time":   "YYYY-MM-DDTHH:MM:SS+HH:MM"
}}"""
```

---

## Frontend (`frontend/src/`)

### Routing (React Router v6)

```
/login
/register
/manager/dashboard
/manager/company
/manager/regions
/manager/locations
/manager/roles
/manager/employees
/manager/shift-templates
/manager/schedule
/employee/availability
```

### Auth

Store the JWT in `localStorage` (acceptable for MVP). `useAuth` hook manages login/logout state and attaches `Authorization: Bearer <token>` to all requests via a shared `apiFetch` wrapper in `src/api/`.

### Schedule Page (`pages/manager/Schedule.tsx`)

1. Manager selects a week via `WeekPicker` (defaults to next Monday).
2. Clicks **Generate Schedule** → `POST /api/v1/schedules/generate`.
3. `useScheduleStream` hook reads the NDJSON stream using the Fetch Streams API:
    - Each parsed line is one `LocationResult`.
    - Append to local React state; each new result renders a location card immediately.
4. Progress bar shows `Location 2 of 5`.
5. Each location card: inline-editable table with columns `Day | Time | Role | Employee`.
6. Per-location actions:
    - **Approve** → `POST /schedules/{id}/approve` → saves `shift_schedule` (status=`approved`) + all `shifts`.
    - **Reject / Regenerate** → `POST /schedules/{id}/reject` → re-triggers generation for that location only; backend passes the same `availability_draft` minus that location's prior contribution back into the graph.
    - **Edit manually** → inline cell editing before approving.
7. Once all locations are approved, show "Schedule Complete" banner.

### Shared `DataTable` Component (`components/shared/DataTable.tsx`)

Must support: column definitions with types (text, select, date), inline cell editing, row-level save/discard, and an optional bulk-action toolbar. Used on Employees, Shift Templates, and Schedule pages.

---

## Seed Data (`backend/seed.py`)

Run with `python seed.py` from the `backend/` directory. Must be idempotent (`INSERT ... ON CONFLICT DO NOTHING`).

| Entity | Values |
|---|---|
| Company | name=`"Acme Corp"`, slug=`"abc123"` |
| Manager user | email=`manager@abc123.com`, password=`abc123`, user_role=`manager` |
| Employee user | email=`employee1@abc123.com`, password=`abc123`, user_role=`employee` |
| Roles | At least 2 user-defined names (e.g. `"Floor Associate"`, `"Team Lead"`) — illustrative only, not hardcoded in app logic |
| Region | 1 region |
| Location | 1 location in the region, timezone=`"America/New_York"` |
| Employees | 7 employees; at least one linked to the employee user above |
| Shift template | 1 template for the location, Mon–Fri, using the seeded roles |
| Availability | Current week pre-populated for all 7 employees |

---

## Docker

### `Dockerfile` (multi-stage)

```dockerfile
# Stage 1 — build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — backend runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=frontend-build /app/frontend/dist ./static
COPY backend/ ./backend
RUN pip install uv && uv pip install --system -r backend/requirements.txt
ENV PYTHONPATH=/app
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

When `ENV=production`, FastAPI mounts `./static` at `/` using `StaticFiles` to serve the built frontend.

### `docker-compose.yml`

Services: `postgres`, `backend`, `frontend` (Vite dev server, dev-only profile). The `backend` service depends on `postgres` being healthy. All env vars via `env_file: .env`.

---

## Testing (`tests/`)

All backend tests use `pytest` per `CLAUDE.md`. All code must pass linting before commit.

### `conftest.py`
- Async SQLAlchemy session scoped per test (`pytest-asyncio`).
- `AsyncClient` wrapping the FastAPI app (`httpx`).
- Fixture: seeds one test company, one manager, two employees, one location, two roles, one shift template.
- Fixture: returns a valid JWT for the test manager.

### Required test files

| File | Covers |
|---|---|
| `test_auth.py` | Register, login, token validation, 401 on missing token, 403 on employee hitting manager route |
| `test_employees.py` | CRUD, bulk CSV upload, company isolation (company B cannot see company A's employees) |
| `test_shift_templates.py` | CRUD, weekly_schedule JSON validation |
| `test_schedule_pipeline.py` | Run LangGraph graph with mocked `call_llm` node; assert `availability_draft` updated after first location; assert no double-booking when two locations share an employee |

---

## Environment Variables

```env
# .env  (used by both docker-compose and local dev)

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://shiftsync:shiftsync@localhost:5432/shiftsync

# JWT
SECRET_KEY=change-me-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Anthropic
ANTHROPIC_API_KEY=

# Email (welcome email on manager registration)
RESEND_API_KEY=
FROM_EMAIL=noreply@shiftsync.example.com

# App
ENV=development     # set to "production" to serve frontend from ./static
```

---

## Features Checklist
- p[]
- [ ] JWT auth — register, login, `get_current_user`, `require_manager` dependency
- [ ] Company-level multi-tenancy enforced on every SQLAlchemy query via `company_id`
- [ ] Manager CRUD for: company, regions, locations, roles, employees, shift templates
- [ ] Employee availability self-service (employees write only their own rows)
- [ ] Bulk CSV upload for employees (role names matched from DB — never hardcoded)
- [ ] LangGraph pipeline embedded in backend: serial per location, shared `availability_draft`
- [ ] Parameterized prompt builder — zero hardcoded role names in `scheduling/prompts.py` or anywhere else
- [ ] `availability_draft` updated after each location; prevents cross-location double-booking
- [ ] `StreamingResponse` NDJSON from `/schedules/generate`; `useScheduleStream` hook on frontend
- [ ] Per-location Approve / Reject+Regenerate / Edit workflow
- [ ] Welcome email on manager registration
- [ ] Idempotent seed script
- [ ] Multi-stage `Dockerfile` + `docker-compose.yml`
- [ ] pytest suite: auth, CRUD, tenant isolation, scheduling pipeline

---

## README Requirements

The generated `README.md` must include:

1. **Project overview** — one paragraph
2. **Prerequisites** — Node 20+, Python 3.11+, `uv`, Docker
3. **Local setup (no Docker):**
   ```bash
   # Backend
   cd backend
   uv pip install -r requirements.txt
   alembic upgrade head
   python seed.py
   uvicorn main:app --reload      # :8000

   # Frontend (separate terminal)
   cd frontend && npm install && npm run dev   # :5173
   ```
4. **Docker setup:** `docker-compose up --build`
5. **Running tests:** `pytest tests/`
6. **Environment variables** — table of all vars with descriptions
7. **Manual verification checklist:**
    - Auth: register as manager → JWT returned; employee token rejected on manager routes (403)
    - CRUD: create a region, location, role, employee; verify DB rows
    - Bulk upload: POST sample CSV; verify employees created with correct roles
    - Availability: employee updates own availability; manager updates another employee's
    - Schedule generation: POST `/schedules/generate`; verify NDJSON lines arrive per-location in order
    - Availability draft: run generation with two locations sharing an employee; verify no double-booking
    - Approve: approve one location; verify `shift_schedule.status = 'approved'` and `shifts` rows exist
    - Reject/regenerate: reject a schedule; verify re-prompt fires and new NDJSON arrives
    - Tenant isolation: authenticate as company B; GET company A's employees; expect empty list or 404

---

## Notes for Claude Code

- **Roles are never hardcoded.** No role name string literal (e.g. `"r1"`, `"Barista"`) may appear anywhere outside `seed.py`. Every role reference in routers, services, LangGraph nodes, and prompts must come from the `roles` table.
- The `availability_draft` in `SchedulingState` is the authoritative source for remaining availability during one scheduling run. It starts as a deep copy of the week's `employee_availability` records and is mutated in-place by `validate_and_update_availability` after each location.
- `POST /schedules/generate` returns a `StreamingResponse(media_type="application/x-ndjson")`. Each yielded line is `json.dumps(LocationResult) + "\n"`. The frontend `useScheduleStream` hook reads this with the Fetch Streams API (`response.body.getReader()`).
- Parse LLM output defensively in `parse_schedule`. If JSON is malformed, set `status="PARSE_ERROR"`, store `raw_llm_output` in the `shift_schedules` row, emit the result, and continue — never raise an unhandled exception inside the graph.
- All timestamps must carry timezone offsets. Derive the timezone from `location.timezone` using `zoneinfo.ZoneInfo`.
- Do not install new dependencies without updating `backend/requirements.txt` or `frontend/package.json`, per `CLAUDE.md`.
- Use type hints extensively in all Python code, per `CLAUDE.md`.
- Use functional components and hooks in all React code, per `CLAUDE.md`.
