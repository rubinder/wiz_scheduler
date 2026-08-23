"""The demo roster's seeded availability.

employee_availability stores one row per CONCRETE date — there is no
recurrence rule — so "available 9-5 every day" has to be materialized date by
date. _seed_availability writes that grid for the whole demo roster across
AVAILABILITY_HORIZON_DAYS.

The timezone assertion here is the load-bearing one. Windows are stored as
local wall-clock TAGGED UTC, matching what the availability endpoint writes
when the UI posts a naive "2026-08-24T09:00". The scheduling validator
compares availability against shift times as naive HH:MM with no conversion
(backend/scheduling/nodes.py), so a window tagged with a real offset reads
back off-by-the-offset on Postgres and rejects every shift.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, EmployeeAvailability
from backend.models.ownership_group import OwnershipGroup
from backend.seed import (
    AVAILABILITY_END_HOUR,
    AVAILABILITY_HORIZON_DAYS,
    AVAILABILITY_START_HOUR,
    COMPANY_ID,
    DEMO_EMPLOYEE_COUNT,
    EMPLOYEE_IDS,
    OWNERSHIP_GROUP_ID,
    _availability_id,
    _seed_availability,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def demo_roster(db_session: AsyncSession) -> None:
    """The demo company with its 21 employees, no availability yet."""
    db_session.add(OwnershipGroup(id=OWNERSHIP_GROUP_ID, name="Acme Corp Group"))
    await db_session.flush()
    db_session.add(Company(id=COMPANY_ID, name="Acme Corp", slug="acme-corp",
                           ownership_group_id=OWNERSHIP_GROUP_ID))
    await db_session.flush()
    for emp_id in EMPLOYEE_IDS:
        db_session.add(Employee(
            id=emp_id, company_id=COMPANY_ID, full_name=emp_id,
            email=f"{emp_id}@example.com", location_ids=[],
        ))
    await db_session.commit()


async def _windows(db: AsyncSession, emp_id: str) -> list[EmployeeAvailability]:
    return list((await db.execute(
        select(EmployeeAvailability)
        .where(EmployeeAvailability.employee_id == emp_id)
        .order_by(EmployeeAvailability.start_time)
    )).scalars().all())


async def test_every_employee_gets_a_window_for_every_day(
    db_session: AsyncSession, demo_roster
):
    await _seed_availability(db_session)
    await db_session.commit()

    total = (await db_session.execute(
        select(func.count()).select_from(EmployeeAvailability)
        .where(EmployeeAvailability.company_id == COMPANY_ID)
    )).scalar_one()

    assert total == DEMO_EMPLOYEE_COUNT * AVAILABILITY_HORIZON_DAYS
    for emp_id in EMPLOYEE_IDS:
        assert len(await _windows(db_session, emp_id)) == AVAILABILITY_HORIZON_DAYS


async def test_windows_cover_all_seven_weekdays(
    db_session: AsyncSession, demo_roster
):
    """The previous seed covered Mon-Fri only, so a weekend template could
    never be filled."""
    await _seed_availability(db_session)
    await db_session.commit()

    windows = await _windows(db_session, EMPLOYEE_IDS[0])
    weekdays = {date(w.year, w.month, w.day).weekday() for w in windows[:14]}

    assert weekdays == {0, 1, 2, 3, 4, 5, 6}


async def test_windows_run_nine_to_five_as_local_wall_clock(
    db_session: AsyncSession, demo_roster
):
    """09:00-17:00 with NO offset applied — see the module docstring."""
    await _seed_availability(db_session)
    await db_session.commit()

    for w in (await _windows(db_session, EMPLOYEE_IDS[0]))[:14]:
        assert w.start_time.hour == AVAILABILITY_START_HOUR == 9
        assert w.end_time.hour == AVAILABILITY_END_HOUR == 17
        assert w.start_time.minute == 0 and w.end_time.minute == 0
        # No NON-ZERO offset is what makes HH:MM survive the round trip.
        # SQLite drops tzinfo and hands back a naive value (None); Postgres
        # keeps it at UTC (zero). Either is fine — anything else is the bug.
        for offset in (w.start_time.utcoffset(), w.end_time.utcoffset()):
            assert offset in (None, timedelta(0)), (
                f"window carries a real offset ({offset}); on Postgres it "
                f"normalizes to UTC and reads back shifted off the shift slot"
            )


async def test_date_columns_agree_with_the_timestamps(
    db_session: AsyncSession, demo_roster
):
    """year/month/day is what the manager availability view filters on."""
    await _seed_availability(db_session)
    await db_session.commit()

    for w in (await _windows(db_session, EMPLOYEE_IDS[0]))[:14]:
        assert (w.year, w.month, w.day) == (
            w.start_time.year, w.start_time.month, w.start_time.day
        )


async def test_horizon_starts_today(db_session: AsyncSession, demo_roster):
    """A visitor generating for the current week must find availability."""
    await _seed_availability(db_session)
    await db_session.commit()

    windows = await _windows(db_session, EMPLOYEE_IDS[0])
    first = date(windows[0].year, windows[0].month, windows[0].day)
    last = date(windows[-1].year, windows[-1].month, windows[-1].day)

    assert first == date.today()
    assert last == date.today() + timedelta(days=AVAILABILITY_HORIZON_DAYS - 1)


async def test_reseeding_replaces_rather_than_duplicates(
    db_session: AsyncSession, demo_roster
):
    """Seeding is run repeatedly; a second run must not double the windows."""
    await _seed_availability(db_session)
    await db_session.commit()
    await _seed_availability(db_session)
    await db_session.commit()

    total = (await db_session.execute(
        select(func.count()).select_from(EmployeeAvailability)
        .where(EmployeeAvailability.company_id == COMPANY_ID)
    )).scalar_one()

    assert total == DEMO_EMPLOYEE_COUNT * AVAILABILITY_HORIZON_DAYS


async def test_reseeding_clears_windows_in_a_foreign_id_scheme(
    db_session: AsyncSession, demo_roster
):
    """The old seed used a different id format, so ON CONFLICT would have left
    those rows behind as duplicate windows on the same days."""
    db_session.add(EmployeeAvailability(
        id="av000000", company_id=COMPANY_ID, employee_id=EMPLOYEE_IDS[0],
        year=2026, month=8, day=17,
        # 14:00-22:00: what a -05:00-tagged 9-5 window becomes once Postgres
        # normalizes it to UTC. The bug this replaced, kept as the fixture.
        start_time=datetime(2026, 8, 17, 14, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 17, 22, tzinfo=timezone.utc),
    ))
    await db_session.commit()

    await _seed_availability(db_session)
    await db_session.commit()

    assert await db_session.get(EmployeeAvailability, "av000000") is None


async def test_availability_ids_are_unique_across_the_whole_grid():
    """Ids are packed into an 8-char column; a collision silently drops a row."""
    start = date.today()
    ids = {
        _availability_id(emp_idx, start + timedelta(days=offset))
        for emp_idx in range(DEMO_EMPLOYEE_COUNT)
        for offset in range(AVAILABILITY_HORIZON_DAYS)
    }

    assert len(ids) == DEMO_EMPLOYEE_COUNT * AVAILABILITY_HORIZON_DAYS
    assert all(len(i) == 8 for i in ids)


async def test_availability_id_is_stable_for_a_given_employee_day():
    """Keyed on the date ordinal, not an offset from today, so a later re-seed
    rewrites overlapping days instead of duplicating them."""
    day = date(2027, 3, 14)
    assert _availability_id(7, day) == _availability_id(7, day)
    assert _availability_id(7, day) != _availability_id(8, day)
    assert _availability_id(7, day) != _availability_id(7, day + timedelta(days=1))


async def test_only_the_demo_companys_rows_are_cleared(
    db_session: AsyncSession, demo_roster
):
    """The reset is scoped by company_id — a real tenant's availability in the
    same table must survive."""
    other_company, other_emp = "othrcomp", "othremp1"
    db_session.add(Company(id=other_company, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(Employee(
        id=other_emp, company_id=other_company, full_name="Someone",
        email="someone@example.com", location_ids=[],
    ))
    await db_session.flush()
    db_session.add(EmployeeAvailability(
        id="otheravl", company_id=other_company, employee_id=other_emp,
        year=2026, month=8, day=17,
        start_time=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 17, 17, tzinfo=timezone.utc),
    ))
    await db_session.commit()

    await _seed_availability(db_session)
    await db_session.commit()

    assert await db_session.get(EmployeeAvailability, "otheravl") is not None
