"""Archived rows leave the planning surfaces and stay in the history (#126)."""

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company, Employee, EmployeeAvailability, EmployeeInvite, EmployeeRole, Location, Region, Shift,
    ShiftSchedule, User,
)
from backend.models.employee_role_minutes import EmployeeRoleMinutes
from backend.models.ownership_group import OwnershipGroup
from backend.services.candidates import rank_candidates
from tests.conftest import (
    COMPANY_ID, EMPLOYEE1_ID, EMPLOYEE2_ID, EMPLOYEE_USER_ID, LOCATION_ID, ROLE_FLOOR_ID,
    _id, _make_token,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# --- scheduling inputs and the open-shift picker -----------------------------


async def test_archived_employee_never_a_candidate(db_session, seed_location, seed_roles, seed_employees):
    emp, role = seed_employees[0], seed_roles[0]
    if not (await db_session.execute(select(EmployeeRole).where(
            EmployeeRole.employee_id == emp.id, EmployeeRole.role_id == role.id))).first():
        db_session.add(EmployeeRole(company_id=COMPANY_ID, employee_id=emp.id, role_id=role.id, skill_level=3))
    emp.location_ids = [seed_location.id]
    await db_session.commit()
    day = (NOW + timedelta(days=3)).date()
    span = (datetime.combine(day, time(9)), datetime.combine(day, time(17)))

    before = await rank_candidates(db_session, COMPANY_ID, seed_location, role.id, *span)
    assert emp.id in [c.employee_id for c in before]

    emp.archived_at = NOW
    await db_session.commit()
    cands = await rank_candidates(db_session, COMPANY_ID, seed_location, role.id, *span)
    assert emp.id not in [c.employee_id for c in cands]


async def test_archived_role_does_not_count_as_having_the_role(
        db_session, seed_location, seed_roles, seed_employees):
    emp, role = seed_employees[0], seed_roles[0]
    emp.location_ids = [seed_location.id]
    role.archived_at = NOW
    await db_session.commit()
    day = (NOW + timedelta(days=3)).date()
    cands = await rank_candidates(db_session, COMPANY_ID, seed_location, role.id,
                                  datetime.combine(day, time(9)), datetime.combine(day, time(17)))
    assert all(not c.has_role for c in cands if c.employee_id == emp.id)


async def test_archived_employee_absent_from_scheduler_input(
        db_session, seed_location, seed_employees, seed_shift_template):
    from backend.scheduling import graph
    week = (NOW + timedelta(days=7)).date().isoformat()

    before = await graph._load_initial_state(COMPANY_ID, week, db_session)
    assert seed_employees[0].id in {e["id"] for e in before["employees"]}

    seed_employees[0].archived_at = NOW
    await db_session.commit()
    state = await graph._load_initial_state(COMPANY_ID, week, db_session)
    assert seed_employees[0].id not in {e["id"] for e in state["employees"]}
    assert seed_employees[1].id in {e["id"] for e in state["employees"]}


async def test_archived_role_drops_template_slots_and_employee_roles(
        db_session, seed_location, seed_roles, seed_employees, seed_shift_template):
    from backend.scheduling import graph
    floor = seed_roles[0]
    floor.archived_at = NOW
    await db_session.commit()
    week = (NOW + timedelta(days=7)).date().isoformat()
    state = await graph._load_initial_state(COMPANY_ID, week, db_session)

    slots = [s for day in state["shift_templates"][seed_location.id]["weekly_schedule"].values()
             for s in day]
    assert slots, "the active role's slot must survive"
    assert floor.id not in {str(s.get("role_id")) for s in slots}
    for emp in state["employees"]:
        assert floor.id not in {r["role_id"] for r in emp["roles"]}


async def test_archived_location_absent_from_scheduler_input(
        db_session, seed_location, seed_employees, seed_shift_template):
    from backend.scheduling import graph
    seed_location.archived_at = NOW
    await db_session.commit()
    state = await graph._load_initial_state(
        COMPANY_ID, (NOW + timedelta(days=7)).date().isoformat(), db_session)
    assert seed_location.id not in {loc["id"] for loc in state["locations"]}


# --- editing an approved schedule ---------------------------------------------


def _approved_edit_url(schedule_id):
    return f"/api/v1/schedules/{schedule_id}/approved-shifts"


async def _approved(db, loc, role, emp, start):
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=loc.id,
                          week_start_date=start.date() - timedelta(days=start.weekday()), status="approved",
                          created_at=datetime.now(timezone.utc))
    db.add(sched)
    await db.flush()
    shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=loc.id,
                  employee_id=emp.id if emp else None, role_id=role.id, role_name=role.name,
                  date=start.date(), start_time=start, end_time=start + timedelta(hours=8))
    db.add(shift)
    await db.commit()
    return sched, shift


