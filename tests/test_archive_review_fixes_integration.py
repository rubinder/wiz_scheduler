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

