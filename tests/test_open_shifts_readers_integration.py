"""Every reader of shifts decides about open shifts explicitly (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Shift, ShiftSchedule
from backend.scheduling.graph import _committed_shifts_by_employee, _load_role_history_minutes
from backend.services.check_in import _match_shift
from backend.services.time_entries import _candidate_shifts
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)


@pytest_asyncio.fixture
async def week(db_session: AsyncSession, seed_employees: list[Employee]) -> dict:
    """An approved schedule with one filled and one open shift."""
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    ids = {}
    for key, emp in (("filled", EMPLOYEE1_ID), ("open", None)):
        ids[key] = _id()
        db_session.add(Shift(
            id=ids[key], company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=LOCATION_ID, employee_id=emp, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=DAY,
            start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
            end_time=datetime(2026, 8, 31, 17, tzinfo=NY),
        ))
    await db_session.commit()
    return {"schedule_id": sid, **ids}


async def test_holds_ignore_open_shifts(db_session, week):
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    held = await _committed_shifts_by_employee(
        db_session, COMPANY_ID, start, start + timedelta(days=7),
    )
    assert set(held) == {EMPLOYEE1_ID}


async def test_fairness_history_ignores_open_shifts(db_session, week):
    history = await _load_role_history_minutes(db_session, COMPANY_ID, "2026-09-07")
    assert {emp for emp, _ in history} == {EMPLOYEE1_ID}


async def test_check_in_never_matches_an_open_shift(db_session, week, seed_location):
    at = datetime(2026, 8, 31, 9, tzinfo=NY)
    shift, _ = await _match_shift(db_session, COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, at)
    assert shift is not None and shift.id == week["filled"]


async def test_payroll_derivation_skips_open_shifts(db_session, week):
    candidates = await _candidate_shifts(db_session, COMPANY_ID, DAY, DAY, None)
    assert [c.shift.id for c in candidates] == [week["filled"]]


async def test_7shifts_export_skips_open_shifts_and_says_so(client, manager_token, week):
    resp = await client.post(
        "/api/v1/export/7shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"access_token": "unused", "shift_ids": [week["open"]]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "exported": 0, "failed": 0, "errors": [], "open_shifts_skipped": 1,
    }


async def test_jsonl_export_includes_open_shifts_with_no_employee(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/jsonl/{week['schedule_id']}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    rows = {r["shift_id"]: r for r in map(json.loads, resp.text.strip().splitlines())}
    assert rows[week["open"]]["employee_id"] is None
    assert rows[week["open"]]["employee_name"] is None
    assert rows[week["filled"]]["employee_id"] == EMPLOYEE1_ID


async def test_approved_schedule_list_carries_open_shifts(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/approved-schedules?week_start_date={DAY.isoformat()}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    shifts = {s["id"]: s for s in resp.json()[0]["shifts"]}
    assert shifts[week["open"]]["employee_id"] is None
    assert shifts[week["open"]]["employee_name"] is None
