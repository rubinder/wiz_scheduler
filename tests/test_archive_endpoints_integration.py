"""Manager delete archives; restore brings it back (#126)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, OwnershipGroup, Region, Role, Shift, ShiftSchedule, User,
)
from tests.conftest import COMPANY_ID, _id, _make_token

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
    # An employee leaves the plan count at once; a location archived this month
    # keeps holding its slot until the month ends.
    assert await count_locations_for_group(db_session, og_id) == 2

    locs[0].archived_at = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0,
                                                              microsecond=0) - timedelta(seconds=1)
    await db_session.commit()
    assert await count_locations_for_group(db_session, og_id) == 1


async def _free_tenant(db_session, n_locations):
    og_id, company_id, region_id, user_id = _id(), _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Free Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Free Co", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    db_session.add(User(id=user_id, company_id=company_id, email=f"{_id()}@free.test",
                        hashed_password="x", full_name="Mgr", user_role="manager"))
    await db_session.flush()
    locs = [Location(id=_id(), company_id=company_id, region_id=region_id, name=f"L{i}", timezone="UTC")
            for i in range(n_locations)]
    db_session.add_all(locs)
    await db_session.commit()
    return {"token": _make_token(user_id, company_id, "manager"), "region_id": region_id, "locs": locs}


@pytest.mark.asyncio
async def test_archive_then_create_location_is_refused_on_free_plan(client, db_session):
    from backend.config import settings
    t = await _free_tenant(db_session, settings.FREE_PLAN_MAX_LOCATIONS)
    resp = await client.delete(f"/api/v1/locations/{t['locs'][0].id}", headers=_h(t["token"]))
    assert resp.status_code == 204
    resp = await client.post("/api/v1/locations/", headers=_h(t["token"]),
                             json={"region_id": t["region_id"], "name": "fresh", "timezone": "UTC"})
    assert resp.status_code == 402
    assert resp.json()["detail"]["limit"] == "locations"


@pytest.mark.asyncio
async def test_restore_location_archived_this_month_not_double_counted(client, db_session):
    from backend.config import settings
    t = await _free_tenant(db_session, settings.FREE_PLAN_MAX_LOCATIONS)
    loc = t["locs"][0]
    await client.delete(f"/api/v1/locations/{loc.id}", headers=_h(t["token"]))
    resp = await client.post(f"/api/v1/locations/{loc.id}/restore", headers=_h(t["token"]))
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None


@pytest.mark.asyncio
async def test_restore_location_archived_last_month_needs_free_slot(client, db_session):
    from backend.config import settings
    t = await _free_tenant(db_session, settings.FREE_PLAN_MAX_LOCATIONS)
    loc = t["locs"][0]
    loc.archived_at = datetime.now(timezone.utc) - timedelta(days=62)
    # A replacement fills the slot the old archived row no longer holds.
    comp_id = loc.company_id
    db_session.add(Location(id=_id(), company_id=comp_id, region_id=t["region_id"], name="Repl", timezone="UTC"))
    await db_session.commit()
    resp = await client.post(f"/api/v1/locations/{loc.id}/restore", headers=_h(t["token"]))
    assert resp.status_code == 402


async def _group_with_employees(db_session):
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Billable Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Billable Co", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    emps = [Employee(id=_id(), company_id=company_id, full_name=f"E{i}") for i in range(3)]
    db_session.add_all(emps)
    await db_session.commit()
    return og_id, emps


@pytest.mark.asyncio
async def test_billable_count_includes_archived_since_period_start(db_session):
    from backend.services.billing import count_billable_employees_for_group
    og_id, emps = await _group_with_employees(db_session)
    period_start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    emps[0].archived_at = period_start + timedelta(days=5)   # archived inside the period
    emps[1].archived_at = period_start - timedelta(days=5)   # archived before it
    await db_session.commit()
    # emps[2] active + emps[0] archived after period_start; emps[1] excluded.
    assert await count_billable_employees_for_group(db_session, og_id, period_start) == 2


@pytest.mark.asyncio
async def test_archive_before_billing_run_does_not_lower_overage(db_session, monkeypatch):
    from backend.config import settings
    from backend.services.billing import compute_monthly_overage
    monkeypatch.setattr(settings, "INCLUDED_EMPLOYEES", 0)
    monkeypatch.setattr(settings, "EMPLOYEE_BLOCK_SIZE", 1)
    monkeypatch.setattr(settings, "EMPLOYEE_COST_PER_BLOCK", 1.0)
    og_id, emps = await _group_with_employees(db_session)
    now = datetime.now(timezone.utc)
    period = f"{now.year:04d}-{now.month:02d}"
    before = await compute_monthly_overage(db_session, og_id, "invoice_item_employees", period)
    assert before == 3.0
    for e in emps:
        e.archived_at = now
    await db_session.commit()
    after = await compute_monthly_overage(db_session, og_id, "invoice_item_employees", period)
    assert after == before