async def test_assigning_archived_employee_409(client, db_session, manager_token, seed_location,
                                               seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    sched, shift = await _approved(db_session, seed_location, seed_roles[0], None, start)
    shift_id = shift.id
    seed_employees[1].archived_at = NOW
    await db_session.commit()
    resp = await client.put(_approved_edit_url(sched.id), headers=_h(manager_token),
                            json={"edits": [{"shift_id": shift_id, "employee_id": seed_employees[1].id}]})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "employee_archived"
    db_session.expire_all()
    assert (await db_session.get(Shift, shift_id)).employee_id is None


async def test_creating_a_shift_for_archived_employee_409(client, db_session, manager_token, seed_location,
                                                          seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    sched, _ = await _approved(db_session, seed_location, seed_roles[0], None, start)
    seed_employees[1].archived_at = NOW
    await db_session.commit()
    resp = await client.put(_approved_edit_url(sched.id), headers=_h(manager_token), json={"edits": [{
        "employee_id": seed_employees[1].id, "role_id": seed_roles[0].id, "date": start.date().isoformat(),
        "start_time": start.isoformat(), "end_time": (start + timedelta(hours=8)).isoformat()}]})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "employee_archived"


async def test_time_only_edit_keeps_archived_assignee(client, db_session, manager_token, seed_location,
                                                      seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    emp = seed_employees[0]
    sched, shift = await _approved(db_session, seed_location, seed_roles[0], emp, start)
    emp.archived_at = NOW  # archived directly, so the shift was not opened
    await db_session.commit()
    new_start = start + timedelta(hours=1)
    resp = await client.put(_approved_edit_url(sched.id), headers=_h(manager_token), json={"edits": [{
        "shift_id": shift.id, "employee_id": emp.id,
        "start_time": new_start.isoformat(), "end_time": (new_start + timedelta(hours=8)).isoformat()}]})
    assert resp.status_code == 200, resp.text


async def test_moving_a_shift_to_an_archived_role_404_but_keeping_it_is_fine(
        client, db_session, manager_token, seed_location, seed_roles, seed_employees):
    start = NOW + timedelta(days=3)
    floor, lead = seed_roles
    sched, shift = await _approved(db_session, seed_location, floor, seed_employees[0], start)
    floor.archived_at = NOW
    lead.archived_at = NOW
    await db_session.commit()

    keep = await client.put(_approved_edit_url(sched.id), headers=_h(manager_token),
                            json={"edits": [{"shift_id": shift.id, "role_id": floor.id}]})
    assert keep.status_code == 200, keep.text
    move = await client.put(_approved_edit_url(sched.id), headers=_h(manager_token),
                            json={"edits": [{"shift_id": shift.id, "role_id": lead.id}]})
    assert move.status_code == 404, move.text


# --- approving a draft ---------------------------------------------------------
# Draft builder copied verbatim from tests/test_open_shifts_approval_integration.py.


def _entry(employee_id, status="ok", name="Alice Johnson"):
    return {
        "employee_id": employee_id, "employee_name": name,
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-04-13",
        "start_time": "2026-04-13T09:00:00-04:00",
        "end_time": "2026-04-13T17:00:00-04:00", "status": status,
    }


async def _draft(db: AsyncSession, shifts: list[dict]) -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13), status="draft",
        raw_llm_output=json.dumps(shifts),
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return sid


async def test_approval_opens_archived_employees_entries(client, db_session, manager_token, seed_location,
                                                         seed_roles, seed_employees):
    sid = await _draft(db_session, [
        _entry(EMPLOYEE1_ID),
        _entry(EMPLOYEE2_ID, name="Bob Smith"),
    ])
    seed_employees[0].archived_at = NOW
    await db_session.commit()

    resp = await client.post(f"/api/v1/schedules/{sid}/approve", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text

    rows = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == sid)
    )).scalars().all()
    assert len(rows) == 2
    assert EMPLOYEE1_ID not in {r.employee_id for r in rows}
    assert sorted(r.employee_id or "" for r in rows) == ["", EMPLOYEE2_ID]
    opened = next(r for r in rows if r.employee_id is None)
    assert opened.role_id == ROLE_FLOOR_ID and opened.preference_violations == []

    archived_minutes = (await db_session.execute(
        select(func.count()).select_from(EmployeeRoleMinutes)
        .where(EmployeeRoleMinutes.employee_id == EMPLOYEE1_ID)
    )).scalar_one()
    assert archived_minutes == 0
    active_minutes = (await db_session.execute(
        select(func.count()).select_from(EmployeeRoleMinutes)
        .where(EmployeeRoleMinutes.employee_id == EMPLOYEE2_ID)
    )).scalar_one()
    assert active_minutes == 1


