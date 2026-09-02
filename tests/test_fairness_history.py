"""Fairness history is derived from approved Shift rows, not a stored aggregate.

#97: `employee_role_minutes` was written once at approval and never adjusted
when an approved schedule was edited, so deleting a shift left its minutes
booked, reassigning one left them with the previous employee, and changing
the times left the original duration standing. Nothing reconciled it, so a
manager who edits often silently degraded their own fairness data.

These tests assert the property that removes the class of bug: the history
is a function of the rows, so an edit cannot leave it stale.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Role, Shift, ShiftSchedule
from backend.models.employee_role_minutes import EmployeeRoleMinutes
from backend.scheduling.graph import _load_role_history_minutes
from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    EMPLOYEE2_ID,
    LOCATION_ID,
    ROLE_FLOOR_ID,
    _id,
)

pytestmark = pytest.mark.asyncio

WEEK = "2026-04-13"          # the week being scheduled
IN_WINDOW = date(2026, 4, 6)  # inside the 3-month look-back
FLOOR = "Floor Associate"


async def _schedule(db: AsyncSession, status: str, when: date = IN_WINDOW) -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=when,
        status=status,
        created_at=datetime.now(timezone.utc),
    ))
    await db.flush()
    return sid


async def _shift(
    db: AsyncSession,
    schedule_id: str,
    *,
    employee_id: str = EMPLOYEE1_ID,
    role_id: str = ROLE_FLOOR_ID,
    on: date = IN_WINDOW,
    hours: float = 8.0,
) -> Shift:
    start = datetime.combine(on, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)
    shift = Shift(
        id=_id(),
        company_id=COMPANY_ID,
        shift_schedule_id=schedule_id,
        location_id=LOCATION_ID,
        employee_id=employee_id,
        role_id=role_id,
        role_name=FLOOR,
        date=on,
        start_time=start,
        end_time=start + timedelta(hours=hours),
    )
    db.add(shift)
    await db.flush()
    return shift


@pytest_asyncio.fixture
async def base(db_session: AsyncSession, seed_employees, seed_roles):
    return db_session


async def _history(db: AsyncSession) -> dict:
    return await _load_role_history_minutes(db, str(COMPANY_ID), WEEK)


# ---------------------------------------------------------------------------
# What counts
# ---------------------------------------------------------------------------

async def test_approved_shifts_count(base: AsyncSession):
    sched = await _schedule(base, "approved")
    await _shift(base, sched, hours=8)
    await base.commit()

    assert (await _history(base))[(str(EMPLOYEE1_ID), FLOOR)] == 480.0


@pytest.mark.parametrize("status", ["draft", "rejected"])
async def test_unapproved_shifts_do_not_count(base: AsyncSession, status: str):
    """A draft is a proposal and a rejected schedule was thrown away.
    Neither is time anybody worked."""
    sched = await _schedule(base, status)
    await _shift(base, sched)
    await base.commit()

    assert (await _history(base)) == {}


async def test_shifts_outside_the_three_month_window_are_excluded(
    base: AsyncSession,
):
    sched = await _schedule(base, "approved")
    await _shift(base, sched, on=date(2025, 6, 1))
    await base.commit()

    assert (await _history(base)) == {}


async def test_minutes_accumulate_across_schedules(base: AsyncSession):
    for hours in (8, 4):
        sched = await _schedule(base, "approved")
        await _shift(base, sched, hours=hours)
    await base.commit()

    assert (await _history(base))[(str(EMPLOYEE1_ID), FLOOR)] == 720.0


# ---------------------------------------------------------------------------
# The bug: edits to an approved schedule
# ---------------------------------------------------------------------------

async def test_deleting_a_shift_releases_its_minutes(base: AsyncSession):
    sched = await _schedule(base, "approved")
    shift = await _shift(base, sched, hours=8)
    await base.commit()
    assert (await _history(base))[(str(EMPLOYEE1_ID), FLOOR)] == 480.0

    await base.execute(delete(Shift).where(Shift.id == shift.id))
    await base.commit()

    # Previously the minutes stayed booked against the employee forever.
    assert (await _history(base)) == {}


async def test_reassigning_a_shift_moves_its_minutes(base: AsyncSession):
    sched = await _schedule(base, "approved")
    shift = await _shift(base, sched, employee_id=EMPLOYEE1_ID, hours=8)
    await base.commit()

    shift.employee_id = EMPLOYEE2_ID
    await base.commit()

    history = await _history(base)
    # Previously: credited to the previous employee, and never to the new one.
    assert (str(EMPLOYEE1_ID), FLOOR) not in history
    assert history[(str(EMPLOYEE2_ID), FLOOR)] == 480.0


async def test_changing_the_times_re_measures_the_shift(base: AsyncSession):
    sched = await _schedule(base, "approved")
    shift = await _shift(base, sched, hours=8)
    await base.commit()

    shift.end_time = shift.start_time + timedelta(hours=3)
    await base.commit()

    # Previously the original 8h duration stood.
    assert (await _history(base))[(str(EMPLOYEE1_ID), FLOOR)] == 180.0


# ---------------------------------------------------------------------------
# Independence from the retained aggregate
# ---------------------------------------------------------------------------

async def test_a_stale_aggregate_row_does_not_influence_history(
    base: AsyncSession,
):
    """employee_role_minutes is still written at approval but is no longer
    what scheduling reads. A drifted row must not leak back in — that is the
    whole point of deriving."""
    base.add(EmployeeRoleMinutes(
        id=_id(),
        company_id=COMPANY_ID,
        employee_id=EMPLOYEE1_ID,
        role_id=ROLE_FLOOR_ID,
        month_start=date(2026, 4, 1),
        total_minutes=99999.0,
    ))
    sched = await _schedule(base, "approved")
    await _shift(base, sched, hours=8)
    await base.commit()

    assert (await _history(base))[(str(EMPLOYEE1_ID), FLOOR)] == 480.0


async def test_a_renamed_role_keeps_its_history(base: AsyncSession):
    """Keyed via the roles table, not Shift.role_name, which is denormalized
    at write time. A rename should follow its history rather than split it."""
    sched = await _schedule(base, "approved")
    await _shift(base, sched, hours=8)
    await base.commit()

    role = await base.get(Role, ROLE_FLOOR_ID)
    role.name = "Floor Staff"
    await base.commit()

    history = await _history(base)
    assert history[(str(EMPLOYEE1_ID), "Floor Staff")] == 480.0
    assert (str(EMPLOYEE1_ID), FLOOR) not in history
