"""Tests for the /api/v1/auth endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_register_creates_user_and_company(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "secret123",
            "full_name": "New User",
            "company_name": "NewCo",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_correct_credentials(client: AsyncClient, seed_manager):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "testpass"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


async def test_login_wrong_password(client: AsyncClient, seed_manager):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


async def test_me_with_valid_token(client: AsyncClient, manager_token: str):
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "manager@test.com"
    assert data["user_role"] == "manager"


async def test_me_without_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


async def test_manager_route_with_employee_token(
    client: AsyncClient,
    employee_token: str,
    seed_employees,
):
    """Employee token should be rejected by manager-only endpoints."""
    resp = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    assert resp.status_code == 403
