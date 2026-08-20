# WizScheduler

**[wizscheduler.com](https://wizscheduler.com)**

## Overview

WizScheduler is a multi-tenant scheduling platform for shift-based businesses — restaurants, retail, and hospitality groups that staff multiple locations from a shared pool of employees. Managers model their operation once (locations, roles, skill levels, shift templates, employee availability, interpersonal affinities), then generate a week of shift assignments with a single action. An LLM-backed pipeline proposes the schedule; a deterministic validator decides what actually ships. The intended user is the multi-unit operator — a franchisee with six stores, a restaurant group with three brands — for whom scheduling is a recurring several-hour weekly task made harder by cross-location staff sharing and, increasingly, by predictive-scheduling law. Everything is per-tenant and configuration-driven: no role names, shift types, or business rules are hardcoded anywhere outside the seed script.

## Architecture

### Why LangGraph for the agentic pipeline

Schedule generation is not one LLM call. It is: load context → build a parameterized prompt → call the model → parse → validate against business rules → reconcile against everything already committed → maybe retry with the failure reasons fed back in → emit. That is a state machine with a conditional cycle in it, and the honest options were to hand-roll the loop or to use a graph runtime.

LangGraph won for three reasons specific to this problem:

**The retry edge is a real cycle, not a wrapper.** When validation finds a double-booking, the pipeline goes *back* to `build_prompt` with `conflict_notes` populated, so the second attempt sees exactly why the first failed. `_should_retry_or_emit` is a conditional edge with a bounded retry count (one retry, then the location is emitted as `CONFLICT`). Expressing that as a graph edge keeps the termination condition in one readable place instead of scattered across nested try/retry blocks.

**Shared mutable state across a serial fan-out.** Locations are processed serially in a single graph run, and the whole reason they cannot be processed independently is `SchedulingState.availability_draft` — a deep copy of employee availability that is mutated after each location so that location #4 cannot book someone already working at location #2. `employee_weekly_hours_draft` accumulates the same way for weekly hour caps. LangGraph's typed state channel gives that accumulator a first-class home; the alternative is threading a mutable dict through a call chain and hoping no one forgets.

**Node-level swappability.** The graph is built at runtime, not statically. When a caller requests deterministic scheduling, `build_graph` wires `load_location_context → local_schedule → validate_schedule` and the LLM nodes are never added to the graph at all. The AI path wires `load_location_context → build_prompt → call_llm → parse_schedule → validate_schedule`. Both converge on the identical validation and emission nodes. That is the payoff: **the validator cannot be bypassed by changing how shifts are proposed**, because it is downstream of both branches by construction.

The pipeline streams — `POST /schedules/generate` returns NDJSON, one line per location, consumed by `useScheduleStream` on the frontend. A 12-location generation shows results for location 1 while location 7 is still running.

### The four-layer validator gate

The LLM is treated as an untrusted proposer. Nothing it returns reaches the database without passing four independent layers, each with a different failure mode and each degrading rather than throwing.

**Layer 1 — Structural (`parse_schedule`).** Extracts assignments from the model response. The call uses Anthropic tool-use with an explicit `submit_schedule` input schema rather than asking for free-text JSON, so the common failure mode is eliminated at the source. Parsing is still defensive: on failure the location is marked `status="PARSE_ERROR"` and the run continues. No node in the graph raises an unhandled exception — one malformed location must never take down a twelve-location run.

**Layer 2 — Per-shift legality (`validate_schedule`).** Every proposed shift is checked independently against seven rules: the employee exists at this location; the employee is qualified for the role; the shift falls inside an explicit availability window; `start_time < end_time`; the date lands inside the schedule week; the shift misses every recurring day blackout; the employee stays under `max_hours_per_week`; and the shift respects the location's `min_rest_hours` (the NYC Fair Workweek "clopening" rule). Two details matter here. Employees are **unavailable by default** — a shift is valid only if a window explicitly covers it, so a missing availability record fails closed. And both sides of the availability comparison are converted into the location's `ZoneInfo` before comparing wall-clock times, because a UTC-stored window and a local-stored shift describing the same instant will not compare equal as strings.

**Layer 3 — Coverage reconciliation (`validate_schedule`, second pass).** Layer 2 *drops* invalid shifts rather than rejecting the whole schedule. That would silently under-staff the week, so layer 3 walks the shift template, compares required headcount per (day, role) against what survived, and injects explicit `VACANT` placeholders for the shortfall. The manager sees "Saturday, 2 servers required, 1 filled" instead of a schedule that looks complete and isn't.

**Layer 4 — Cross-location reconciliation (`validate_and_update_availability`).** Layers 2 and 3 only know about the current location. Layer 4 checks the surviving shifts for overlaps against `availability_draft` — the running record of everything committed at *every previously processed location this run* — then commits them into the draft. This is what makes serial processing correct: it is the only layer that can catch the same employee booked at two stores at 6pm Friday. On conflict it routes back through the retry edge once, then emits `CONFLICT`.

Why it exists: an LLM asked to satisfy eight simultaneous hard constraints across a shared employee pool will satisfy most of them most of the time. "Most" is not a scheduling product. The gate converts a probabilistic proposer into a system with deterministic guarantees — every shift that reaches the database provably satisfies every hard constraint, because a rule engine checked it, not because the prompt asked nicely. The generation quality determines how *good* the schedule is; the validator determines whether it is *legal*. Those are deliberately different subsystems.

### Tenant isolation

Isolation is enforced at the application layer, on every query, from a JWT-derived tenant scope.

`get_current_user` decodes the bearer token, loads the `User`, and hands routers `current_user.company_id`. Every query in every router filters on it. Manager-only routes sit behind `require_manager`; employee routes additionally assert `employee.user_id == current_user.id`, so an authenticated employee cannot read a peer's record inside their own tenant. Tests assert the boundary directly — a company A token must not reach company B data.

**What this does not do:** there is no PostgreSQL row-level security in this codebase. No `CREATE POLICY`, no `ALTER TABLE … ENABLE ROW LEVEL SECURITY`, no session-variable plumbing via `set_config`/`current_setting`. The application is the sole enforcement point, which means a query written without a `company_id` predicate is a cross-tenant leak that the database will happily serve. This is a deliberate, and reversible, trade-off: RLS is defense-in-depth against exactly the class of bug that application-layer filtering is vulnerable to, and it is the most valuable remaining hardening step. It is called out here rather than papered over because a reader auditing this system should know where the boundary actually lives.

### The ownership group hierarchy

Real multi-unit operators are not one flat tenant. A franchisee owns four Company records under one brand; a restaurant group owns three brands with disjoint staff and shared back-office billing. The data model is therefore three levels — `OwnershipGroup → Company → Location` — and the JWT is the mechanism that makes it navigable.

Tokens carry `sub`, `company_id`, and `user_role`. The `company_id` claim is the *active* tenant scope, not an immutable property of the user: `POST /auth/switch-company` lets a manager move between sibling companies, but only after the server confirms the target shares the caller's `ownership_group_id`. It then persists the change and mints a fresh token scoped to the new company. The client never chooses its own scope — it asks, and the server re-issues. Ownership group membership is resolved from the database on each switch rather than embedded in the token, so revoking a company from a group takes effect at the next switch instead of at token expiry.

The hierarchy maps onto business logic that genuinely lives at different levels:

- **Company level** — employees, roles, locations, shift templates. Staff belong to a company. This is the scheduling boundary, and it is why the AI pipeline's cross-location double-booking check operates within a company: two stores under one owner share a labor pool, two brands do not.
- **Ownership group level** — billing and metered consumption. Stripe customer and subscription, `ai_credits_usd`, autoreload configuration and its failure state, cancellation and the read-only grace period, email quota, integration-import quota, storage snapshots. The operator holds one payment relationship regardless of how many companies sit under it.

`get_ownership_group_company_ids` is the fan-out primitive: it resolves the caller's group and returns every company ID in it, so group-scoped reporting can span companies without weakening the per-company default. Billing state suspends the expensive capability, never the customer's access to their own records — an operator in grace can always retrieve their data and reactivate.

### The free plan

Registration is free by default — `stripe_session_id` on `POST /auth/register` is now optional, and an operator can use the product before ever talking to Stripe. Upgrading is a separate, later action: `POST /billing/upgrade-checkout` starts a Stripe Checkout session and `POST /billing/confirm-upgrade` completes it, matched back to the ownership group via `client_reference_id`.

Free, per ownership group: 1 location, 5 employees, 5 schedule generations per calendar month. AI schedule generation and the 7shifts/Deputy importers are paid-only — free tenants get the deterministic local scheduler, which is not a lesser product so much as a different one (see "The same validator guards the AI and deterministic paths" below). Every write path that creates an `Employee` or `Location` calls `assert_can_add` (`backend/services/plan.py`) before inserting, under a row lock on the ownership group so two managers racing to add employee #5 can't both succeed; bulk uploads are all-or-nothing rather than partially applying up to the limit. `POST /schedules/generate` is gated by `check_can_generate`, which replaced `require_active_billing` — that dependency no longer exists.

Plan is *derived*, not stored: `paid` iff `stripe_subscription_id IS NOT NULL AND canceled_at IS NULL` on the ownership group, `free` otherwise. A Company with no `ownership_group_id` at all is treated as unlimited — that state only exists in seed data and tests, no production path creates one. There is deliberately no `plan` column. A stored column is a second source of truth, and it drifts the first time a Stripe webhook is missed, retried out of order, or replayed after a manual fix in the Stripe dashboard — the row says one thing, Stripe says another, and nothing forces them back into agreement. Deriving the plan on every read means there is only ever one fact to be wrong about: the two columns Stripe itself last wrote. This mirrors why plan is billing-owned rather than company-owned in the first place — see "The ownership group hierarchy" above.

## Tech Stack

**Backend** — Python 3.11+, FastAPI, Pydantic v2 (+ pydantic-settings), SQLAlchemy 2.x async with `Mapped[...]`/`mapped_column`, asyncpg, Alembic, PostgreSQL 16.

**AI scheduling** — LangGraph, Anthropic Claude (`claude-sonnet-4-20250514`) via the async SDK with tool-use structured output.

**Auth** — JWT via python-jose, bcrypt password hashing via passlib, Google OAuth via google-auth.

**Frontend** — React 18, TypeScript 5, Vite 6, Tailwind CSS 3, React Router 6, Stripe.js.

**Payments & email** — Stripe (subscriptions, metered AI credits, webhooks), Resend (transactional email).

**Observability** — prometheus-client with request metrics middleware, structured failure logging middleware, CloudWatch alarms.

**Infrastructure** — AWS via Terraform: ECS Fargate behind an ALB, RDS PostgreSQL, CloudFront + S3 frontend, ECR, WAF, Route 53. CI/CD through GitHub Actions.

**Testing** — pytest, pytest-asyncio, httpx, aiosqlite; Playwright for frontend.

## Key Engineering Decisions

**Roles are data, never string literals.** No role name may appear as a literal anywhere outside `seed.py`. Every reference resolves through the `roles` table. This is enforced as a hard convention because the product's addressable market is defined by it — a `"Server"`/`"Cook"` literal in a validator makes the system a restaurant tool forever. Enforcing it costs indirection in the prompt builder and the validator; it buys a warehouse, a clinic, and a salon as tenants without a code change.

**Employee names never reach the LLM.** The prompt builder sends IDs, roles, and skill levels. `validate_schedule` re-attaches real names from `emp_by_id` after the model has responded. Names are irrelevant to the assignment problem — the model reasons about qualification and availability, not identity — so sending them is pure PII exposure with no upside. It also removes a whole class of bias the model has no business exercising over who gets Saturday night.

**Structured tool-use over prompted JSON.** The Claude call passes a `submit_schedule` tool with a full `input_schema` rather than instructing the model to reply with JSON. Schema conformance moves from something you hope for and parse defensively to something the API enforces. `parse_schedule` stays defensive anyway — the belt is cheap once the suspenders are on.

**Invalid shifts are dropped, not raised on.** Every failure inside the graph degrades to a status: `PARSE_ERROR`, `VALIDATION_ERROR`, `CONFLICT`, `VACANT`. Nothing propagates an exception. In a serial multi-location run, throwing means one bad location destroys eleven good ones and the manager gets a stack trace instead of a schedule. Partial results with explicit, labeled gaps are strictly more useful than an all-or-nothing failure — a manager can fill three `VACANT` slots by hand in two minutes.

**The same validator guards the AI and deterministic paths.** `local_scheduler.py` implements strategy-based assignment (`random`, `rotation`, `rotation_history`, `max_hours`) with no LLM involved. It exists so the product degrades gracefully when the API is down, so tests can exercise the pipeline deterministically and without cost, and so tenants who distrust AI scheduling still have a product. It is wired as an alternate node into the *same* graph, converging on the *same* validation nodes — one implementation of the business rules, two ways to propose against it. Duplicating the rules per path would guarantee they drift.

**Compliance modeled as location configuration, not as code.** NYC Fair Workweek minimum-rest is implemented as `location.min_rest_hours` — a nullable per-location value the validator honors when set. The alternative was an `if location.city == "NYC"` branch. Predictive-scheduling law is spreading city by city with different thresholds, so the jurisdiction-specific branch would have needed rewriting for every new market; the configurable value covers Philadelphia and Seattle on the day a manager types a number into a form. The same rule is enforced in the LLM path, the local scheduler, and the validator, and it accumulates across locations — the whole point is catching a close at store A followed by an open at store B.

**Availability is deny-by-default.** A shift is legal only if an explicit availability window covers it. A missing availability record produces zero shifts, not unrestricted scheduling. This is loud when data is incomplete, which is correct: over-scheduling someone who never said they were free is a labor complaint, while under-scheduling is a visible `VACANT` slot a manager fixes in seconds. Fail closed on the side where the errors are recoverable.

**Timezone correctness is a first-class invariant.** Every timestamp carries an offset, derived from `location.timezone` via `zoneinfo.ZoneInfo`. The availability check converts both the shift and the window into location-local time before comparing wall-clock strings, because comparing a UTC-stored window against a local-stored shift silently produces zero valid shifts — a bug that presents as "the AI didn't schedule anyone" rather than as a timezone error, and costs days to diagnose. A multi-region product has no safe default timezone.

**Serial location processing, deliberately.** Locations could be scheduled in parallel; they are not. Correctness requires a single consistent view of who is already committed, and `availability_draft` provides it only under serial mutation. Parallelizing would mean distributed reservation over a shared employee pool to solve a problem that takes seconds. The user-facing latency concern is answered by NDJSON streaming instead: results appear per-location as they complete, so the run *feels* incremental while remaining sequentially correct.

**Billing state suspends capability, never data access.** `check_can_generate` gates schedule generation alone — both the free monthly cap and the paid-only AI path. A canceled or over-limit operator retains CRUD, login, billing, and GDPR export — they can always reach their own data and reactivate. Locking customers out of their records to collect payment is both a GDPR problem and a reactivation problem.

**A queryable knowledge graph of the codebase is checked in.** `graphify-out/` holds 1,160 nodes and 1,831 edges across 155 communities, refreshed incrementally via `/graphify . --update`. It is the first-pass lookup for "how do these parts connect" and is roughly 27x more token-efficient than scanning source. For an AI-assisted codebase, a machine-readable architecture index is developer tooling, not a novelty.

## Running Locally

### Prerequisites

- Node.js 20+
- Python 3.11+
- `uv` — `pip install uv`
- PostgreSQL 16+ (or Docker, below)

### Backend

```bash
cp .env.example .env            # from the repo root, then edit — see table below

cd backend
uv pip install -r requirements.txt

alembic upgrade head            # migrations (alembic.ini lives here)
python seed.py                  # demo data (idempotent)

cd ..                           # imports are rooted at `backend.*`
uvicorn backend.main:app --reload
```

API on `http://localhost:8000`, interactive docs at `/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Dev server on `http://localhost:5173`, proxying API requests to the backend.

### Docker

```bash
docker compose up --build                    # Postgres + backend with built frontend
docker compose --profile dev up --build      # adds the hot-reload frontend
```

### Tests

Test dependencies are not in `requirements.txt`:

```bash
pip install pytest pytest-asyncio aiosqlite httpx

pytest tests/                                # all
pytest tests/test_auth.py                    # one file
pytest tests/test_auth.py::test_login -v     # one test
```

Tests run against in-memory SQLite — no Postgres instance required.

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Async SQLAlchemy connection string | `postgresql+asyncpg://shiftsync:shiftsync@localhost:5432/shiftsync` |
| `SECRET_KEY` | JWT signing secret | `change-me-in-production` |
| `ALGORITHM` | JWT algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime | `60` |
| `ANTHROPIC_API_KEY` | Claude API key for AI scheduling | (empty) |
| `RESEND_API_KEY` | Resend key for transactional email | (empty) |
| `FROM_EMAIL` | Sender address | `noreply@shiftsync.example.com` |
| `ENV` | `development` / `production` | `development` |

Without `ANTHROPIC_API_KEY`, the deterministic local scheduler still works end to end.

### Pre-deploy checks

Run against production before merging or deploying any change that touches ownership-group billing state (the free-tier plan derivation in particular). Both queries are read-only.

**1. No paying customer may be silently demoted to free.** Expected: `0`.

```sql
SELECT count(*) FROM ownership_groups
WHERE stripe_subscription_id IS NULL AND canceled_at IS NULL;
```

A non-zero result means those ownership groups derive to `free` under the plan logic in `backend/services/plan.py` (see "The free plan" above) and would suddenly be capped at 1 location / 5 employees / 5 generations a month. Investigate every returned row individually — a subscription that's missing here because a webhook was dropped needs to be reconciled with Stripe directly. **Do not blind-backfill** `stripe_subscription_id`; a fabricated value can collide with a real one and, as of migration 0029, will fail to insert.

**2. Uniqueness — this is now a HARD GATE.** Migration 0029 adds partial unique indexes on `ownership_groups.stripe_customer_id` and `ownership_groups.stripe_subscription_id`, and the migration **will fail outright** if either query below returns rows:

```sql
SELECT stripe_subscription_id, count(*) FROM ownership_groups
WHERE stripe_subscription_id IS NOT NULL
GROUP BY 1 HAVING count(*) > 1;

SELECT stripe_customer_id, count(*) FROM ownership_groups
WHERE stripe_customer_id IS NOT NULL
GROUP BY 1 HAVING count(*) > 1;
```

Both must return zero rows before `alembic upgrade head` is run against production. Duplicates here mean the same Stripe customer or subscription somehow got attached to more than one ownership group (webhook replay, a manual DB edit, a bug in an old reactivate/upgrade path) — resolve which row is the true owner and clear or correct the other before migrating, not after.
