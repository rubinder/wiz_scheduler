"""
Shared test fixtures for WizScheduler.

Uses SQLite + aiosqlite as the test database so no Postgres is required.
"""

# `backend.main` imports `backend.middleware.metrics`, which initializes
# prometheus_client in multiprocess mode using PROMETHEUS_MULTIPROC_DIR.
# Production creates that directory in the Docker entrypoint; the test
# runner doesn't go through Docker, so create it here before any backend
# import touches metrics.
import os
os.makedirs(os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_multiproc"), exist_ok=True)

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.database import Base
from backend.dependencies import get_db
from backend.main import app
from backend.models import (
    Company,
    Employee,
    EmployeeRole,
    Location,
    Region,
    Role,
    ShiftTemplate,
    User,
)

# ---------------------------------------------------------------------------
# Async SQLite engine for tests
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def pytest_sessionfinish(session, exitstatus):
    """Dispose the test engine so aiosqlite's per-connection thread pool
    shuts down. Without this, pytest prints the summary but the Python
    process hangs because the aiosqlite threads keep the interpreter alive.
    """
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(test_engine.dispose())
        loop.close()
    except Exception:
        pass


# Register SQLite-compatible replacements for PostgreSQL functions
@event.listens_for(test_engine.sync_engine, "connect")
def _register_sqlite_functions(dbapi_connection, connection_record):
    """Make server_default=text("now()") and gen_random_uuid() work on SQLite."""
    import sqlite3
    from datetime import datetime, timezone as _tz

    dbapi_connection.create_function("now", 0, lambda: datetime.now(_tz.utc).isoformat())


# ---------------------------------------------------------------------------
# ID helper — SQLite has no gen_random_id(); we supply IDs explicitly.
# ---------------------------------------------------------------------------

from backend.utils.id_gen import generate_short_id

def _id() -> str:
    return generate_short_id()


# ---------------------------------------------------------------------------
# Session-scoped DB setup
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    """Create all tables before each test, drop after."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def reset_in_process_rate_limiters():
    """Reset in-memory rate-limit counters between tests so state from one
    test doesn't bleed into another and trip 429s on unrelated requests."""
    from backend.services.rate_limit import (
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    )
    for lim in (
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    ):
        lim.reset()
    yield
    for lim in (
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    ):
        lim.reset()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient that overrides get_db to use the test session."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Deterministic IDs for fixtures
# ---------------------------------------------------------------------------

COMPANY_ID = _id()
COMPANY2_ID = _id()
REGION_ID = _id()
LOCATION_ID = _id()
ROLE_FLOOR_ID = _id()
ROLE_LEAD_ID = _id()
MANAGER_USER_ID = _id()
EMPLOYEE_USER_ID = _id()
EMPLOYEE1_ID = _id()
EMPLOYEE2_ID = _id()
SHIFT_TEMPLATE_ID = _id()


def _make_token(user_id: str, company_id: str, role: str) -> str:
    """Create a JWT identical to what the auth router produces."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "user_role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ---------------------------------------------------------------------------
# Seed fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed_company(db_session: AsyncSession) -> Company:
    company = Company(id=COMPANY_ID, name="Test Corp", slug="test123")
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


@pytest_asyncio.fixture
async def seed_region(db_session: AsyncSession, seed_company: Company) -> Region:
    region = Region(id=REGION_ID, company_id=COMPANY_ID, name="East Coast")
    db_session.add(region)
    await db_session.commit()
    await db_session.refresh(region)
    return region


@pytest_asyncio.fixture
async def seed_roles(db_session: AsyncSession, seed_company: Company) -> list[Role]:
    r1 = Role(id=ROLE_FLOOR_ID, company_id=COMPANY_ID, name="Floor Associate", description="General floor")
    r2 = Role(id=ROLE_LEAD_ID, company_id=COMPANY_ID, name="Team Lead", description="Supervision")
    db_session.add_all([r1, r2])
    await db_session.commit()
    await db_session.refresh(r1)
    await db_session.refresh(r2)
    return [r1, r2]


@pytest_asyncio.fixture
async def seed_location(db_session: AsyncSession, seed_region: Region) -> Location:
    loc = Location(
        id=LOCATION_ID,
        company_id=COMPANY_ID,
        region_id=REGION_ID,
        name="Downtown Store",
        timezone="America/New_York",
    )
    db_session.add(loc)
    await db_session.commit()
    await db_session.refresh(loc)
    return loc


@pytest_asyncio.fixture
async def seed_manager(db_session: AsyncSession, seed_company: Company) -> User:
    from passlib.context import CryptContext

    pwd = CryptContext(schemes=["bcrypt"]).hash("testpass")
    user = User(
        id=MANAGER_USER_ID,
        company_id=COMPANY_ID,
        email="manager@test.com",
        hashed_password=pwd,
        full_name="Test Manager",
        user_role="manager",
        # An established account, like every row the 0032 backfill stamped.
        # Tests that care about the unverified state build their own user;
        # leaving this NULL would 403 every generate test for a reason none
        # of them are about.
        email_verified_at=datetime.now(timezone.utc),
        email_normalized="manager@test.com",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def seed_employee_user(db_session: AsyncSession, seed_company: Company) -> User:
    from passlib.context import CryptContext

    pwd = CryptContext(schemes=["bcrypt"]).hash("testpass")
    user = User(
        id=EMPLOYEE_USER_ID,
        company_id=COMPANY_ID,
        email="employee@test.com",
        hashed_password=pwd,
        full_name="Test Employee",
        user_role="employee",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def seed_employees(
    db_session: AsyncSession, seed_company: Company, seed_location: Location, seed_roles: list[Role]
) -> list[Employee]:
    e1 = Employee(
        id=EMPLOYEE1_ID,
        company_id=COMPANY_ID,
        full_name="Alice Johnson",
        email="alice@test.com",
    )
    e2 = Employee(
        id=EMPLOYEE2_ID,
        company_id=COMPANY_ID,
        full_name="Bob Smith",
        email="bob@test.com",
    )
    db_session.add_all([e1, e2])
    await db_session.flush()

    # Assign roles
    er1 = EmployeeRole(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID, skill_level=3
    )
    er2 = EmployeeRole(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE2_ID, role_id=ROLE_LEAD_ID, skill_level=2
    )
    db_session.add_all([er1, er2])
    await db_session.commit()
    await db_session.refresh(e1)
    await db_session.refresh(e2)
    return [e1, e2]


@pytest_asyncio.fixture
async def seed_shift_template(
    db_session: AsyncSession, seed_company: Company, seed_location: Location, seed_roles: list[Role]
) -> ShiftTemplate:
    weekly = [
        {
            "day": "Monday",
            "role_name": "Floor Associate",
            "role_id": str(ROLE_FLOOR_ID),
            "headcount": 2,
            "start_time": "09:00",
            "end_time": "17:00",
        },
        {
            "day": "Monday",
            "role_name": "Team Lead",
            "role_id": str(ROLE_LEAD_ID),
            "headcount": 1,
            "start_time": "09:00",
            "end_time": "17:00",
        },
    ]
    tmpl = ShiftTemplate(
        id=SHIFT_TEMPLATE_ID,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        name="Weekday Standard",
        weekly_schedule=weekly,
    )
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    return tmpl


@pytest_asyncio.fixture
def manager_token(seed_manager: User) -> str:
    return _make_token(MANAGER_USER_ID, COMPANY_ID, "manager")


@pytest_asyncio.fixture
def employee_token(seed_employee_user: User) -> str:
    return _make_token(EMPLOYEE_USER_ID, COMPANY_ID, "employee")


# ---------------------------------------------------------------------------
# Composite fixture used by team-collab + locking tests
# ---------------------------------------------------------------------------

from types import SimpleNamespace


@pytest_asyncio.fixture
async def seeded_company(
    seed_company: Company, seed_manager: User, seed_location: Location
) -> SimpleNamespace:
    """Returns a namespace with `company_id`, `manager_user_id`, and
    `location_id` pointing at the seeded Company + manager User + Location.
    `og_id` is None unless the caller explicitly creates one. Use this when a
    test needs both a Company and a manager User in one shot without composing
    multiple fixtures manually.
    """
    return SimpleNamespace(
        company_id=seed_company.id,
        manager_user_id=seed_manager.id,
        location_id=seed_location.id,
        og_id=seed_company.ownership_group_id,
    )


# ---------------------------------------------------------------------------
# Fixtures for scheduling-preferences API tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_employee_id(seed_employees: list[Employee]) -> str:
    """A single employee id belonging to the seeded (manager_token) company."""
    return seed_employees[0].id


@pytest_asyncio.fixture
async def other_company_employee_id(db_session: AsyncSession) -> str:
    """An employee belonging to a DIFFERENT company than `manager_token`.

    Used to assert that write endpoints refuse to act on another company's
    employee even when the request is otherwise well-formed.
    """
    company = Company(id=COMPANY2_ID, name="Other Co", slug="other-co")
    db_session.add(company)
    await db_session.flush()
    employee = Employee(
        id=_id(), company_id=COMPANY2_ID, full_name="Outsider Employee"
    )
    db_session.add(employee)
    await db_session.commit()
    return employee.id
