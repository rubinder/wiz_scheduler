"""employees.timezone: NULL by default, set only by a manager, validated."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee
from tests.conftest import EMPLOYEE1_ID, LOCATION_ID

BASE = "/api/v1/employees"


async def test_a_new_employee_has_no_timezone(
    client: AsyncClient, manager_token: str, seed_location,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"full_name": "New Person", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone"] is None


async def test_a_manager_sets_and_clears_it(
    client: AsyncClient, manager_token: str, seed_employees, db_session: AsyncSession,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"timezone": "America/Los_Angeles"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "America/Los_Angeles"

    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"timezone": None})
    assert resp.status_code == 200
    db_session.expire_all()
    emp = (await db_session.execute(
        select(Employee).where(Employee.id == EMPLOYEE1_ID)
    )).scalar_one()
    assert emp.timezone is None


async def test_an_update_without_the_field_leaves_it_alone(
    client: AsyncClient, manager_token: str, seed_employees,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                     json={"timezone": "America/Chicago"})
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"full_name": "Renamed"})
    assert resp.json()["timezone"] == "America/Chicago"


async def test_changing_locations_never_touches_it(
    client: AsyncClient, manager_token: str, seed_employees,
):
    headers = {"Authorization": f"Bearer {manager_token}"}
    resp = await client.put(f"{BASE}/{EMPLOYEE1_ID}", headers=headers,
                            json={"location_ids": [LOCATION_ID]})
    assert resp.json()["timezone"] is None


async def test_an_invalid_zone_is_rejected(
    client: AsyncClient, manager_token: str, seed_employees,
):
    resp = await client.put(
        f"{BASE}/{EMPLOYEE1_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"timezone": "Pacific Time"},
    )
    assert resp.status_code == 422
