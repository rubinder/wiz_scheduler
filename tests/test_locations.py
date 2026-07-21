"""Tests for /api/v1/locations endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import COMPANY_ID, LOCATION_ID, REGION_ID

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /locations
# ---------------------------------------------------------------------------


async def test_list_locations(
    client: AsyncClient,
    manager_token: str,
    seed_location,
):
    resp = await client.get(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Downtown Store"
    assert data[0]["timezone"] == "America/New_York"


async def test_list_locations_empty(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.get(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /locations
# ---------------------------------------------------------------------------


async def test_create_location(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "region_id": REGION_ID,
            "name": "Uptown Mall",
            "timezone": "America/Chicago",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Uptown Mall"
    assert data["region_id"] == REGION_ID
    assert data["company_id"] == COMPANY_ID


async def test_create_location_with_address(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "region_id": REGION_ID,
            "name": "Suburb Branch",
            "timezone": "America/Denver",
            "address": "123 Main St",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["address"] == "123 Main St"


async def test_create_location_with_min_rest_hours(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "region_id": REGION_ID,
            "name": "NYC Fast Food",
            "timezone": "America/New_York",
            "min_rest_hours": 11,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["min_rest_hours"] == 11


async def test_create_location_defaults_min_rest_hours_null(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "region_id": REGION_ID,
            "name": "No Rule Store",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["min_rest_hours"] is None


async def test_create_location_requires_manager(
    client: AsyncClient,
    employee_token: str,
    seed_region,
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={
            "region_id": REGION_ID,
            "name": "Nope",
            "timezone": "UTC",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /locations/{id}
# ---------------------------------------------------------------------------


async def test_update_location(
    client: AsyncClient,
    manager_token: str,
    seed_location,
):
    resp = await client.put(
        f"/api/v1/locations/{LOCATION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Renamed Store"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Store"


async def test_update_location_sets_and_clears_min_rest_hours(
    client: AsyncClient,
    manager_token: str,
    seed_location,
):
    # Set the rule
    resp = await client.put(
        f"/api/v1/locations/{LOCATION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"min_rest_hours": 11},
    )
    assert resp.status_code == 200
    assert resp.json()["min_rest_hours"] == 11

    # Clear it back to null (explicit null must clear, not be ignored)
    resp = await client.put(
        f"/api/v1/locations/{LOCATION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"min_rest_hours": None},
    )
    assert resp.status_code == 200
    assert resp.json()["min_rest_hours"] is None


async def test_update_location_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.put(
        "/api/v1/locations/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /locations/{id}
# ---------------------------------------------------------------------------


async def test_delete_location(
    client: AsyncClient,
    manager_token: str,
    seed_location,
):
    resp = await client.delete(
        f"/api/v1/locations/{LOCATION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 204

    # Verify gone
    resp = await client.get(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.json() == []


async def test_delete_location_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.delete(
        "/api/v1/locations/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /locations/bulk-upload (CSV)
# ---------------------------------------------------------------------------


async def test_bulk_upload_csv(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    csv_content = (
        "name,region_name,address,timezone\n"
        "Store A,East Coast,100 Oak St,America/New_York\n"
        "Store B,East Coast,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["skipped"] == 0


async def test_bulk_upload_skips_duplicate(
    client: AsyncClient,
    manager_token: str,
    seed_location,
):
    csv_content = (
        "name,region_name,address,timezone\n"
        "Downtown Store,East Coast,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["skipped"] == 1


async def test_bulk_upload_skips_missing_name(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    csv_content = (
        "name,region_name,address,timezone\n"
        ",East Coast,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["skipped"] == 1


async def test_bulk_upload_unknown_region(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    csv_content = (
        "name,region_name,address,timezone\n"
        "Far Away,West Coast,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["skipped"] == 1
    assert "not found" in data["errors"][0].lower()


async def test_bulk_upload_json(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    import json

    payload = [
        {"name": "JSON Store", "region_name": "East Coast", "timezone": "UTC"},
    ]
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("locations.json", json.dumps(payload), "application/json")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
