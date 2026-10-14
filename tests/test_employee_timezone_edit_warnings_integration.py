"""Edit warnings read a timezone employee's availability in the shift's zone."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, EmployeeAvailability, Location, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, REGION_ID, ROLE_FLOOR_ID, _id

BASE = "/api/v1/schedules"
DAY = date(2026, 8, 31)
LA = ZoneInfo("America/Los_Angeles")


async def _la_shift(db: AsyncSession, with_shift: bool = True) -> tuple[str, str]:
    la_id = _id()
    db.add(Location(id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
                    name="LA Store", timezone="America/Los_Angeles"))
    await db.flush()
    sched_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=sched_id, company_id=COMPANY_ID, location_id=la_id,
                         week_start_date=DAY, status="approved",
                         created_at=datetime.now(timezone.utc)))
    await db.flush()
    if with_shift:
        db.add(Shift(id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sched_id,
                     location_id=la_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
                     role_name="Floor Associate", date=DAY,
                     start_time=datetime(2026, 8, 31, 6, 0, tzinfo=LA),
                     end_time=datetime(2026, 8, 31, 10, 0, tzinfo=LA)))
    # Available 09:00-17:00 on the employee's New York clock = 06:00-14:00 LA.
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=2026, month=8, day=31,
        start_time=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    ))
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE1_ID)
                     .values(timezone="America/New_York"))
    await db.commit()
    return sched_id, shift_id


async def test_a_covered_la_shift_does_not_warn(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    sched_id, shift_id = await _la_shift(db_session)
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id,
                         "start_time": "2026-08-31T07:00:00-07:00",
                         "end_time": "2026-08-31T11:00:00-07:00"}]},
    )
    assert resp.status_code == 200, resp.text
    codes = [w["code"] for w in resp.json().get("warnings", [])]
    assert "no_availability" not in codes


async def test_an_uncovered_la_shift_warns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    sched_id, shift_id = await _la_shift(db_session)
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id,
                         "start_time": "2026-08-31T13:00:00-07:00",
                         "end_time": "2026-08-31T17:00:00-07:00"}]},
    )
    assert resp.status_code == 200, resp.text
    codes = [w["code"] for w in resp.json().get("warnings", [])]
    assert "no_availability" in codes


async def _add_new_la_shift(
    client: AsyncClient, token: str, sched_id: str, start: str, end: str,
) -> list[str]:
    """Add a brand-new shift (no shift_id) to the LA schedule; return warning codes."""
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {token}"},
        json={"edits": [{
            "shift_id": None,
            "employee_id": EMPLOYEE1_ID,
            "role_id": ROLE_FLOOR_ID,
            "date": "2026-08-31",
            "start_time": start,
            "end_time": end,
        }]},
    )
    assert resp.status_code == 200, resp.text
    return [w["code"] for w in resp.json().get("warnings", [])]


async def test_a_new_la_shift_inside_converted_availability_does_not_warn(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    """06:00-09:00 LA is inside 09:00-17:00 New York (= 06:00-14:00 LA), but
    outside the unconverted 09:00-17:00 face: only a converted read passes."""
    sched_id, _ = await _la_shift(db_session, with_shift=False)
    codes = await _add_new_la_shift(
        client, manager_token, sched_id,
        "2026-08-31T06:00:00-07:00", "2026-08-31T09:00:00-07:00",
    )
    assert "no_availability" not in codes


async def test_a_new_la_shift_outside_converted_availability_warns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    """15:00-17:00 LA is 18:00-20:00 New York -- after the employee's day,
    though inside the unconverted 09:00-17:00 face."""
    sched_id, _ = await _la_shift(db_session, with_shift=False)
    codes = await _add_new_la_shift(
        client, manager_token, sched_id,
        "2026-08-31T15:00:00-07:00", "2026-08-31T17:00:00-07:00",
    )
    assert "no_availability" in codes
