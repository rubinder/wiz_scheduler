"""Tests for /api/v1/employees endpoints."""

import io

import pytest
from httpx import AsyncClient

from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

pytestmark = pytest.mark.asyncio


async def test_create_employee(client: AsyncClient, manager_token: str, seed_employees):
    resp = await client.post(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"full_name": "Charlie Brown", "email": "charlie@test.com"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "Charlie Brown"
    assert data["email"] == "charlie@test.com"


async def test_list_employees(client: AsyncClient, manager_token: str, seed_employees):
    resp = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {e["full_name"] for e in data}
    assert "Alice Johnson" in names
    assert "Bob Smith" in names


async def test_update_employee(client: AsyncClient, manager_token: str, seed_employees):
    resp = await client.put(
        f"/api/v1/employees/{EMPLOYEE1_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"full_name": "Alice Updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Alice Updated"


async def test_delete_employee(client: AsyncClient, manager_token: str, seed_employees):
    resp = await client.delete(
        f"/api/v1/employees/{EMPLOYEE1_ID}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 204

    # Verify deletion
    resp = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert len(resp.json()) == 1


async def test_bulk_csv_upload(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
    seed_location,
    seed_roles,
):
    csv_content = (
        "full_name,email,role_names,skill_levels,location_names\n"
        "Dave Wilson,dave@test.com,Floor Associate,2,Downtown Store\n"
        "Eve Garcia,eve@test.com,Floor Associate|Team Lead,3|2,Downtown Store\n"
    )
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("employees.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 2
    assert data["skipped"] == 0


async def test_company_isolation(client: AsyncClient, db_session, seed_employees, manager_token: str):
    """A second company's manager should not see the first company's employees."""
    from passlib.context import CryptContext

    from backend.models import Company, User

    # Create 2nd company and manager
    company2_id = _id()
    company2 = Company(id=company2_id, name="Other Corp", slug="other123")
    db_session.add(company2)
    await db_session.flush()

    pwd = CryptContext(schemes=["bcrypt"]).hash("pass2")
    user2_id = _id()
    user2 = User(
        id=user2_id,
        company_id=company2_id,
        email="mgr2@other.com",
        hashed_password=pwd,
        full_name="Other Manager",
        user_role="manager",
    )
    db_session.add(user2)
    await db_session.commit()

    # Login as 2nd manager
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "mgr2@other.com", "password": "pass2"},
    )
    token2 = login_resp.json()["access_token"]

    # List employees — should be empty for company 2
    resp = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 0


# ---------------------------------------------------------------------------
# /employees/bulk-upload size + row caps (#46)
# ---------------------------------------------------------------------------


async def test_bulk_upload_rejects_oversize_body(
    client: AsyncClient, manager_token: str, seed_roles, seed_location,
):
    """A body larger than 5 MB returns 413 before any parsing happens."""
    # Build a CSV that's just over 5 MB. Header + N narrow rows of a fixed
    # length is the cheapest way to land deterministically over the cap.
    header = "full_name,email,role_names,skill_levels,location_names\n"
    row = "X" * 200 + "\n"
    target_bytes = 5 * 1024 * 1024 + 1024  # 5MB + 1KB
    body = header + row * ((target_bytes // len(row)) + 1)
    assert len(body) > 5 * 1024 * 1024

    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("big.csv", body, "text/csv")},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"].lower()


async def test_bulk_upload_accepts_under_size_cap(
    client: AsyncClient, manager_token: str, seed_roles, seed_location,
):
    """An under-5MB CSV still processes normally."""
    header = "full_name,email,role_names,skill_levels,location_names\n"
    rows = "\n".join(
        f"User{i},user{i}@x.com,Floor Associate,2,Downtown Store"
        for i in range(50)
    )
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("ok.csv", header + rows, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 50


async def test_bulk_upload_rejects_over_10k_rows(
    client: AsyncClient, manager_token: str, seed_roles, seed_location,
):
    """A small-bytes CSV with >10,000 rows returns 400."""
    header = "full_name,email,role_names,skill_levels,location_names\n"
    # ~30 bytes/row × 10,001 ≈ 300 KB — well under the 5MB body cap.
    body = header + "\n".join(
        f"u{i},u{i}@x.com,Floor Associate,2,Downtown Store"
        for i in range(10_001)
    )
    assert len(body) < 5 * 1024 * 1024

    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {manager_token}"},
        files={"file": ("many.csv", body, "text/csv")},
    )
    assert resp.status_code == 400
    assert "10000" in resp.json()["detail"] or "10,000" in resp.json()["detail"]
