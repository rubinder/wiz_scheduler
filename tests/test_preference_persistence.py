"""The persisted asterisk (#99): columns, serialisation, and the write sites."""

import json
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

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
