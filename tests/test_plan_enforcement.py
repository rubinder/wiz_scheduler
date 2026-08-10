"""Free-plan enforcement across employee/location write paths."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region, User
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


async def test_sixth_employee_refused(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    await _add_employees(db_session, free_tenant["company_id"], 5)

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "Sixth"},
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
    """12 rows against an empty free OG: refused, and ZERO rows written."""
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("emps.csv", _employee_csv(12), "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "plan_limit_exceeded"
    assert resp.json()["detail"]["attempted"] == 12

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
        files={"file": ("emps.csv", _employee_csv(5), "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 200
    assert resp.json()["created"] == 5


async def test_bulk_upload_counts_existing_rows(
    client: AsyncClient, db_session: AsyncSession, free_tenant: dict
):
    """3 existing + 3 uploaded == 6 > 5, so refused."""
    await _add_employees(db_session, free_tenant["company_id"], 3)

    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("emps.csv", _employee_csv(3), "text/csv")},
        headers={"Authorization": f"Bearer {free_tenant['token']}"},
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["current"] == 3
