"""Final whole-branch review fixes for archive-not-delete (#126)."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from backend.config import settings
from backend.models import (
    Company, Employee, Location, OwnershipGroup, Region, Shift, ShiftSchedule, User,
)
from backend.services.archive import archive_location, restore_location
from backend.services.billing import count_schedules_this_month
from backend.services.location_quota import any_location_available, resolve_location_quota
from backend.services.plan import assert_can_add, get_plan_state
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id, _make_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _free_group(db, n_locations):
    og_id, company_id, region_id, user_id = _id(), _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="Free Group"))
    await db.flush()
    db.add(Company(id=company_id, name="Free Co", slug=_id(), ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    db.add(User(id=user_id, company_id=company_id, email=f"{_id()}@free.test",
                hashed_password="x", full_name="Mgr", user_role="manager"))
    await db.flush()
    locs = [Location(id=_id(), company_id=company_id, region_id=region_id, name=f"L{i}", timezone="UTC")
            for i in range(n_locations)]
    db.add_all(locs)
    await db.commit()
    return {"og_id": og_id, "company_id": company_id, "region_id": region_id, "locs": locs,
            "token": _make_token(user_id, company_id, "manager")}


# --- 1. archiving a location keeps its unapproved schedules as "discarded" ------


async def test_archive_restore_generate_is_still_refused_by_the_free_quota(db_session):
    t = await _free_group(db_session, 1)
    loc = t["locs"][0]
    week = datetime.now(timezone.utc).date()
    db_session.add(ShiftSchedule(id=_id(), company_id=t["company_id"], location_id=loc.id,
                                 week_start_date=week - timedelta(days=week.weekday()),
                                 status="draft", created_at=datetime.now(timezone.utc)))
    await db_session.commit()
    before = await count_schedules_this_month(db_session, t["og_id"])

    await archive_location(db_session, t["company_id"], loc)
    await db_session.commit()
    await restore_location(db_session, t["company_id"], loc)
    await db_session.commit()

    quota = await resolve_location_quota(db_session, t["company_id"], [loc.id])
    assert quota[loc.id]["allowed"] is False
    assert quota[loc.id]["code"] == "location_scheduled_this_month"
    assert await any_location_available(db_session, t["company_id"]) is False
    # Paid overage meters the same rows: archiving must not un-count them.
    assert await count_schedules_this_month(db_session, t["og_id"]) == before == 1


async def _discarded(db) -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13), status="discarded",
        raw_llm_output=json.dumps([{
            "employee_id": EMPLOYEE1_ID, "employee_name": "Alice Johnson",
            "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
            "location_id": LOCATION_ID, "date": "2026-04-13",
            "start_time": "2026-04-13T09:00:00-04:00",
            "end_time": "2026-04-13T17:00:00-04:00", "status": "ok",
        }]),
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return sid


async def test_approving_a_discarded_schedule_409(client, db_session, manager_token, seed_location,
                                                  seed_roles, seed_employees):
    sid = await _discarded(db_session)
    resp = await client.post(f"/api/v1/schedules/{sid}/approve", headers=_h(manager_token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "schedule_discarded"
    db_session.expire_all()
    assert (await db_session.get(ShiftSchedule, sid)).status == "discarded"
    count = (await db_session.execute(
        select(func.count()).select_from(Shift).where(Shift.shift_schedule_id == sid)
    )).scalar_one()
    assert count == 0


async def test_discarded_schedule_cannot_be_edited_or_rejected(client, db_session, manager_token,
                                                              seed_location, seed_roles, seed_employees):
    sid = await _discarded(db_session)
    resp = await client.put(f"/api/v1/schedules/{sid}/shifts", headers=_h(manager_token),
                            json={"shifts": []})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "schedule_discarded"
    resp = await client.post(f"/api/v1/schedules/{sid}/reject", headers=_h(manager_token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "schedule_discarded"
    db_session.expire_all()
    assert (await db_session.get(ShiftSchedule, sid)).status == "discarded"


async def test_week_listing_hides_discarded_schedules(client, db_session, manager_token, seed_location,
                                                      seed_roles, seed_employees):
    sid = await _discarded(db_session)
    resp = await client.get("/api/v1/schedules/week/2026-04-13", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text
    assert sid not in [s["id"] for s in resp.json()]


# --- 2. over_limit counts active locations only ---------------------------------


async def test_archiving_down_to_the_location_limit_clears_over_limit(db_session):
    t = await _free_group(db_session, settings.FREE_PLAN_MAX_LOCATIONS + 1)
    state = await get_plan_state(db_session, t["company_id"])
    assert state["over_limit"] is True

    await archive_location(db_session, t["company_id"], t["locs"][0])
    await db_session.commit()

    state = await get_plan_state(db_session, t["company_id"])
    assert state["over_limit"] is False
    assert state["can_generate_local"] is True
    assert state["block_reason"] is None
    assert state["locations"]["count"] == settings.FREE_PLAN_MAX_LOCATIONS
    # The archived location still holds its slot this month for new adds.
    with pytest.raises(HTTPException) as exc:
        await assert_can_add(db_session, t["company_id"], locations=1)
    assert exc.value.status_code == 402
    assert exc.value.detail["limit"] == "locations"


# --- 5. an erased employee cannot be edited back into existence -----------------


async def test_update_erased_employee_409(client, db_session, manager_token, seed_employees):
    emp = seed_employees[0]
    now = datetime.now(timezone.utc)
    emp.archived_at, emp.erased_at, emp.email = now, now, None
    await db_session.commit()
    resp = await client.put(f"/api/v1/employees/{emp.id}", headers=_h(manager_token),
                            json={"email": "back@example.com"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "employee_erased"
    email = (await db_session.execute(select(Employee.email).where(Employee.id == emp.id))).scalar_one()
    assert email is None


# --- 8. bulk-upload "restore it instead" carries the row prefix -----------------


async def test_bulk_upload_archived_location_message_has_row_prefix(client, db_session, manager_token,
                                                                    seed_location):
    seed_location.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    payload = json.dumps([{"name": seed_location.name, "region_name": "East Coast"}])
    resp = await client.post("/api/v1/locations/bulk-upload", headers=_h(manager_token),
                             files={"file": ("l.json", payload, "application/json")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == [f"Row 1: {seed_location.name}: exists (archived) — restore it instead"]


async def test_bulk_upload_archived_employee_message_has_row_prefix(client, db_session, manager_token,
                                                                    seed_employees):
    emp = seed_employees[0]
    emp.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    payload = json.dumps([{"full_name": "Someone", "email": emp.email}])
    resp = await client.post("/api/v1/employees/bulk-upload", headers=_h(manager_token),
                             files={"file": ("e.json", payload, "application/json")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == [f"Row 1: {emp.email}: exists (archived) — restore it instead"]


# --- 6. archive races: the archived check reads the locked, committed row -------


async def test_assigning_employee_archived_by_another_session_409(client, db_session, manager_token,
                                                                  seed_location, seed_roles, seed_employees):
    """The request session already holds the employee in its identity map
    (expire_on_commit=False), as a long request would. Another transaction
    archives them and commits; the assignment must still see it."""
    from tests.conftest import get_test_session_factory
    from backend.services.archive import archive_employee

    start = datetime.now(timezone.utc) + timedelta(days=3)
    role, emp = seed_roles[0], seed_employees[1]
    sched = ShiftSchedule(id=_id(), company_id=COMPANY_ID, location_id=seed_location.id,
                          week_start_date=start.date() - timedelta(days=start.weekday()),
                          status="approved", created_at=datetime.now(timezone.utc))
    db_session.add(sched)
    await db_session.flush()
    shift = Shift(id=_id(), company_id=COMPANY_ID, shift_schedule_id=sched.id, location_id=seed_location.id,
                  employee_id=None, role_id=role.id, role_name=role.name, date=start.date(),
                  start_time=start, end_time=start + timedelta(hours=8))
    db_session.add(shift)
    await db_session.commit()
    assert emp.archived_at is None

    async with get_test_session_factory()() as other:
        other_emp = await other.get(Employee, emp.id)
        await archive_employee(other, COMPANY_ID, other_emp)
        await other.commit()
    assert emp.archived_at is None  # the request session's copy is stale

    sched_id, shift_id, emp_id = sched.id, shift.id, emp.id
    resp = await client.put(f"/api/v1/schedules/{sched_id}/approved-shifts", headers=_h(manager_token),
                            json={"edits": [{"shift_id": shift_id, "employee_id": emp_id}]})
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "employee_archived"
    assigned = (await db_session.execute(select(Shift.employee_id).where(Shift.id == shift_id))).scalar_one()
    assert assigned is None


async def test_lock_employees_returns_committed_archived_at(db_session, seed_employees):
    from tests.conftest import get_test_session_factory
    from backend.services.archive import lock_employees

    archived_id, active_id = seed_employees[0].id, seed_employees[1].id
    async with get_test_session_factory()() as other:
        (await other.get(Employee, archived_id)).archived_at = datetime.now(timezone.utc)
        await other.commit()
    assert seed_employees[0].archived_at is None  # stale identity-map copy
    locked = await lock_employees(db_session, COMPANY_ID, [archived_id, active_id, None])
    await db_session.rollback()
    assert locked[archived_id] is not None
    assert locked[active_id] is None


# --- follow-up 1: no rotating the monthly allowance across archived locations ---


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _generated(db, t, loc, *, status="draft", created_at=None) -> str:
    sid = _id()
    week = datetime.now(timezone.utc).date()
    db.add(ShiftSchedule(id=sid, company_id=t["company_id"], location_id=loc.id,
                         week_start_date=week - timedelta(days=week.weekday()),
                         status=status, created_at=created_at or datetime.now(timezone.utc)))
    await db.commit()
    return sid


async def test_rotating_locations_through_archive_cannot_exceed_the_location_limit(
        db_session, monkeypatch):
    monkeypatch.setattr(settings, "FREE_PLAN_MAX_LOCATIONS", 1)
    t = await _free_group(db_session, 2)  # a downgraded tenant: two locations, limit one
    a, b = t["locs"]

    # Archive B to get under the limit; A may generate.
    await archive_location(db_session, t["company_id"], b)
    await db_session.commit()
    assert (await get_plan_state(db_session, t["company_id"]))["over_limit"] is False
    assert (await resolve_location_quota(db_session, t["company_id"], [a.id]))[a.id]["allowed"] is True
    sid = await _generated(db_session, t, a)

    # Swap: archive A, restore B (B still holds its slot this month).
    await archive_location(db_session, t["company_id"], a)
    await restore_location(db_session, t["company_id"], b)
    await db_session.commit()
    assert (await get_plan_state(db_session, t["company_id"]))["over_limit"] is False

    quota = await resolve_location_quota(db_session, t["company_id"], [b.id])
    assert quota[b.id]["allowed"] is False
    assert quota[b.id]["code"] == "location_limit_this_month"
    assert await any_location_available(db_session, t["company_id"]) is False

    # Next month the count starts again.
    await db_session.execute(
        ShiftSchedule.__table__.update().where(ShiftSchedule.id == sid)
        .values(created_at=_month_start() - timedelta(days=1))
    )
    await db_session.commit()
    assert (await resolve_location_quota(db_session, t["company_id"], [b.id]))[b.id]["allowed"] is True


async def test_rotation_through_reject_is_refused_too(db_session, monkeypatch):
    monkeypatch.setattr(settings, "FREE_PLAN_MAX_LOCATIONS", 1)
    t = await _free_group(db_session, 2)
    a, b = t["locs"]
    await _generated(db_session, t, a, status="rejected")
    quota = await resolve_location_quota(db_session, t["company_id"], [a.id, b.id])
    # A started its month and may retry within its own allowance; B may not start.
    assert quota[a.id]["allowed"] is True
    assert quota[b.id]["allowed"] is False
    assert quota[b.id]["code"] == "location_limit_this_month"


async def test_one_run_cannot_start_more_locations_than_the_limit(db_session, monkeypatch):
    monkeypatch.setattr(settings, "FREE_PLAN_MAX_LOCATIONS", 1)
    t = await _free_group(db_session, 2)
    a, b = t["locs"]
    quota = await resolve_location_quota(db_session, t["company_id"], [a.id, b.id])
    assert [quota[a.id]["allowed"], quota[b.id]["allowed"]] == [True, False]


async def test_within_the_limit_every_location_still_generates(db_session):
    t = await _free_group(db_session, settings.FREE_PLAN_MAX_LOCATIONS)
    ids = [loc.id for loc in t["locs"]]
    await _generated(db_session, t, t["locs"][0])
    quota = await resolve_location_quota(db_session, t["company_id"], ids)
    assert all(quota[i]["allowed"] for i in ids[1:])


# --- follow-up 2: retention never deletes a schedule the quota counts -----------


async def test_retention_keeps_this_months_quota_counted_schedules(db_session, monkeypatch):
    from backend.services.data_retention import run_data_retention
    monkeypatch.setattr(settings, "RETENTION_STALE_DRAFTS_DAYS", 0)
    monkeypatch.setattr(settings, "RETENTION_REJECTED_SCHEDULES_DAYS", 0)
    t = await _free_group(db_session, 1)
    loc = t["locs"][0]
    this_month = max(_month_start(), datetime.now(timezone.utc) - timedelta(seconds=5))
    kept = [await _generated(db_session, t, loc, status=s, created_at=this_month)
            for s in ("draft", "discarded", "rejected")]
    gone = [await _generated(db_session, t, loc, status=s, created_at=_month_start() - timedelta(days=1))
            for s in ("draft", "discarded", "rejected")]

    await run_data_retention(db_session)
    await db_session.commit()

    left = set((await db_session.execute(
        select(ShiftSchedule.id).where(ShiftSchedule.id.in_(kept + gone))
    )).scalars().all())
    assert left == set(kept)
