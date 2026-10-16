"""Approval keeps vacancies as open shifts (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

import json
from datetime import date, datetime, timezone

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability, Shift, ShiftSchedule
from backend.models.employee_role_minutes import EmployeeRoleMinutes
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"


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


async def _approve(client: AsyncClient, token: str, sid: str) -> dict:
    resp = await client.post(
        f"{BASE}/{sid}/approve", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_approve_turns_open_entries_into_open_shifts(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [
        _entry(EMPLOYEE1_ID),
        {**_entry(None, status="OPEN", name=None)},
    ])
    data = await _approve(client, manager_token, sid)
    by_employee = sorted(
        ((s["employee_id"], s["employee_name"]) for s in data["shifts"]),
        key=lambda p: p[0] or "",
    )
    assert by_employee == [(None, None), (EMPLOYEE1_ID, "")]
    rows = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == sid)
    )).scalars().all()
    assert sorted(r.employee_id or "" for r in rows) == ["", EMPLOYEE1_ID]


async def test_approve_turns_a_legacy_vacant_entry_into_an_open_shift(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry("VACANT", name="VACANT-Staffing Shortage")])
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]


async def test_approve_turns_a_legacy_trimmed_entry_into_an_open_shift_not_a_hold(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    """A pre-#174 cap trim could leave status VACANT on an entry that still
    names a real employee. It must become an OPEN shift, never a Shift row
    holding that employee's time."""
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID, status="VACANT")])
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]


async def test_approve_still_skips_conflict_entries(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID, status="CONFLICT")])
    data = await _approve(client, manager_token, sid)
    assert data["shifts"] == []


async def test_an_open_shift_books_no_fairness_minutes(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(None, status="OPEN", name=None)])
    await _approve(client, manager_token, sid)
    count = (await db_session.execute(
        select(func.count()).select_from(EmployeeRoleMinutes)
    )).scalar_one()
    assert count == 0


async def test_draft_save_accepts_an_open_entry(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID)])
    resp = await client.put(
        f"{BASE}/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": [_entry(None, status="OPEN", name=None)]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["shifts"][0]["employee_id"] is None
    await db_session.refresh(await db_session.get(ShiftSchedule, sid))
    stored = json.loads((await db_session.get(ShiftSchedule, sid)).raw_llm_output)
    assert stored[0]["employee_id"] is None and stored[0]["status"] == "OPEN"


async def test_draft_save_normalises_an_empty_employee_to_open(
    client, manager_token, db_session, seed_employees, seed_location, seed_roles,
):
    sid = await _draft(db_session, [_entry(EMPLOYEE1_ID)])
    resp = await client.put(
        f"{BASE}/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": [_entry("", status="ok", name="")]},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["shifts"][0]
    assert saved["employee_id"] is None and saved["status"] == "OPEN"
    data = await _approve(client, manager_token, sid)
    assert [s["employee_id"] for s in data["shifts"]] == [None]
