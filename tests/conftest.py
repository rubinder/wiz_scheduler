"""
Shared test fixtures for WizScheduler.

Uses Postgres via testcontainers for all tests to match production environment.
"""

import pytest

def pytest_configure(config):
    """Register custom pytest markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test (requires HTTP client/database)"
    )
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test (pure logic, no database/HTTP)"
    )

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
from testcontainers.community.postgres import PostgresContainer

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
# Postgres container (session-scoped) — starts once, used for all tests
# ---------------------------------------------------------------------------

_pg_container = None
_test_engine = None

def pytest_sessionstart():
    """Called before test collection; start the Postgres container."""
    global _pg_container, _test_engine

    from sqlalchemy.pool import NullPool

    _pg_container = PostgresContainer("postgres:16-alpine")
    _pg_container.start()

    # Extract connection details
    host = _pg_container.get_container_host_ip()
    port = _pg_container.get_exposed_port(5432)
    db_name = _pg_container.dbname
    user = _pg_container.username
    password = _pg_container.password

    # Build connection string for asyncpg (production dialect)
    TEST_DATABASE_URL = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    # Create engine with NullPool to avoid connection pool issues across event loops
    _test_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)

def pytest_sessionfinish(session, exitstatus):
    """Called after test run; stop the Postgres container and dispose engine."""
    global _pg_container, _test_engine

    if _test_engine:
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_test_engine.dispose())
            loop.close()
        except Exception:
            pass

    if _pg_container:
        _pg_container.stop()

def _get_test_engine():
    """Get the test engine. Must be called after pytest_sessionstart() has run."""
    if _test_engine is None:
        raise RuntimeError("Test engine not initialized. Did pytest_sessionstart() run?")
    return _test_engine

# Lazy reference for backward compatibility
class _TestEngineProxy:
    """Proxy that evaluates test_engine lazily."""
    def __getattr__(self, name):
        return getattr(_get_test_engine(), name)

    def begin(self):
        return _get_test_engine().begin()

    def connect(self):
        return _get_test_engine().connect()

test_engine = _TestEngineProxy()

def get_test_session_factory():
    """Get the session factory."""
    return async_sessionmaker(_get_test_engine(), class_=AsyncSession, expire_on_commit=False)

TestSessionLocal = None  # Will be set during session start

# ---------------------------------------------------------------------------
# ID helper — we supply IDs explicitly (matches production UUID generation)
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
    engine = _get_test_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def reset_in_process_rate_limiters():
    """Reset in-memory rate-limit counters between tests so state from one
    test doesn't bleed into another and trip 429s on unrelated requests."""
    from backend.services.rate_limit import (
        compliance_check_limiter,
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    )
    for lim in (
        compliance_check_limiter,
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    ):
        lim.reset()
    yield
    for lim in (
        compliance_check_limiter,
        forgot_password_limiter,
        login_limiter,
        register_limiter,
        resend_verification_limiter,
        schedule_generate_ai_limiter,
    ):
        lim.reset()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_test_session_factory()() as session:
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
    from backend.services.passwords import hash_password

    pwd = hash_password("testpass")
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
    from backend.services.passwords import hash_password

    pwd = hash_password("testpass")
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
# Schema compatibility test fixtures (fresh instances per test)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_company(db_session: AsyncSession) -> Company:
    """Fresh company for compatibility tests."""
    company = Company(id=_id(), name="Test Company", slug=_id())
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)
    return company


@pytest_asyncio.fixture
async def test_region(db_session: AsyncSession, test_company: Company) -> Region:
    """Fresh region for compatibility tests."""
    region = Region(id=_id(), company_id=test_company.id, name="Test Region")
    db_session.add(region)
    await db_session.commit()
    await db_session.refresh(region)
    return region


@pytest_asyncio.fixture
async def test_location(db_session: AsyncSession, test_company: Company, test_region: Region) -> Location:
    """Fresh location for compatibility tests."""
    loc = Location(
        id=_id(),
        company_id=test_company.id,
        region_id=test_region.id,
        name="Test Location",
        timezone="UTC",
    )
    db_session.add(loc)
    await db_session.commit()
    await db_session.refresh(loc)
    return loc


@pytest_asyncio.fixture
async def test_employee(db_session: AsyncSession, test_company: Company) -> Employee:
    """Fresh employee for compatibility tests."""
    emp = Employee(
        id=_id(),
        company_id=test_company.id,
        full_name="Test Employee",
        email="test@example.com",
    )
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    return emp


@pytest_asyncio.fixture
async def db(db_session: AsyncSession) -> AsyncSession:
    """Alias for db_session for schema compatibility tests."""
    return db_session

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
