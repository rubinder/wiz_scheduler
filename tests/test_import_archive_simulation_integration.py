# tests/test_import_archive_simulation_integration.py
"""End-to-end 7shifts and Deputy re-imports against a fake upstream (#126).

A re-import that no longer returns an employee, location or role must archive
it -- never delete it -- so past approved shifts, check-ins and pay records
survive, and the employee's future shifts become open shifts (#174).
"""

import copy
from datetime import date, datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, OwnershipGroup, PayrollExport,
    Region, Role, Shift, ShiftSchedule, SpecialHoursDay, TimeEntry, User,
)
from backend.tenant import tenant_scope_disabled
from tests.conftest import _id, _make_token
from tests.fake_upstream import install_fake_upstream

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)
PAST_DAY = (NOW - timedelta(days=21)).date()
FUTURE_DAY = (NOW + timedelta(days=7)).date()

V2 = "/v2"
NO_MORE = {"cursor": {"next": None}}
SEVEN_SHIFTS = {
    f"{V2}/companies": {"data": [{"id": 100, "name": "Acme"}]},
    f"{V2}/company/100/locations": {"data": [
        {"id": 11, "name": "Downtown", "timezone": "America/New_York"},
        {"id": 12, "name": "Uptown", "timezone": "America/Chicago"},
    ], "meta": NO_MORE},
    f"{V2}/company/100/departments": {"data": [], "meta": NO_MORE},
    f"{V2}/company/100/roles": {"data": [{"id": 21, "name": "Cook"}, {"id": 22, "name": "Server"}]},
    f"{V2}/company/100/users": {"data": [
        {"id": 31, "user_id": 9031, "first_name": "Ann", "last_name": "A", "email": "ann@x.test", "active": True},
        {"id": 32, "user_id": 9032, "first_name": "Ben", "last_name": "B", "email": "ben@x.test", "active": True},
    ], "meta": NO_MORE},
    f"{V2}/company/100/users/31/assignments": {"data": {
        "locations": [{"id": 11}, {"id": 12}], "roles": [{"id": 21, "skill_level": 2}, {"id": 22, "skill_level": 2}]}},
    f"{V2}/company/100/users/32/assignments": {"data": {
        "locations": [{"id": 12}], "roles": [{"id": 22, "skill_level": 1}]}},
}

DEPUTY = {
    "/api/v1/resource/Company": [
        {"Id": 1, "CompanyName": "Downtown", "Active": True, "Timezone": "America/New_York"},
        {"Id": 2, "CompanyName": "Uptown", "Active": True, "Timezone": "America/Chicago"},
    ],
    "/api/v1/resource/OperationalUnit": [
        {"Id": 51, "OperationalUnitName": "Cook", "Company": 1, "Active": True},
        {"Id": 52, "OperationalUnitName": "Server", "Company": 2, "Active": True},
    ],
    "/api/v1/supervise/employee": [
        {"Id": 71, "FirstName": "Ann", "LastName": "A", "Email": "ann@x.test", "Active": True,
         "WorkplaceArray": [{"Company": 1}, {"Company": 2}]},
        {"Id": 72, "FirstName": "Ben", "LastName": "B", "Email": "ben@x.test", "Active": True,
         "WorkplaceArray": [{"Company": 2}]},
    ],
    "/api/v1/supervise/unavail/71": [],
    "/api/v1/supervise/unavail/72": [],
}


@pytest_asyncio.fixture
async def paid_tenant(db_session: AsyncSession, monkeypatch) -> dict:
    """A paid ownership group, so assert_paid_plan passes on every import."""
    monkeypatch.setattr(settings, "INTEGRATION_IMPORT_BURST", 1000)
    og_id, company_id, region_id, user_id = _id(), _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Paid", stripe_subscription_id="sub_x"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Paid Co", slug=_id(), ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    db_session.add(User(id=user_id, company_id=company_id, email="mgr@paid.test",
                        hashed_password="x", full_name="Mgr", user_role="manager"))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id, "region_id": region_id, "user_id": user_id,
            "headers": {"Authorization": f"Bearer {_make_token(user_id, company_id, 'manager')}"}}


async def _one(db: AsyncSession, stmt):
    with tenant_scope_disabled():
        return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one()


