"""Three overridable warnings on an approved-schedule edit (#84 stage 2).

All three apply the edit anyway. A manager routinely knows something the
availability table does not -- a verbal swap, an emergency cover -- and
refusing outright would make the feature useless in exactly the cases it
exists for.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, EmployeeAvailability, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

pytestmark = pytest.mark.asyncio
BASE = "/api/v1/schedules"

# seed_location's timezone (tests/conftest.py). All shift/availability
# fixtures below are built against this so `_shift_local_face` has a real
# zone to convert through.
NY = ZoneInfo("America/New_York")
SHIFT_DATE = date(2026, 8, 31)


async def codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def approved_shift(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> tuple[str, str]:
    """An approved schedule with one shift, local face 13:00-21:00 on
    2026-08-31. Chosen to line up with the overlap/adjacency fixtures below:
    a 12:00-20:00 shift genuinely overlaps it, a 06:00-13:00 shift only
    touches it.
    """
    sched_id = _id()
    shift_id = _id()
    db_session.add(ShiftSchedule(
        id=sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=SHIFT_DATE,
        status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.add(Shift(
        id=shift_id,
        company_id=COMPANY_ID,
        shift_schedule_id=sched_id,
        location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID,
        role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate",
        date=SHIFT_DATE,
        start_time=datetime(2026, 8, 31, 13, 0, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 21, 0, tzinfo=NY),
    ))
    await db_session.commit()
    return sched_id, shift_id


def _wide_availability(employee_id: str) -> EmployeeAvailability:
    """Local wall-clock tagged UTC (the #61 contract), covering all of
    2026-08-31 so this employee's own availability never independently
    causes a no_availability warning."""
    return EmployeeAvailability(
        id=_id(),
        company_id=COMPANY_ID,
        employee_id=employee_id,
        year=2026, month=8, day=31,
        start_time=datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
    )


@pytest_asyncio.fixture
async def employee_without_availability(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> str:
    emp = Employee(id=_id(), company_id=COMPANY_ID, full_name="No Availability")
    db_session.add(emp)
    await db_session.commit()
    return emp.id


@pytest_asyncio.fixture
async def fully_available_employee(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> str:
    emp = Employee(id=_id(), company_id=COMPANY_ID, full_name="Fully Available")
    db_session.add(emp)
    await db_session.flush()
    db_session.add(_wide_availability(emp.id))
    await db_session.commit()
    return emp.id


async def _busy_employee(
    db_session: AsyncSession, name: str, busy_start_hour: int, busy_end_hour: int,
) -> str:
    """An employee, fully available all day, who already has one committed
    shift at this location from busy_start_hour to busy_end_hour local."""
    emp = Employee(id=_id(), company_id=COMPANY_ID, full_name=name)
    db_session.add(emp)
    await db_session.flush()
    db_session.add(_wide_availability(emp.id))

    other_sched_id = _id()
    db_session.add(ShiftSchedule(
        id=other_sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=SHIFT_DATE,
        status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.add(Shift(
        id=_id(),
        company_id=COMPANY_ID,
        shift_schedule_id=other_sched_id,
        location_id=LOCATION_ID,
        employee_id=emp.id,
        role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate",
        date=SHIFT_DATE,
        start_time=datetime(2026, 8, 31, busy_start_hour, 0, tzinfo=NY),
        end_time=datetime(2026, 8, 31, busy_end_hour, 0, tzinfo=NY),
    ))
    await db_session.commit()
    return emp.id


@pytest_asyncio.fixture
async def employee_busy_1200_to_2000(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> str:
    return await _busy_employee(db_session, "Busy Noon to 8pm", 12, 20)


@pytest_asyncio.fixture
async def employee_busy_0600_to_1300(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> str:
    return await _busy_employee(db_session, "Busy 6am to 1pm", 6, 13)


@pytest_asyncio.fixture
async def exported_shift(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> tuple[str, str]:
    sched_id = _id()
    shift_id = _id()
    db_session.add(ShiftSchedule(
        id=sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=SHIFT_DATE,
        status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    db_session.add(Shift(
        id=shift_id,
        company_id=COMPANY_ID,
        shift_schedule_id=sched_id,
        location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID,
        role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate",
        date=SHIFT_DATE,
        start_time=datetime(2026, 8, 31, 13, 0, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 21, 0, tzinfo=NY),
        exported_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sched_id, shift_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_no_availability_warns_but_applies(
    client: AsyncClient, manager_token: str, approved_shift, employee_without_availability,
):
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_without_availability}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1, "warned, not refused"
    assert "no_availability" in await codes(resp)


async def test_already_booked_fires_on_a_partial_overlap(
    client: AsyncClient, manager_token: str, approved_shift, employee_busy_1200_to_2000,
):
    """A 12:00-20:00 shift genuinely conflicts with 13:00-21:00. An
    exact-match test would miss most real conflicts."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_busy_1200_to_2000}]},
    )
    assert resp.status_code == 200
    assert "already_booked" in await codes(resp)


async def test_already_booked_does_not_fire_on_an_adjacent_shift(
    client: AsyncClient, manager_token: str, approved_shift, employee_busy_0600_to_1300,
):
    """Ends exactly when the other begins -- touching, not overlapping."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": employee_busy_0600_to_1300}]},
    )
    assert "already_booked" not in await codes(resp)


async def test_already_exported_warns(
    client: AsyncClient, manager_token: str, exported_shift,
):
    """7shifts now disagrees with the schedule and nothing re-exports."""
    schedule_id, shift_id = exported_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 200
    assert "already_exported" in await codes(resp)


async def test_a_clean_edit_warns_about_nothing(
    client: AsyncClient, manager_token: str, approved_shift, fully_available_employee,
):
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": fully_available_employee}]},
    )
    assert resp.json()["warnings"] == []


async def test_deleting_never_warns_about_availability(
    client: AsyncClient, manager_token: str, approved_shift,
):
    """Removing a shift cannot make anyone unavailable."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert "no_availability" not in await codes(resp)
    assert "already_booked" not in await codes(resp)
