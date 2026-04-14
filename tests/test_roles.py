"""Tests for /api/v1/roles endpoints."""

import json

import pytest
from httpx import AsyncClient

from tests.conftest import COMPANY_ID, ROLE_FLOOR_ID, ROLE_LEAD_ID

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /roles
# ---------------------------------------------------------------------------


async def test_list_roles(
    client: AsyncClient,
    manager_token: str,
    seed_roles,
):
    resp = await client.get(
        "/api/v1/roles/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {r["name"] for r in data}
    assert "Floor Associate" in names
    assert "Team Lead" in names


async def test_list_roles_empty(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.get(
        "/api/v1/roles/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /roles
# ---------------------------------------------------------------------------


async def test_create_role(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.post(
        "/api/v1/roles/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Cashier", "description": "Handle payments"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Cashier"
    assert data["description"] == "Handle payments"
    assert data["company_id"] == COMPANY_ID


async def test_create_role_requires_manager(
    client: AsyncClient,
    employee_token: str,
    seed_company,
):
    resp = await client.post(
        "/api/v1/roles/",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"name": "Nope"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /roles/{id}
# ---------------------------------------------------------------------------


async def test_update_role(
    client: AsyncClient,
    manager_token: str,
    seed_roles,
):
    resp = await client.put(
        f"/api/v1/roles/{ROLE_FLOOR_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Floor Worker"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Floor Worker"


async def test_update_role_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.put(
        "/api/v1/roles/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /roles/{id}
# ---------------------------------------------------------------------------


async def test_delete_role(
    client: AsyncClient,
    manager_token: str,
    seed_roles,
):
    resp = await client.delete(
        f"/api/v1/roles/{ROLE_FLOOR_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 204

    resp = await client.get(
        "/api/v1/roles/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert len(resp.json()) == 1


async def test_delete_role_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    resp = await client.delete(
        "/api/v1/roles/FAKE",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /roles/bulk-upload
# ---------------------------------------------------------------------------


async def test_bulk_upload_csv(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    csv_content = (
        "name,description\n"
        "Greeter,Welcome customers\n"
        "Stocker,Restock shelves\n"
    )
    resp = await client.post(
        "/api/v1/roles/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("roles.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["skipped"] == 0


async def test_bulk_upload_skips_duplicate(
    client: AsyncClient,
    manager_token: str,
    seed_roles,
):
    csv_content = (
        "name,description\n"
        "Floor Associate,duplicate\n"
    )
    resp = await client.post(
        "/api/v1/roles/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("roles.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["skipped"] == 1


async def test_bulk_upload_json(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    payload = [{"name": "Driver", "description": "Deliveries"}]
    resp = await client.post(
        "/api/v1/roles/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("roles.json", json.dumps(payload), "application/json")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


async def test_bulk_upload_skips_empty_name(
    client: AsyncClient,
    manager_token: str,
    seed_company,
):
    csv_content = (
        "name,description\n"
        ",Missing name\n"
    )
    resp = await client.post(
        "/api/v1/roles/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("roles.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0
    assert data["skipped"] == 1