async def _history(db: AsyncSession, tenant: dict, emp: Employee, past_loc: Location,
                   future_loc: Location, role: Role, keep_role: Role) -> dict:
    """Past approved shift with a check-in, time entry, payroll export and
    special-hours day at `past_loc`; a future approved shift at `future_loc`."""
    cid = tenant["company_id"]
    past_sched = ShiftSchedule(id=_id(), company_id=cid, location_id=past_loc.id,
                               week_start_date=PAST_DAY - timedelta(days=PAST_DAY.weekday()),
                               status="approved")
    future_sched = ShiftSchedule(id=_id(), company_id=cid, location_id=future_loc.id,
                                 week_start_date=FUTURE_DAY - timedelta(days=FUTURE_DAY.weekday()),
                                 status="approved")
    db.add_all([past_sched, future_sched])
    await db.flush()
    past_start = datetime.combine(PAST_DAY, time(14), tzinfo=timezone.utc)
    past = Shift(id=_id(), company_id=cid, shift_schedule_id=past_sched.id, location_id=past_loc.id,
                 employee_id=emp.id, role_id=role.id, role_name=role.name, date=PAST_DAY,
                 start_time=past_start, end_time=past_start + timedelta(hours=8))
    future_start = datetime.combine(FUTURE_DAY, time(14), tzinfo=timezone.utc)
    future = Shift(id=_id(), company_id=cid, shift_schedule_id=future_sched.id,
                   location_id=future_loc.id, employee_id=emp.id, role_id=keep_role.id,
                   role_name=keep_role.name, date=FUTURE_DAY,
                   start_time=future_start, end_time=future_start + timedelta(hours=8))
    db.add_all([past, future])
    await db.flush()
    check_in = EmployeeCheckIn(id=_id(), company_id=cid, location_id=past_loc.id, employee_id=emp.id,
                               shift_id=past.id, checked_in_at=past_start, local_date=PAST_DAY,
                               counter=0, status="matched")
    db.add(check_in)
    await db.flush()
    export = PayrollExport(id=_id(), company_id=cid, exported_by_user_id=tenant["user_id"],
                           location_id=past_loc.id, format="csv", range_start=PAST_DAY,
                           range_end=PAST_DAY, entry_count=1, paid_minutes_total=480)
    db.add(export)
    await db.flush()
    entry = TimeEntry(id=_id(), company_id=cid, location_id=past_loc.id, employee_id=emp.id,
                      shift_id=past.id, role_id=role.id, role_name=role.name,
                      employee_name=emp.full_name, location_name=past_loc.name,
                      location_timezone=past_loc.timezone, pay_date=PAST_DAY,
                      start_time=past.start_time, end_time=past.end_time, paid_minutes=480,
                      source="checked_in", check_in_id=check_in.id, payroll_export_id=export.id)
    db.add(entry)
    db.add(SpecialHoursDay(id=_id(), company_id=cid, location_id=past_loc.id, date=FUTURE_DAY,
                           open_time=time(10), close_time=time(16)))
    await db.commit()
    return {"past": past.id, "future": future.id, "entry": entry.id, "check_in": check_in.id}


