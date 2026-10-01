"""Pay rate and overtime fields are paid-plan gated at the write layer;
hire_date/seniority_rank are free (#134)."""
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region, User
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


async def _tenant(db: AsyncSession, *, paid: bool) -> SimpleNamespace:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G", stripe_subscription_id="sub_x" if paid else None))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}", ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, employee_id, manager_id = _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id, name="Main", timezone="America/New_York"))
    db.add(User(id=manager_id, company_id=company_id, email=f"{manager_id}@example.com", hashed_password="x", full_name="Mo Manager", user_role="manager"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="Dana Okafor", location_ids=[location_id]))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, employee_id=employee_id,
        manager_headers={"Authorization": f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
    )


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=False)


async def test_free_plan_cannot_set_employee_pay_rate(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"pay_rate": 20.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_employee_pay_rate(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{paid.employee_id}",
        json={"pay_rate": 20.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pay_rate"] == 20.0


async def test_free_plan_can_set_employee_hire_date_and_seniority_rank(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"hire_date": "2022-03-01", "seniority_rank": 1}, headers=free.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hire_date"] == "2022-03-01"
    assert body["seniority_rank"] == 1


async def test_free_plan_cannot_set_company_overtime_threshold(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        "/api/v1/company/",
        json={"overtime_threshold_hours": 35.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_company_overtime_threshold(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        "/api/v1/company/",
        json={"overtime_threshold_hours": 35.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["overtime_threshold_hours"] == 35.0


async def test_free_plan_cannot_set_location_overtime_threshold(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/locations/{free.location_id}",
        json={"overtime_threshold_hours": 30.0}, headers=free.manager_headers,
    )
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "cost_aware_scheduling_requires_paid_plan"


async def test_paid_plan_can_set_location_overtime_threshold(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/locations/{paid.location_id}",
        json={"overtime_threshold_hours": 30.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["overtime_threshold_hours"] == 30.0


async def test_seniority_rank_zero_rejected(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"seniority_rank": 0}, headers=free.manager_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_negative_pay_rate_rejected(client: AsyncClient, paid: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/employees/{paid.employee_id}",
        json={"pay_rate": -5.0}, headers=paid.manager_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_free_plan_employee_update_omitting_pay_rate_succeeds(client: AsyncClient, free: SimpleNamespace):
    """The frontend omits (never nulls) gated fields from update payloads on
    a free plan so unrelated edits don't trip the paid-gate. A body that
    genuinely lacks the `pay_rate` key must succeed regardless of plan."""
    resp = await client.put(
        f"/api/v1/employees/{free.employee_id}",
        json={"full_name": "New Name"}, headers=free.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "New Name"


async def test_free_plan_company_update_omitting_overtime_fields_succeeds(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        "/api/v1/company/",
        json={"name": "New Co Name"}, headers=free.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Co Name"


async def test_free_plan_location_update_omitting_overtime_fields_succeeds(client: AsyncClient, free: SimpleNamespace):
    resp = await client.put(
        f"/api/v1/locations/{free.location_id}",
        json={"timezone": "America/Chicago"}, headers=free.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "America/Chicago"
