"""The check-in endpoints.

Paid gating is asserted on the EMPLOYEE endpoint as well as the manager one:
what gates the feature is the tenant's plan, not the caller's role.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, Location, Region, Shift, ShiftSchedule, User,
)
from backend.models.ownership_group import OwnershipGroup
from backend.services.check_in import issue_token
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


async def _tenant(db: AsyncSession, *, paid: bool) -> dict:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(
        id=og_id, name="G",
        stripe_subscription_id="sub_x" if paid else None,
    ))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}",
                   ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location = Location(id=_id(), company_id=company_id, region_id=region_id,
                        name="Here", timezone="America/New_York")
    db.add(location)
    manager_id, employee_user_id, employee_id = _id(), _id(), _id()
    db.add(User(id=manager_id, company_id=company_id,
                email=f"{manager_id}@example.com", hashed_password="x",
                full_name="M", user_role="manager"))
    db.add(User(id=employee_user_id, company_id=company_id,
                email=f"{employee_user_id}@example.com", hashed_password="x",
                full_name="E", user_role="employee"))
    db.add(Employee(id=employee_id, company_id=company_id, full_name="E",
                    email=f"{employee_id}@example.com",
                    location_ids=[location.id], user_id=employee_user_id))
    await db.commit()
    return {
        "company_id": company_id,
        "slug": f"slug-{company_id}",
        "location": location,
        "employee_id": employee_id,
        "manager_headers": {
            "Authorization":
                f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
        "employee_headers": {
            "Authorization":
                f"Bearer {_make_token(employee_user_id, company_id, 'employee')}"},
    }


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> dict:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> dict:
    return await _tenant(db_session, paid=False)


# --- QR endpoint ------------------------------------------------------------

async def test_manager_gets_a_qr_svg(client: AsyncClient, paid: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["manager_headers"],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counter"] == 0
    assert body["svg"].lstrip().startswith("<?xml") or "<svg" in body["svg"]


async def test_the_qr_response_never_carries_the_secret(
    client: AsyncClient, paid: dict
):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["manager_headers"],
    )
    assert "test-secret-value" not in resp.text


async def test_an_employee_cannot_read_the_qr(client: AsyncClient, paid: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={paid['location'].id}",
        headers=paid["employee_headers"],
    )
    assert resp.status_code == 403


async def test_qr_is_paid_only(client: AsyncClient, free: dict):
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={free['location'].id}",
        headers=free["manager_headers"],
    )
    assert resp.status_code == 402


async def test_qr_refuses_another_tenants_location(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    other = await _tenant(db_session, paid=True)
    resp = await client.get(
        f"/api/v1/check-ins/qr?location_id={other['location'].id}",
        headers=paid["manager_headers"],
    )
    assert resp.status_code == 404


# --- check-in endpoint ------------------------------------------------------

async def test_employee_checks_in(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])

    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": token, "location_id": paid["location"].id},
        headers=paid["employee_headers"],
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "no_shift"


async def test_a_spent_code_returns_409_not_500(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    body = {"token": token, "location_id": paid["location"].id}
    await client.post("/api/v1/check-ins", json=body,
                      headers=paid["employee_headers"])

    resp = await client.post("/api/v1/check-ins", json=body,
                             headers=paid["employee_headers"])

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] in {"invalid_token", "code_already_used"}


async def test_check_in_is_paid_only(
    client: AsyncClient, db_session: AsyncSession, free: dict
):
    token, _ = await issue_token(db_session, free["slug"], free["location"])
    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": token, "location_id": free["location"].id},
        headers=free["employee_headers"],
    )
    assert resp.status_code == 402


async def test_check_in_requires_authentication(client: AsyncClient, paid: dict):
    resp = await client.post(
        "/api/v1/check-ins",
        json={"token": "anything", "location_id": paid["location"].id},
    )
    assert resp.status_code in (401, 403)


# --- report -----------------------------------------------------------------

async def test_report_returns_rows(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    await client.post("/api/v1/check-ins",
                      json={"token": token, "location_id": paid["location"].id},
                      headers=paid["employee_headers"])

    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid["manager_headers"])

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["rows"]) == 1


async def test_report_filters_by_employee(
    client: AsyncClient, db_session: AsyncSession, paid: dict
):
    token, _ = await issue_token(db_session, paid["slug"], paid["location"])
    await client.post("/api/v1/check-ins",
                      json={"token": token, "location_id": paid["location"].id},
                      headers=paid["employee_headers"])

    resp = await client.get(
        f"/api/v1/check-ins/report?employee_id={_id()}",
        headers=paid["manager_headers"],
    )
    assert resp.json()["rows"] == []


async def test_report_is_manager_only(client: AsyncClient, paid: dict):
    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid["employee_headers"])
    assert resp.status_code == 403
