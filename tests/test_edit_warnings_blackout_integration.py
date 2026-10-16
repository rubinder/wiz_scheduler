"""The no_availability warning now honours blackouts, like the scheduler (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from backend.models import EmployeeAvailability, EmployeeDayBlackout, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")


async def test_assigning_into_a_blackout_warns_no_availability(
    client, manager_token, db_session, seed_employees,
):
    sid, shift_id = _id(), _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 8, 31), status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    db_session.add_all([
        Shift(
            id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=LOCATION_ID, employee_id=None, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=date(2026, 8, 31),
            start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
            end_time=datetime(2026, 8, 31, 17, tzinfo=NY),
        ),
        EmployeeAvailability(
            id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
            year=2026, month=8, day=31,
            start_time=datetime(2026, 8, 31, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
        ),
        EmployeeDayBlackout(
            id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
            day_of_week=0, start_time="12:00", end_time="13:00",
        ),
    ])
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/schedules/{sid}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": EMPLOYEE1_ID}]},
    )
    assert resp.status_code == 200, resp.text
    assert {w["code"] for w in resp.json()["warnings"]} == {"no_availability"}
