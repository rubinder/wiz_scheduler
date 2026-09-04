"""The persisted asterisk (#99): columns, serialisation, and the write sites."""

import json
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, Shift, ShiftSchedule
from backend.models.employee import EmployeeDayPreference
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, EMPLOYEE2_ID, LOCATION_ID, REGION_ID, ROLE_FLOOR_ID, _id

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/schedules"
WEEK = date(2026, 8, 31)
DAY_V = [{"kind": "day", "weight": 0.7, "days": [0, 1, 2], "unavoidable": False}]


async def _approved_with_shift(db, violations):
    sid, shid = _id(), _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=WEEK, status="approved",
        created_at=datetime.now(timezone.utc),
        preference_summary={"shifts_against_preference": 1, "unavoidable": 0, "roster_thin": False},
    ))
    db.add(Shift(
        id=shid, company_id=COMPANY_ID, shift_schedule_id=sid, location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID, role_name="Floor Associate",
        date=date(2026, 9, 3),
        start_time=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 3, 17, 0, tzinfo=timezone.utc),
        preference_violations=violations,
    ))
    await db.commit()
    return sid, shid


async def test_week_endpoint_serialises_the_columns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _approved_with_shift(db_session, DAY_V)
    resp = await client.get(f"{BASE}/week/{WEEK.isoformat()}?status=approved",
                            headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    sched = resp.json()[0]
    assert sched["preference_summary"]["shifts_against_preference"] == 1
    assert sched["shifts"][0]["preference_violations"] == DAY_V


async def test_a_pre_existing_null_column_reads_as_an_empty_list(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _approved_with_shift(db_session, None)
    resp = await client.get(f"{BASE}/week/{WEEK.isoformat()}?status=approved",
                            headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.json()[0]["shifts"][0]["preference_violations"] == []


async def test_approve_copies_violations_from_the_draft(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    shifts = [{
        "employee_id": EMPLOYEE1_ID, "employee_name": "Alice Johnson",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-09-03",
        "start_time": "2026-09-03T09:00:00-04:00", "end_time": "2026-09-03T17:00:00-04:00",
        "status": "ok", "preference_violations": DAY_V,
    }]
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID, week_start_date=WEEK,
        status="draft", raw_llm_output=json.dumps(shifts), strategy="random",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post(f"{BASE}/{sid}/approve", headers={"Authorization": f"Bearer {manager_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["shifts"][0]["preference_violations"] == DAY_V
    row = (await db_session.execute(select(Shift).where(Shift.shift_schedule_id == sid))).scalar_one()
    assert row.preference_violations == DAY_V


async def _mon_tue_wed(db, employee_id):
    for d in (0, 1, 2):
        db.add(EmployeeDayPreference(
            id=_id(), company_id=COMPANY_ID, employee_id=employee_id, day_of_week=d, weight=0.7,
        ))
    await db.commit()


async def test_draft_edit_reannotates_and_returns_the_shifts(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID, week_start_date=WEEK,
        status="draft", raw_llm_output="[]", created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    body = {"shifts": [{
        "employee_id": EMPLOYEE1_ID, "employee_name": "Alice Johnson",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": LOCATION_ID, "date": "2026-09-03",  # Thursday
        "start_time": "2026-09-03T09:00:00-04:00", "end_time": "2026-09-03T17:00:00-04:00",
        "status": "ok", "preference_violations": [],
    }]}
    resp = await client.put(f"{BASE}/{sid}/shifts", headers={"Authorization": f"Bearer {manager_token}"}, json=body)
    assert resp.status_code == 200, resp.text
    returned = resp.json()["shifts"][0]["preference_violations"]
    assert returned == DAY_V

    sched = (await db_session.execute(select(ShiftSchedule).where(ShiftSchedule.id == sid))).scalar_one()
    assert json.loads(sched.raw_llm_output)[0]["preference_violations"] == DAY_V


async def test_load_employee_preferences_shape(db_session: AsyncSession, seed_employees):
    from backend.services.preference_loader import load_employee_preferences
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    prefs = await load_employee_preferences(db_session, COMPANY_ID)
    assert prefs[EMPLOYEE1_ID]["day_preferences"] == [
        {"day_of_week": 0, "weight": 0.7}, {"day_of_week": 1, "weight": 0.7}, {"day_of_week": 2, "weight": 0.7},
    ]
    assert prefs[EMPLOYEE1_ID]["hour_range_preferences"] == []
    assert prefs[EMPLOYEE1_ID]["hour_range_caps"] == []
    assert EMPLOYEE1_ID in prefs and len(prefs) == 1


async def test_approved_reassignment_rewrites_both_employees(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    """Alice prefers Mon-Wed and holds a Thursday shift (annotated). Reassigning
    it to Bob, who has no preferences, must clear the asterisk. Bob's other
    shift that week is re-evaluated too and stays clean."""
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid, shid = await _approved_with_shift(db_session, DAY_V)
    other = _id()
    db_session.add(Shift(
        id=other, company_id=COMPANY_ID, shift_schedule_id=sid, location_id=LOCATION_ID,
        employee_id=EMPLOYEE2_ID, role_id=ROLE_FLOOR_ID, role_name="Floor Associate",
        date=date(2026, 9, 1),
        start_time=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
        preference_violations=DAY_V,  # stale on purpose: must be recomputed to []
    ))
    await db_session.commit()

    resp = await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shid, "employee_id": EMPLOYEE2_ID}]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    moved = (await db_session.execute(select(Shift).where(Shift.id == shid))).scalar_one()
    stale = (await db_session.execute(select(Shift).where(Shift.id == other))).scalar_one()
    assert moved.preference_violations == []
    assert stale.preference_violations == []


async def test_approved_reassignment_onto_a_preference_adds_the_asterisk(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    await _mon_tue_wed(db_session, EMPLOYEE1_ID)
    sid, shid = await _approved_with_shift(db_session, [])
    # Move Alice's Thursday shift... to Alice. Same employee, but a time edit
    # still re-evaluates: keep it Thursday, change the hours.
    resp = await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shid, "start_time": "2026-09-03T10:00:00+00:00",
                          "end_time": "2026-09-03T18:00:00+00:00"}]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    row = (await db_session.execute(select(Shift).where(Shift.id == shid))).scalar_one()
    assert row.preference_violations == DAY_V


async def test_approved_reassignment_recomputes_a_different_schedule_in_the_week(
    client: AsyncClient, manager_token: str, db_session: AsyncSession, seed_employees,
):
    """The recompute spans every approved schedule in the week, not just the
    one being edited. A second location's schedule holds a stale-annotated
    shift for the employee the edit reassigns onto; that shift must be
    recomputed too, even though its schedule and location were never touched
    by the PUT."""
    sid, shid = await _approved_with_shift(db_session, DAY_V)

    other_location_id = _id()
    db_session.add(Location(
        id=other_location_id, company_id=COMPANY_ID, region_id=REGION_ID,
        name="Uptown Store", timezone="America/New_York",
    ))
    other_sid = _id()
    other_shid = _id()
    db_session.add(ShiftSchedule(
        id=other_sid, company_id=COMPANY_ID, location_id=other_location_id,
        week_start_date=WEEK, status="approved",
        created_at=datetime.now(timezone.utc),
        preference_summary={"shifts_against_preference": 1, "unavoidable": 0, "roster_thin": False},
    ))
    db_session.add(Shift(
        id=other_shid, company_id=COMPANY_ID, shift_schedule_id=other_sid, location_id=other_location_id,
        employee_id=EMPLOYEE2_ID, role_id=ROLE_FLOOR_ID, role_name="Floor Associate",
        date=date(2026, 9, 1),
        start_time=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 1, 17, 0, tzinfo=timezone.utc),
        preference_violations=DAY_V,  # stale on purpose: must be recomputed to []
    ))
    await db_session.commit()

    resp = await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shid, "employee_id": EMPLOYEE2_ID}]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    other_row = (await db_session.execute(select(Shift).where(Shift.id == other_shid))).scalar_one()
    assert other_row.preference_violations == []
