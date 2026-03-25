"""Tests for /api/v1/shift-templates endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import LOCATION_ID, ROLE_FLOOR_ID, ROLE_LEAD_ID, SHIFT_TEMPLATE_ID

pytestmark = pytest.mark.asyncio


async def test_create_shift_template(
    client: AsyncClient,
    manager_token: str,
    seed_location,
    seed_roles,
):
    weekly = [
        {
            "day": "Tuesday",
            "role_name": "Floor Associate",
            "role_id": str(ROLE_FLOOR_ID),
            "headcount": 2,
            "start_time": "10:00",
            "end_time": "18:00",
        }
    ]
    resp = await client.post(
        "/api/v1/shift-templates/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": str(LOCATION_ID),
            "name": "Tuesday Template",
            "weekly_schedule": weekly,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Tuesday Template"
    assert len(data["weekly_schedule"]) == 1


async def test_list_shift_templates(
    client: AsyncClient,
    manager_token: str,
    seed_shift_template,
):
    resp = await client.get(
        "/api/v1/shift-templates/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["name"] == "Weekday Standard"


async def test_update_shift_template(
    client: AsyncClient,
    manager_token: str,
    seed_shift_template,
):
    resp = await client.put(
        f"/api/v1/shift-templates/{SHIFT_TEMPLATE_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Updated Template"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Template"


async def test_delete_shift_template(
    client: AsyncClient,
    manager_token: str,
    seed_shift_template,
):
    resp = await client.delete(
        f"/api/v1/shift-templates/{SHIFT_TEMPLATE_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 204

    # Verify deletion
    resp = await client.get(
        "/api/v1/shift-templates/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert len(resp.json()) == 0


async def test_weekly_schedule_json_stored_correctly(
    client: AsyncClient,
    manager_token: str,
    seed_shift_template,
):
    resp = await client.get(
        "/api/v1/shift-templates/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    data = resp.json()
    template = data[0]
    schedule = template["weekly_schedule"]
    assert isinstance(schedule, list)
    assert schedule[0]["day"] == "Monday"
    assert schedule[0]["role_name"] == "Floor Associate"
    assert schedule[0]["headcount"] == 2
    assert schedule[0]["start_time"] == "09:00"
    assert schedule[0]["end_time"] == "17:00"
