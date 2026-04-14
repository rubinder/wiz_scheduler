"""Tests for /api/v1/regions endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import COMPANY_ID, REGION_ID

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /regions
# ---------------------------------------------------------------------------


async def test_list_regions(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.get(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "East Coast"


async def test_list_regions_empty(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.get(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /regions
# ---------------------------------------------------------------------------


async def test_create_region(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.post(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "West Coast"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "West Coast"
    assert data["company_id"] == COMPANY_ID


async def test_create_region_with_geo_bounds(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    bounds = {"lat_min": 30.0, "lat_max": 40.0, "lng_min": -90.0, "lng_max": -80.0}
    resp = await client.post(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Southern", "geo_bounds": bounds},
    )
    assert resp.status_code == 201
    assert resp.json()["geo_bounds"] == bounds


async def test_create_region_requires_manager(
    client: AsyncClient,
    employee_token: str,
    seed_company,
):
    resp = await client.post(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"name": "Nope"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /regions/{id}
# ---------------------------------------------------------------------------


async def test_update_region(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.put(
        f"/api/v1/regions/{REGION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Northeast"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Northeast"


async def test_update_region_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.put(
        "/api/v1/regions/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /regions/{id}
# ---------------------------------------------------------------------------


async def test_delete_region(
    client: AsyncClient,
    manager_token: str,
    seed_region,
):
    resp = await client.delete(
        f"/api/v1/regions/{REGION_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 204

    resp = await client.get(
        "/api/v1/regions/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.json() == []


async def test_delete_region_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.delete(
        "/api/v1/regions/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 404
