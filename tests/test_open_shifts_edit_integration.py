"""Open shifts on an approved schedule (#174): unassign, create, fill."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, EmployeeCheckIn, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"
NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)


@pytest_asyncio.fixture
async def approved(db_session: AsyncSession, seed_employees: list[Employee]) -> str:
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sid


async def _shift(db: AsyncSession, sid: str, employee_id: str | None, **extra) -> str:
    shift_id = _id()
    db.add(Shift(
        id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sid,
        location_id=LOCATION_ID, employee_id=employee_id,
        role_id=ROLE_FLOOR_ID, role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 17, tzinfo=NY), **extra,
    ))
    await db.commit()
    return shift_id


async def _edit(client, token, sid, edits):
    return await client.put(
        f"{BASE}/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {token}"},
        json={"edits": edits},
    )


async def test_open_true_unassigns_a_shift(client, manager_token, db_session, approved):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []
    db_session.expire_all()
    assert (await db_session.get(Shift, shift_id)).employee_id is None

    week = await client.get(
        f"{BASE}/week/{DAY.isoformat()}?status=approved",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    shift = week.json()[0]["shifts"][0]
    assert shift["employee_id"] is None and shift["employee_name"] is None


async def test_open_true_without_a_shift_id_creates_an_open_shift(
    client, manager_token, db_session, approved,
):
    resp = await _edit(client, manager_token, approved, [{
        "open": True, "role_id": ROLE_FLOOR_ID, "date": DAY.isoformat(),
        "start_time": "2026-08-31T09:00:00-04:00",
        "end_time": "2026-08-31T17:00:00-04:00",
    }])
    assert resp.status_code == 200, resp.text
    rows = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == approved)
    )).scalars().all()
    assert [r.employee_id for r in rows] == [None]


async def test_a_new_shift_without_an_offset_is_read_in_the_location_zone(
    client, manager_token, db_session, approved,
):
    resp = await _edit(client, manager_token, approved, [{
        "open": True, "role_id": ROLE_FLOOR_ID, "date": DAY.isoformat(),
        "start_time": "2026-08-31T09:00:00", "end_time": "2026-08-31T17:00:00",
    }])
    assert resp.status_code == 200, resp.text
    row = (await db_session.execute(
        select(Shift).where(Shift.shift_schedule_id == approved)
    )).scalar_one()
    # 09:00 in New York (EDT, UTC-4) is 13:00 UTC.
    assert row.start_time.astimezone(timezone.utc).hour == 13


async def test_open_with_an_employee_is_rejected(client, manager_token, db_session, approved):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    resp = await _edit(client, manager_token, approved, [
        {"shift_id": shift_id, "open": True, "employee_id": EMPLOYEE1_ID},
    ])
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_edit"


async def test_filling_an_open_shift_assigns_and_warns_as_usual(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(db_session, approved, None)
    resp = await _edit(client, manager_token, approved, [
        {"shift_id": shift_id, "employee_id": EMPLOYEE1_ID},
    ])
    assert resp.status_code == 200, resp.text
    # EMPLOYEE1 has no availability rows in this test.
    assert {w["code"] for w in resp.json()["warnings"]} == {"no_availability"}
    db_session.expire_all()
    assert (await db_session.get(Shift, shift_id)).employee_id == EMPLOYEE1_ID


async def test_unassigning_a_checked_in_shift_is_refused(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(db_session, approved, EMPLOYEE1_ID)
    db_session.add(EmployeeCheckIn(
        id=_id(), company_id=COMPANY_ID, location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID, shift_id=shift_id,
        checked_in_at=datetime.now(timezone.utc), local_date=DAY,
        counter=1, status="matched", minutes_from_start=0,
    ))
    await db_session.commit()
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_unassigning_an_exported_shift_warns_with_a_null_employee(
    client, manager_token, db_session, approved,
):
    shift_id = await _shift(
        db_session, approved, EMPLOYEE1_ID, exported_at=datetime.now(timezone.utc),
    )
    resp = await _edit(client, manager_token, approved, [{"shift_id": shift_id, "open": True}])
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == [{
        "code": "already_exported", "shift_id": shift_id, "employee_id": None,
        "detail": "This shift was exported to 7shifts; the external schedule will now disagree.",
    }]
