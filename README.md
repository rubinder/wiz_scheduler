# WizScheduler

WizScheduler is a full-stack employee scheduling application powered by FastAPI and React. It uses an LLM-backed scheduling pipeline (via LangGraph and Anthropic Claude) to automatically generate shift assignments that respect employee availability, role qualifications, and affinity constraints. Managers can create shift templates, upload employees in bulk, generate weekly schedules, then review and approve or reject the results.

## Prerequisites

- **Node.js** 20+
- **Python** 3.11+
- **uv** (Python package installer) — `pip install uv`
- **Docker** and **Docker Compose** (for containerised setup)
- **PostgreSQL** 16+ (for local non-Docker development)

## Local Setup

### Backend

```bash
cd backend
pip install uv
uv pip install --system -r requirements.txt

# Copy and edit environment variables
cp ../.env.example ../.env

# Run database migrations
alembic upgrade head

# Seed the database
python seed.py

# Start the dev server
uvicorn backend.main:app --reload
```

The API is available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend dev server runs at `http://localhost:5173` and proxies API requests to the backend.

## Docker Setup

```bash
# Start all services (Postgres + backend with built frontend)
docker compose up --build

# Or include the frontend dev server for hot-reload
docker compose --profile dev up --build
```

The backend serves at `http://localhost:8000` and the dev frontend at `http://localhost:5173`.

## Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio aiosqlite httpx

# Run all tests from the project root
pytest tests/ -v
```

Tests use an in-memory SQLite database, so no Postgres instance is required.

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Async SQLAlchemy connection string | `postgresql+asyncpg://shiftsync:shiftsync@localhost:5432/shiftsync` |
| `SECRET_KEY` | JWT signing secret | `change-me-in-production` |
| `ALGORITHM` | JWT algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime in minutes | `60` |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | (empty) |
| `RESEND_API_KEY` | Resend API key for transactional email | (empty) |
| `FROM_EMAIL` | Sender email address | `noreply@shiftsync.example.com` |
| `ENV` | Environment mode (`development` / `production`) | `development` |

## Manual Verification Checklist

- [ ] `POST /api/v1/auth/register` creates a company + manager user, returns JWT
- [ ] `POST /api/v1/auth/login` returns JWT for valid credentials
- [ ] `GET /api/v1/auth/me` returns current user info with valid token
- [ ] Manager can CRUD regions, locations, roles, employees, shift templates
- [ ] Employee token is rejected on manager-only routes (403)
- [ ] Bulk CSV upload creates employees with correct role assignments
- [ ] `POST /api/v1/schedules/generate` streams scheduling events via NDJSON
- [ ] `POST /api/v1/schedules/{id}/approve` creates Shift records
- [ ] `POST /api/v1/schedules/{id}/reject` sets status to rejected
- [ ] Company isolation: users in company A cannot access company B data
- [ ] `docker compose up --build` starts all services successfully
- [ ] `pytest tests/ -v` passes all tests
