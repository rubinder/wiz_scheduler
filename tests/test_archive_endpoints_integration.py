"""Manager delete archives; restore brings it back (#126)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, OwnershipGroup, Region, Role, Shift, ShiftSchedule,
)
from tests.conftest import COMPANY_ID, _id

pytestmark = pytest.mark.integration


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_delete_employee_with_check_in_archives_instead_of_500(
        client: AsyncClient, db_session, manager_token, seed_location, seed_roles, seed_employees):
    emp, role, loc = seed_employees[0], seed_roles[0], seed_location
    start = datetime.now(timezone.utc) - timedelta(days=3)
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                          week_start_date=start.date() - timedelta(days=start.weekday()), status="approved")
    db_session.add(sched)
    await db_session.flush()
    shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc.id,
                  employee_id=emp.id, role_id=role.id, role_name=role.name, date=start.date(),
                  start_time=start, end_time=start + timedelta(hours=8))
    db_session.add(shift)
    await db_session.flush()
    db_session.add(EmployeeCheckIn(id=_id(), company_id=COMPANY_ID, location_id=loc.id, employee_id=emp.id,
                                   shift_id=shift.id, checked_in_at=start, local_date=start.date(),
                                   counter=0, status="matched"))
    await db_session.commit()

    resp = await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    assert resp.status_code == 204

    listed = (await client.get("/api/v1/employees/", headers=_h(manager_token))).json()
    assert emp.id not in [e["id"] for e in listed]
    with_archived = (await client.get("/api/v1/employees/?include_archived=true",
                                      headers=_h(manager_token))).json()
    row = next(e for e in with_archived if e["id"] == emp.id)
    assert row["archived_at"] is not None


@pytest.mark.asyncio
async def test_restore_employee(client, db_session, manager_token, seed_employees):
    emp = seed_employees[0]
    await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None


@pytest.mark.asyncio
async def test_restore_erased_employee_409(client, db_session, manager_token, seed_employees):
    emp = seed_employees[0]
    now = datetime.now(timezone.utc)
    emp.archived_at, emp.erased_at = now, now
    await db_session.commit()
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "employee_erased"


@pytest.mark.asyncio
async def test_restore_employee_respects_free_limit(client, db_session, manager_token, seed_employees,
                                                    monkeypatch):
    emp = seed_employees[0]
    await client.delete(f"/api/v1/employees/{emp.id}", headers=_h(manager_token))
    import backend.routers.employees as employees_router

    async def at_limit(*args, **kwargs):
        raise HTTPException(status_code=402, detail={"code": "plan_limit"})
    monkeypatch.setattr(employees_router, "assert_can_add", at_limit)
    resp = await client.post(f"/api/v1/employees/{emp.id}/restore", headers=_h(manager_token))
    assert resp.status_code == 402
    row = (await db_session.execute(select(Employee).where(Employee.id == emp.id)
                                    .execution_options(populate_existing=True))).scalar_one()
    assert row.archived_at is not None


@pytest.mark.asyncio
async def test_delete_location_and_role_archive(client, db_session, manager_token, seed_location, seed_roles):
    assert (await client.delete(f"/api/v1/locations/{seed_location.id}", headers=_h(manager_token))).status_code == 204
    assert (await client.delete(f"/api/v1/roles/{seed_roles[0].id}", headers=_h(manager_token))).status_code == 204
    locs = (await client.get("/api/v1/locations/", headers=_h(manager_token))).json()
    roles = (await client.get("/api/v1/roles/", headers=_h(manager_token))).json()
    assert seed_location.id not in [l["id"] for l in locs]
    assert seed_roles[0].id not in [r["id"] for r in roles]
    assert (await client.post(f"/api/v1/locations/{seed_location.id}/restore",
                              headers=_h(manager_token))).status_code == 200
    assert (await client.post(f"/api/v1/roles/{seed_roles[0].id}/restore",
                              headers=_h(manager_token))).status_code == 200


@pytest.mark.asyncio
async def test_bulk_upload_reports_archived_match(client, db_session, manager_token, seed_roles):
    role = seed_roles[0]
    await client.delete(f"/api/v1/roles/{role.id}", headers=_h(manager_token))
    payload = json.dumps([{"name": role.name}])
    resp = await client.post("/api/v1/roles/bulk-upload", headers=_h(manager_token),
                             files={"file": ("roles.json", payload, "application/json")})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 0
    assert body["errors"] == [f"{role.name}: exists (archived) — restore it instead"]


@pytest.mark.asyncio
async def test_archived_employee_not_counted_for_plan(db_session):
    from backend.services.billing import count_employees_for_group, count_locations_for_group
    og_id, company_id, region_id = _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Count Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Count Co", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    emps = [Employee(id=_id(), company_id=company_id, full_name=f"E{i}") for i in range(3)]
    locs = [Location(id=_id(), company_id=company_id, region_id=region_id, name=f"L{i}", timezone="UTC")
            for i in range(2)]
    db_session.add_all(emps + locs)
    await db_session.commit()
    assert await count_employees_for_group(db_session, og_id) == 3
    assert await count_locations_for_group(db_session, og_id) == 2

    now = datetime.now(timezone.utc)
    emps[0].archived_at = now
    locs[0].archived_at = now
    await db_session.commit()
    assert await count_employees_for_group(db_session, og_id) == 2
    assert await count_locations_for_group(db_session, og_id) == 1