async def test_approval_keeps_an_archived_employees_conflict_entry_unmaterialised(
        client, db_session, manager_token, seed_location, seed_roles, seed_employees):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID, status="CONFLICT")])
    seed_employees[0].archived_at = NOW
    await db_session.commit()
    resp = await client.post(f"/api/v1/schedules/{sid}/approve", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["shifts"] == []


async def test_approving_draft_at_archived_location_409(client, db_session, manager_token, seed_location,
                                                        seed_roles, seed_employees):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID)])
    seed_location.archived_at = NOW
    await db_session.commit()

    resp = await client.post(f"/api/v1/schedules/{sid}/approve", headers=_h(manager_token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "location_archived"
    db_session.expire_all()
    assert (await db_session.get(ShiftSchedule, sid)).status == "draft"
    count = (await db_session.execute(
        select(func.count()).select_from(Shift).where(Shift.shift_schedule_id == sid)
    )).scalar_one()
    assert count == 0


# --- history surfaces ------------------------------------------------------------


async def test_week_view_still_shows_archived_employee(client, db_session, manager_token, seed_location,
                                                       seed_roles, seed_employees):
    start = NOW - timedelta(days=2)
    emp = seed_employees[0]
    sched, _ = await _approved(db_session, seed_location, seed_roles[0], emp, start)
    emp.archived_at = NOW
    await db_session.commit()
    resp = await client.get(f"/api/v1/schedules/week/{sched.week_start_date.isoformat()}",
                            headers=_h(manager_token))
    assert resp.status_code == 200
    assert emp.full_name in resp.text


# --- an archived employee's own login ----------------------------------------------


async def _link_to_login(db: AsyncSession, emp: Employee) -> None:
    emp.user_id = EMPLOYEE_USER_ID
    await db.commit()


async def test_my_profile_hidden_when_archived(client, db_session, employee_token, seed_employees,
                                               seed_employee_user):
    emp = seed_employees[0]
    await _link_to_login(db_session, emp)
    assert (await client.get("/api/v1/employees/me", headers=_h(employee_token))).status_code == 200

    emp.archived_at = NOW
    await db_session.commit()
    resp = await client.get("/api/v1/employees/me", headers=_h(employee_token))
    assert resp.status_code == 404


async def test_archived_employee_cannot_touch_own_availability(client, db_session, employee_token,
                                                               seed_employees, seed_employee_user):
    emp = seed_employees[0]
    await _link_to_login(db_session, emp)
    start = NOW + timedelta(days=2)
    avail = EmployeeAvailability(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                                 year=start.year, month=start.month, day=start.day,
                                 start_time=start, end_time=start + timedelta(hours=8))
    db_session.add(avail)
    await db_session.commit()
    listed = await client.get(f"/api/v1/employees/{emp.id}/availability", headers=_h(employee_token))
    assert listed.status_code == 200

    emp.archived_at = NOW
    await db_session.commit()
    listed = await client.get(f"/api/v1/employees/{emp.id}/availability", headers=_h(employee_token))
    assert listed.status_code == 404
    created = await client.post("/api/v1/employees/availability", headers=_h(employee_token), json={
        "employee_id": emp.id, "year": start.year, "month": start.month, "day": start.day,
        "start_time": start.isoformat(), "end_time": (start + timedelta(hours=8)).isoformat()})
    assert created.status_code == 404
    deleted = await client.delete(f"/api/v1/employees/availability/{avail.id}", headers=_h(employee_token))
    assert deleted.status_code == 403
    avail_id = avail.id
    db_session.expire_all()
    assert await db_session.get(EmployeeAvailability, avail_id) is not None


async def test_archived_employee_cannot_read_own_blackouts(client, db_session, employee_token,
                                                           seed_employees, seed_employee_user):
    emp = seed_employees[0]
    await _link_to_login(db_session, emp)
    url = f"/api/v1/employees/{emp.id}/day-blackouts"
    assert (await client.get(url, headers=_h(employee_token))).status_code == 200

    emp.archived_at = NOW
    await db_session.commit()
    assert (await client.get(url, headers=_h(employee_token))).status_code == 404
    # Creating blackouts is manager-only; the employee token never reaches it.
    created = await client.post("/api/v1/employees/day-blackouts", headers=_h(employee_token), json={
        "employee_id": emp.id, "day_of_week": 0, "start_time": "09:00", "end_time": "12:00"})
    assert created.status_code == 403


async def test_archived_employee_preferences_refused(client, db_session, employee_token, manager_token,
                                                     seed_employees, seed_employee_user):
    emp = seed_employees[0]
    await _link_to_login(db_session, emp)
    emp.archived_at = NOW
    await db_session.commit()
    body = {"employee_id": emp.id, "day_of_week": 1, "weight": 0.5}
    as_employee = await client.post("/api/v1/scheduling-preferences/days",
                                    headers=_h(employee_token), json=body)
    assert as_employee.status_code == 403
    # Nor is a new preference recorded for an archived employee by a manager.
    as_manager = await client.post("/api/v1/scheduling-preferences/days",
                                   headers=_h(manager_token), json=body)
    assert as_manager.status_code == 404


async def test_invite_for_archived_employee_is_revoked(client, db_session, seed_employees):
    emp = seed_employees[1]
    token = _id() + _id()
    db_session.add(EmployeeInvite(id=_id(), company_id=COMPANY_ID, employee_id=emp.id, token=token,
                                  email="bob-invite@test.com", status="pending",
                                  expires_at=NOW + timedelta(days=7)))
    emp.archived_at = NOW
    await db_session.commit()

    info = await client.get(f"/api/v1/invites/{token}")
    assert info.status_code == 410
    assert info.json()["detail"]["code"] == "invite_revoked"
    accepted = await client.post(f"/api/v1/invites/{token}/accept", json={"password": "Str0ng-pass!"})
    assert accepted.status_code == 410
    assert accepted.json()["detail"]["code"] == "invite_revoked"
    users = (await db_session.execute(
        select(func.count()).select_from(User).where(User.email == "bob-invite@test.com")
    )).scalar_one()
    assert users == 0


# --- check-in ------------------------------------------------------------------------


@pytest.fixture
def _qr_secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


async def _paid_tenant(db: AsyncSession) -> dict:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G", stripe_subscription_id="sub_x"))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}", ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location = Location(id=_id(), company_id=company_id, region_id=region_id,
                        name="Here", timezone="America/New_York")
    db.add(location)
    manager_id, user_id = _id(), _id()
    db.add(User(id=manager_id, company_id=company_id, email=f"{manager_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="E", user_role="employee"))
    await db.flush()
    employee = Employee(id=_id(), company_id=company_id, full_name="E", email=f"{user_id}@example.com",
                        location_ids=[location.id], user_id=user_id)
    db.add(employee)
    await db.commit()
    return {
        "location": location, "employee": employee,
        "manager": _h(_make_token(manager_id, company_id, "manager")),
        "employee_headers": _h(_make_token(user_id, company_id, "employee")),
    }


async def test_check_in_at_archived_location_refused(client, db_session, _qr_secret):
    t = await _paid_tenant(db_session)
    t["location"].archived_at = NOW
    await db_session.commit()
    resp = await client.post("/api/v1/check-ins", headers=t["employee_headers"],
                             json={"location_id": t["location"].id, "token": "x"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "location_archived"
    qr = await client.get(f"/api/v1/check-ins/qr?location_id={t['location'].id}", headers=t["manager"])
    assert qr.status_code == 404


async def test_archived_employee_cannot_check_in(client, db_session, _qr_secret):
    t = await _paid_tenant(db_session)
    t["employee"].archived_at = NOW
    await db_session.commit()
    resp = await client.post("/api/v1/check-ins", headers=t["employee_headers"],
                             json={"location_id": t["location"].id, "token": "x"})
    assert resp.status_code == 403, resp.text
