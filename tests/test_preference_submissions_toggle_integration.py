"""Admin toggle for employee preference submissions (#189)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company
from tests.conftest import COMPANY_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

API = "/api/v1"
URL = f"{API}/company/settings/employee-preference-submissions"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_defaults_to_false_for_manager(client: AsyncClient, manager_token: str):
    resp = await client.get(f"{API}/company/", headers=_h(manager_token))
    assert resp.status_code == 200
    assert resp.json()["employee_preference_submissions_enabled"] is False


async def test_defaults_to_false_for_employee(client: AsyncClient, employee_token: str):
    resp = await client.get(f"{API}/company/", headers=_h(employee_token))
    assert resp.status_code == 200
    assert resp.json()["employee_preference_submissions_enabled"] is False


async def test_admin_enables_and_records_audit(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, employee_token: str
):
    resp = await client.put(URL, json={"enabled": True}, headers=_h(manager_token))
    assert resp.status_code == 200
    assert resp.json()["employee_preference_submissions_enabled"] is True

    db_session.expire_all()
    company = await db_session.get(Company, COMPANY_ID)
    assert company.employee_preference_submissions_enabled is True
    assert company.employee_preference_submissions_toggled_at is not None
    assert company.employee_preference_submissions_toggled_by is not None

    # Visible to employees too.
    emp = await client.get(f"{API}/company/", headers=_h(employee_token))
    assert emp.json()["employee_preference_submissions_enabled"] is True


async def test_admin_disables_again(client: AsyncClient, manager_token: str):
    await client.put(URL, json={"enabled": True}, headers=_h(manager_token))
    resp = await client.put(URL, json={"enabled": False}, headers=_h(manager_token))
    assert resp.status_code == 200
    assert resp.json()["employee_preference_submissions_enabled"] is False


async def test_regular_manager_forbidden(client: AsyncClient, regular_token: str):
    resp = await client.put(URL, json={"enabled": True}, headers=_h(regular_token))
    assert resp.status_code == 403


async def test_employee_forbidden(client: AsyncClient, employee_token: str):
    resp = await client.put(URL, json={"enabled": True}, headers=_h(employee_token))
    assert resp.status_code == 403
