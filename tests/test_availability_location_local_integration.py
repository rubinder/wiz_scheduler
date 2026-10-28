"""Availability is always the location's local wall clock (#171).

Whoever enters it (the employee, a manager, a 7shifts/Deputy import) is
assumed to know the location's zone, so nothing converts it per employee:
the scheduler, the candidate ranking and the edit warnings all read it as
the location's local time. (Employees had an optional timezone from #170
that did convert; #171 dropped it.)

Fixture: a Los Angeles location (the seeded employee's other test location
is New York) and Monday availability 09:00-17:00 -- which means 09:00-17:00
in Los Angeles.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeAvailability, EmployeeRole, Location, Shift, ShiftSchedule,
)
from backend.scheduling.graph import _load_initial_state
from backend.scheduling.nodes import load_location_context
from backend.services.availability_check import is_available, window_dict
from backend.services.candidates import rank_candidates
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, REGION_ID, ROLE_FLOOR_ID, _id

DAY = date(2026, 8, 31)  # a Monday; Los Angeles is on -07:00
LA = ZoneInfo("America/Los_Angeles")
BASE = "/api/v1/schedules"


async def _seed(db: AsyncSession) -> str:
    """LA location; EMPLOYEE1 works there, Floor role,
    available Mon 09:00-17:00 (location-local, tagged UTC per #61)."""
    la_id = _id()
    db.add(Location(id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
                    name="LA Store", timezone="America/Los_Angeles"))
    await db.flush()
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE1_ID).values(
        location_ids=[la_id],
    ))
    db.add(EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
                        role_id=ROLE_FLOOR_ID, skill_level=3))
    db.add(EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=EMPLOYEE1_ID,
        year=DAY.year, month=DAY.month, day=DAY.day,
        start_time=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    ))
    await db.commit()
    return la_id


async def _approved_la_schedule(db: AsyncSession, la_id: str, *, start_h: int, end_h: int) -> tuple[str, str]:
    sched_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=sched_id, company_id=COMPANY_ID, location_id=la_id,
                         week_start_date=DAY, status="approved",
                         created_at=datetime.now(timezone.utc)))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sched_id,
                 location_id=la_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
                 role_name="Floor Associate", date=DAY,
                 start_time=datetime(2026, 8, 31, start_h, 0, tzinfo=LA),
                 end_time=datetime(2026, 8, 31, end_h, 0, tzinfo=LA)))
    await db.commit()
    return sched_id, shift_id


def _faces(windows: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(w["start"][11:16], w["end"][11:16]) for w in windows]


# -- (a) the scheduler's loader and per-location context --------------------

async def test_scheduler_sees_availability_as_location_local(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    la_id = await _seed(db_session)
    state = await _load_initial_state(COMPANY_ID, DAY.isoformat(), db_session)

    emp = next(e for e in state["employees"] if e["id"] == EMPLOYEE1_ID)
    assert _faces(emp["available_windows"]) == [("09:00", "17:00")]

    la_index = next(i for i, loc in enumerate(state["locations"]) if loc["id"] == la_id)
    ctx = load_location_context({
        **state, "current_location_index": la_index, "availability_draft": {},
    })
    emp_ctx = next(e for e in ctx["current_employees"] if e["id"] == EMPLOYEE1_ID)
    # Not converted New York -> Los Angeles (which would read 06:00-14:00).
    assert _faces(emp_ctx["available_windows"]) == [("09:00", "17:00")]


async def test_committed_shift_is_carved_on_the_location_clock(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    la_id = await _seed(db_session)
    # An approved 06:00-10:00 LA shift holds 06:00-10:00 of the LA-local window.
    await _approved_la_schedule(db_session, la_id, start_h=6, end_h=10)
    state = await _load_initial_state(COMPANY_ID, DAY.isoformat(), db_session)
    emp = next(e for e in state["employees"] if e["id"] == EMPLOYEE1_ID)
    assert _faces(emp["available_windows"]) == [("10:00", "17:00")]


# -- (b) the one definition of "available" and the candidate ranking --------

async def test_is_available_reads_windows_as_location_local(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    await _seed(db_session)
    rows = (await db_session.execute(
        select(EmployeeAvailability).where(EmployeeAvailability.employee_id == EMPLOYEE1_ID)
    )).scalars().all()
    windows = [window_dict(r) for r in rows]

    def check(start_h: int, end_h: int) -> bool:
        return is_available(
            windows=windows, other_spans=[], blackouts=[],
            span_start=datetime(2026, 8, 31, start_h), span_end=datetime(2026, 8, 31, end_h),
        )

    assert check(9, 17)
    assert not check(6, 14)


async def test_candidate_ranking_reads_location_local(
    db_session: AsyncSession, seed_employees, seed_location, seed_roles,
):
    la_id = await _seed(db_session)
    location = await db_session.get(Location, la_id)

    async def alice(start_h: int, end_h: int):
        ranked = await rank_candidates(
            db_session, COMPANY_ID, location, ROLE_FLOOR_ID,
            datetime(2026, 8, 31, start_h), datetime(2026, 8, 31, end_h),
        )
        return next(c for c in ranked if c.employee_id == EMPLOYEE1_ID)

    assert (await alice(9, 17)).available is True
    assert (await alice(6, 14)).available is False


# -- (c) the approved-schedule no_availability edit warning -----------------

async def _edit_codes(client: AsyncClient, token: str, sched_id: str, shift_id: str,
                      start: str, end: str) -> list[str]:
    resp = await client.put(
        f"{BASE}/{sched_id}/approved-shifts",
        headers={"Authorization": f"Bearer {token}"},
        json={"edits": [{"shift_id": shift_id, "start_time": start, "end_time": end}]},
    )
    assert resp.status_code == 200, resp.text
    return [w["code"] for w in resp.json().get("warnings", [])]


async def test_edit_inside_location_local_availability_does_not_warn(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    la_id = await _seed(db_session)
    sched_id, shift_id = await _approved_la_schedule(db_session, la_id, start_h=10, end_h=12)
    codes = await _edit_codes(client, manager_token, sched_id, shift_id,
                              "2026-08-31T09:00:00-07:00", "2026-08-31T17:00:00-07:00")
    assert "no_availability" not in codes


async def test_edit_outside_location_local_availability_warns(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_employees, seed_location, seed_roles,
):
    la_id = await _seed(db_session)
    sched_id, shift_id = await _approved_la_schedule(db_session, la_id, start_h=10, end_h=12)
    codes = await _edit_codes(client, manager_token, sched_id, shift_id,
                              "2026-08-31T06:00:00-07:00", "2026-08-31T14:00:00-07:00")
    assert "no_availability" in codes
