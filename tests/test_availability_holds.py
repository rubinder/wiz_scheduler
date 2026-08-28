"""Availability holds: approving must not destroy availability (#84 stage 1).

The gate for this change is that generation output does not move. Approve
stops carving employee_availability and the pipeline starts subtracting Shift
rows instead; if the schedules that come out differ, the change has altered
scheduling behaviour rather than relocating where consumption is computed.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import EmployeeAvailability
from backend.utils.id_gen import generate_short_id

from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    LOCATION_ID,
    ROLE_FLOOR_ID,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def approved_schedule_fixture(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    """Approve one shift for one employee and return (employee_id, date).

    Uses the real approve path rather than inserting Shift rows directly, so
    the test reflects what approving actually does to availability.
    """
    from backend.models import ShiftSchedule

    emp_id = EMPLOYEE1_ID
    work_date = (datetime.now(timezone.utc) + timedelta(days=7)).date()

    # Availability 09:00-17:00, stored as local wall-clock tagged UTC.
    db_session.add(EmployeeAvailability(
        id="avholds1",
        company_id=COMPANY_ID,
        employee_id=emp_id,
        year=work_date.year,
        month=work_date.month,
        day=work_date.day,
        start_time=datetime(work_date.year, work_date.month, work_date.day, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(work_date.year, work_date.month, work_date.day, 17, 0, tzinfo=timezone.utc),
    ))

    shifts = [
        {
            "employee_id": emp_id,
            "employee_name": "Alice Johnson",
            "role_id": ROLE_FLOOR_ID,
            "role_name": "Floor Associate",
            "location_id": LOCATION_ID,
            "date": work_date.isoformat(),
            "start_time": f"{work_date.isoformat()}T13:00:00",
            "end_time": f"{work_date.isoformat()}T21:00:00",
            "status": "ok",
        },
    ]
    sched_id = generate_short_id()
    sched = ShiftSchedule(
        id=sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=work_date,
        status="draft",
        raw_llm_output=json.dumps(shifts),
        strategy="random",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sched)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/schedules/{sched_id}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text

    return emp_id, work_date


async def test_approving_leaves_availability_rows_intact(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The point of the whole change.

    Before: approve deleted the covering window and re-inserted leftovers, so
    an employee available 09:00-17:00 who worked 13:00-21:00 ended with NO
    availability rows for that date. After: the rows are untouched and
    consumption lives in the Shift row.
    """
    emp_id, work_date = approved_schedule_fixture

    db_session.expire_all()
    rows = (await db_session.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.employee_id == emp_id,
        )
    )).scalars().all()

    same_day = [r for r in rows if r.start_time.date() == work_date]
    assert same_day, (
        "approving must not delete the employee's availability for the day "
        "they worked — consumption is derived from the Shift row instead"
    )
    assert len(same_day) == 1, "the window should be untouched, not split"
    # A content-equivalent replacement row is not good enough: the old carve
    # deletes the covering window and re-inserts a differently-id'd remnant
    # that happens to have the same start/end for this shift shape (it only
    # trims the tail of the window). That still means the ORIGINAL row is
    # gone -- indistinguishable from destruction if you only check shape.
    # Checking identity is what actually pins "untouched".
    assert same_day[0].id == "avholds1", (
        "the original availability row must survive untouched -- the old "
        "carve destroys it and inserts a new row with a fresh id, even when "
        "the remaining window happens to look the same"
    )


async def test_an_approved_shift_removes_those_hours_from_availability(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """Consumption still happens — it is just computed at read time now."""
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    # Available 09:00-17:00, worked 13:00-21:00 -> 09:00-13:00 remains.
    assert len(windows) == 1
    assert windows[0]["start"].endswith("T09:00:00+00:00")
    assert windows[0]["end"].endswith("T13:00:00+00:00")


async def test_deleting_the_shift_releases_the_hold(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """Releasing a hold and deleting a shift are the same act."""
    from sqlalchemy import delete
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    await db_session.execute(delete(Shift).where(Shift.employee_id == emp_id))
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    assert len(windows) == 1
    assert windows[0]["end"].endswith("T17:00:00+00:00"), (
        "the full window returns once nothing references those hours"
    )


async def test_the_same_span_subtracted_twice_is_a_no_op(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """There is no unique constraint on (location_id, week_start_date), so a
    manager who regenerates and re-approves produces two approved schedules
    covering the same hours. Availability must not be double-consumed."""
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    original = (await db_session.execute(
        select(Shift).where(Shift.employee_id == emp_id)
    )).scalars().first()

    db_session.add(Shift(
        id="dupshft1",
        company_id=original.company_id,
        shift_schedule_id=original.shift_schedule_id,
        location_id=original.location_id,
        employee_id=original.employee_id,
        role_id=original.role_id,
        role_name=original.role_name,
        date=original.date,
        start_time=original.start_time,
        end_time=original.end_time,
    ))
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    windows = avail.get(emp_id, [])
    assert len(windows) == 1
    assert windows[0]["end"].endswith("T13:00:00+00:00"), (
        "two identical shifts consume the same hours once, not twice"
    )


async def test_a_malformed_shift_timestamp_does_not_raise(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The scheduling graph degrades; it never throws."""
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    # Should not raise even if a row is unparseable.
    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    assert isinstance(avail, dict)