@pytest.mark.xfail(strict=True, reason="#126: re-import hard-deletes; fixed in Task 7")
@pytest.mark.asyncio
async def test_7shifts_reimport_archives_and_restores(client: AsyncClient, db_session: AsyncSession,
                                                      paid_tenant: dict, monkeypatch):
    routes = copy.deepcopy(SEVEN_SHIFTS)
    install_fake_upstream(monkeypatch, routes)
    url, h = "/api/v1/import/7shifts", paid_tenant["headers"]

    first = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["employees"]["created"] == 2

    ann = await _one(db_session, select(Employee).where(Employee.external_id == "9031"))
    downtown = await _one(db_session, select(Location).where(Location.external_id == "11"))
    uptown = await _one(db_session, select(Location).where(Location.external_id == "12"))
    cook = await _one(db_session, select(Role).where(Role.external_id == "21"))
    server = await _one(db_session, select(Role).where(Role.external_id == "22"))
    ids = await _history(db_session, paid_tenant, ann, downtown, uptown, cook, server)

    # Ann, Downtown and Cook disappear upstream.
    routes[f"{V2}/company/100/users"]["data"] = routes[f"{V2}/company/100/users"]["data"][1:]
    routes[f"{V2}/company/100/locations"]["data"] = routes[f"{V2}/company/100/locations"]["data"][1:]
    routes[f"{V2}/company/100/roles"]["data"] = routes[f"{V2}/company/100/roles"]["data"][1:]

    second = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["employees"]["archived"] == 1
    assert body["locations"]["archived"] == 1
    assert body["roles"]["archived"] == 1

    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is not None
    assert (await _one(db_session, select(Location).where(Location.id == downtown.id))).archived_at is not None
    assert (await _one(db_session, select(Role).where(Role.id == cook.id))).archived_at is not None
    past = await _one(db_session, select(Shift).where(Shift.id == ids["past"]))
    assert past.employee_id == ann.id and past.role_id == cook.id
    assert (await _one(db_session, select(TimeEntry).where(TimeEntry.id == ids["entry"]))).employee_id == ann.id
    assert (await _one(db_session, select(EmployeeCheckIn).where(EmployeeCheckIn.id == ids["check_in"]))) is not None
    future = await _one(db_session, select(Shift).where(Shift.id == ids["future"]))
    assert future.employee_id is None  # open shift (#174)

    # Ann and Downtown come back upstream.
    routes[f"{V2}/company/100/users"]["data"].insert(0, copy.deepcopy(SEVEN_SHIFTS[f"{V2}/company/100/users"]["data"][0]))
    routes[f"{V2}/company/100/locations"]["data"].insert(0, copy.deepcopy(SEVEN_SHIFTS[f"{V2}/company/100/locations"]["data"][0]))

    third = await client.post(url, json={"access_token": "fake"}, headers=h)
    assert third.status_code == 200, third.text
    assert third.json()["employees"]["restored"] == 1
    assert third.json()["locations"]["restored"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is None
    assert (await _one(db_session, select(Location).where(Location.id == downtown.id))).archived_at is None
    assert await _one(db_session, select(func.count()).select_from(Employee)
                      .where(Employee.external_id == "9031")) == 1


@pytest.mark.xfail(strict=True, reason="#126: re-import hard-deletes; fixed in Task 7")
@pytest.mark.asyncio
async def test_deputy_reimport_archives_and_restores(client: AsyncClient, db_session: AsyncSession,
                                                     paid_tenant: dict, monkeypatch):
    routes = copy.deepcopy(DEPUTY)
    install_fake_upstream(monkeypatch, routes)
    url, h = "/api/v1/import/deputy", paid_tenant["headers"]
    req = {"access_token": "fake", "deputy_base_url": "https://fake.deputy.com"}

    first = await client.post(url, json=req, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["employees"]["created"] == 2

    ann = await _one(db_session, select(Employee).where(Employee.external_id == "deputy:71"))
    downtown = await _one(db_session, select(Location).where(Location.name == "Downtown"))
    uptown = await _one(db_session, select(Location).where(Location.name == "Uptown"))
    cook = await _one(db_session, select(Role).where(Role.name == "Cook"))
    server = await _one(db_session, select(Role).where(Role.name == "Server"))
    ids = await _history(db_session, paid_tenant, ann, downtown, uptown, cook, server)

    routes["/api/v1/supervise/employee"] = routes["/api/v1/supervise/employee"][1:]
    second = await client.post(url, json=req, headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["employees"]["archived"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is not None
    assert (await _one(db_session, select(Shift).where(Shift.id == ids["past"]))).employee_id == ann.id
    assert (await _one(db_session, select(Shift).where(Shift.id == ids["future"]))).employee_id is None

    routes["/api/v1/supervise/employee"].insert(0, copy.deepcopy(DEPUTY["/api/v1/supervise/employee"][0]))
    third = await client.post(url, json=req, headers=h)
    assert third.status_code == 200, third.text
    assert third.json()["employees"]["restored"] == 1
    assert (await _one(db_session, select(Employee).where(Employee.id == ann.id))).archived_at is None
    assert await _one(db_session, select(func.count()).select_from(Employee)
                      .where(Employee.external_id == "deputy:71")) == 1
