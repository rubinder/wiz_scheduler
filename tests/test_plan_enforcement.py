"""Free-plan enforcement across employee/location write paths."""

from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region, User
from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def free_tenant(db_session: AsyncSession) -> dict:
    """A free ownership group with one company, one region, one manager."""
    og_id, company_id, region_id, user_id = _id(), _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Free Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Free Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    db_session.add(User(id=user_id, company_id=company_id,
                        email="mgr@free.test", hashed_password="x",
                        full_name="Mgr", user_role="manager"))
    await db_session.commit()
    return {
        "og_id": og_id,
        "company_id": company_id,
        "region_id": region_id,
        "token": _make_token(user_id, company_id, "manager"),
    }


async def _add_employees(db: AsyncSession, company_id: str, n: int) -> None:
    for i in range(n):
        db.add(Employee(id=_id(), company_id=company_id, full_name=f"E{i}"))
    await db.commit()


async def test_employee_past_the_cap_refused(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    """Filling the cap, then adding one more, is refused."""
    await _add_employees(
        db_session, free_tenant["company_id"], settings.FREE_PLAN_MAX_EMPLOYEES
    )

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "One Too Many"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "plan_limit_exceeded"
    assert resp.json()["detail"]["limit"] == "employees"


async def test_fifth_employee_allowed(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    await _add_employees(db_session, free_tenant["company_id"], 4)

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "Fifth"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 201


async def test_second_location_refused(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    db_session.add(Location(id=_id(), company_id=free_tenant["company_id"],
                            region_id=free_tenant["region_id"],
                            name="L1", timezone="UTC"))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/locations/",
        json={"region_id": free_tenant["region_id"], "name": "L2",
              "timezone": "UTC"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["limit"] == "locations"


async def test_paid_tenant_unaffected(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    og = await db_session.get(OwnershipGroup, free_tenant["og_id"])
    og.stripe_subscription_id = "sub_123"
    await db_session.commit()
    await _add_employees(db_session, free_tenant["company_id"], 10)

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "Eleventh"},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 201


_CSV_HEADER = "full_name,email,role_names,skill_levels,location_names\n"


def _employee_csv(n: int) -> bytes:
    rows = "".join(f"Person {i},p{i}@x.test,,,\n" for i in range(n))
    return (_CSV_HEADER + rows).encode()


async def test_oversized_bulk_upload_refused_whole(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    """An upload larger than the cap: refused, and ZERO rows written."""
    oversized = settings.FREE_PLAN_MAX_EMPLOYEES + 7
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("emps.csv", _employee_csv(oversized), "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "plan_limit_exceeded"
    assert resp.json()["detail"]["attempted"] == oversized

    count = await db_session.scalar(
        select(func.count(Employee.id)).where(
            Employee.company_id == free_tenant["company_id"]
        )
    )
    assert count == 0, "bulk upload must be all-or-nothing"


async def test_bulk_upload_within_limit_succeeds(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("emps.csv",
                        _employee_csv(settings.FREE_PLAN_MAX_EMPLOYEES),
                        "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 200
    assert resp.json()["created"] == settings.FREE_PLAN_MAX_EMPLOYEES


async def test_bulk_upload_counts_existing_rows(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    """Existing rows count toward the cap: (cap - 1) existing + 2 uploaded
    is one past it, so the upload is refused."""
    existing = settings.FREE_PLAN_MAX_EMPLOYEES - 1
    await _add_employees(db_session, free_tenant["company_id"], existing)

    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("emps.csv", _employee_csv(2), "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["current"] == existing


def _current_week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _import_bodies() -> list[tuple[str, dict]]:
    """(path, request body) for each of the four importer endpoints.

    Bodies match each endpoint's real Pydantic schema (not the brief's
    illustrative `{"api_key": ...}`, which doesn't match any of the four
    schemas — see task-6-report.md for details). The 7shifts availabilities
    schema also validates week_start/week_end via a model_validator, so
    dates must be a real, in-range week or FastAPI 422s before the plan
    guard ever runs.
    """
    week_start = _current_week_monday()
    week_end = week_start + timedelta(days=7)
    return [
        ("/api/v1/import/7shifts", {"access_token": "fake-key"}),
        (
            "/api/v1/import/deputy",
            {
                "access_token": "fake-key",
                "deputy_base_url": "https://fake.deputy.com",
            },
        ),
        (
            "/api/v1/import/7shifts/availabilities",
            {
                "access_token": "fake-key",
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
            },
        ),
        (
            "/api/v1/import/deputy/availabilities",
            {
                "access_token": "fake-key",
                "deputy_base_url": "https://fake.deputy.com",
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
            },
        ),
    ]


@pytest.mark.parametrize("path,body", _import_bodies())
async def test_integration_import_refused_on_free(
    client: AsyncClient, free_tenant: dict, path: str, body: dict
):
    resp = await client.post(
        path,
        json=body,
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "integration_import_requires_paid_plan"
