"""Derivation: approved shift + the arrival that gates it -> one payable row.

Shift fixtures are built with tzinfo=timezone.utc directly, exactly as
tests/test_check_in_service.py does, and never from an offset-bearing ISO
string — SQLite strips tzinfo on round-trip and would silently shift them.
Assertions here are about counts, statuses and constraints; the timezone rule
itself is asserted directly in tests/test_time_entries_service.py.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Role, Shift,
    ShiftSchedule, TimeEntry, User,
)
from backend.models.employee_check_in import (
    CHECK_IN_DUPLICATE, CHECK_IN_MATCHED, CHECK_IN_NO_SHIFT,
    CHECK_IN_WRONG_LOCATION,
)
from backend.models.time_entry import TIME_ENTRY_CHECKED_IN
from backend.services.time_entries import (
    derive_time_entries, list_exceptions, list_time_entries,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio

# A fixed reference point in the past so nothing here depends on the hour the
# suite happens to run. "Today" is UTC, per CLAUDE.md.
TODAY = datetime.now(timezone.utc).date()


async def _tenant(db: AsyncSession, tz: str = "America/New_York") -> SimpleNamespace:
    company_id, region_id = _id(), _id()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}"))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, role_id, employee_id, user_id = _id(), _id(), _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone=tz))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(Employee(id=employee_id, company_id=company_id,
                    full_name="Dana Okafor", email=f"{employee_id}@example.com",
                    location_ids=[location_id]))
    db.add(User(id=user_id, company_id=company_id, email=f"{user_id}@example.com",
                hashed_password="x", full_name="M", user_role="manager"))
    await db.commit()
    return SimpleNamespace(
        company_id=company_id, location_id=location_id, role_id=role_id,
        role_name=role.name, employee_id=employee_id, user_id=user_id,
    )


async def _shift(
    db: AsyncSession, t: SimpleNamespace, start: datetime,
    hours: int = 8, status: str = "approved",
) -> str:
    schedule_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=schedule_id, company_id=t.company_id,
                         location_id=t.location_id,
                         week_start_date=start.date(), status=status))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=t.company_id,
                 shift_schedule_id=schedule_id, location_id=t.location_id,
                 employee_id=t.employee_id, role_id=t.role_id,
                 role_name=t.role_name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=hours)))
    await db.commit()
    return shift_id


async def _check_in(
    db: AsyncSession, t: SimpleNamespace, shift_id: str | None,
    at: datetime, status: str = CHECK_IN_MATCHED, minutes: int | None = -4,
    counter: int = 0,
) -> str:
    row_id = _id()
    db.add(EmployeeCheckIn(
        id=row_id, company_id=t.company_id, location_id=t.location_id,
        employee_id=t.employee_id, shift_id=shift_id, checked_in_at=at,
        local_date=at.date(), counter=counter, status=status,
        minutes_from_start=minutes,
    ))
    await db.commit()
    return row_id


def _yesterday_at(hour: int) -> datetime:
    """A UTC instant yesterday. Yesterday, not today, so the shift has always
    started by the time the test runs."""
    return datetime.combine(
        TODAY - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    ).replace(hour=hour)


async def test_a_matched_check_in_produces_one_entry(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    check_in_id = await _check_in(db_session, t, shift_id,
                                  start - timedelta(minutes=4))

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 1
    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.source == TIME_ENTRY_CHECKED_IN
    assert entry.shift_id == shift_id
    assert entry.check_in_id == check_in_id
    assert entry.lateness_minutes == -4
    assert entry.paid_minutes == 480
    assert entry.role_name == t.role_name
    assert entry.approved_at is None


async def test_a_duplicate_check_in_is_still_evidence_of_arrival(
    db_session: AsyncSession
):
    """`duplicate` answers a punctuality question, not an attendance one. On a
    split-shift day the second shift's genuine arrival is recorded as
    duplicate; refusing it would push a real shift into the exception queue."""
    t = await _tenant(db_session)
    start = _yesterday_at(17)
    shift_id = await _shift(db_session, t, start, hours=4)
    await _check_in(db_session, t, shift_id, start, status=CHECK_IN_DUPLICATE,
                    minutes=0)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 1


async def test_the_earliest_scan_owns_the_lateness_figure(
    db_session: AsyncSession
):
    """A matched row always wins over a later duplicate for the same shift, so
    the 17:00 re-scan record_check_in keeps out of the punctuality figure never
    becomes the lateness figure here either."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start - timedelta(minutes=4),
                    status=CHECK_IN_MATCHED, minutes=-4, counter=0)
    await _check_in(db_session, t, shift_id, start + timedelta(hours=8),
                    status=CHECK_IN_DUPLICATE, minutes=480, counter=1)

    await derive_time_entries(db_session, t.company_id,
                              TODAY - timedelta(days=7), TODAY)

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.lateness_minutes == -4


@pytest.mark.parametrize("status", [CHECK_IN_NO_SHIFT, CHECK_IN_WRONG_LOCATION])
async def test_an_unmatched_check_in_gates_nothing(
    db_session: AsyncSession, status: str
):
    """_match_shift leaves shift_id NULL on those rows, so the join cannot see
    them and the shift is an exception instead."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    await _shift(db_session, t, start)
    await _check_in(db_session, t, None, start, status=status, minutes=None)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0
    assert result.exception_count == 1


async def test_a_draft_schedule_pays_nothing(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start, status="draft")
    await _check_in(db_session, t, shift_id, start)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0
    assert result.existing == 0
    assert result.exception_count == 0


async def test_a_future_shift_is_neither_an_entry_nor_an_exception(
    db_session: AsyncSession
):
    t = await _tenant(db_session)
    start = datetime.now(timezone.utc) + timedelta(days=2)
    await _shift(db_session, t, start)

    result = await derive_time_entries(
        db_session, t.company_id, TODAY, TODAY + timedelta(days=7)
    )

    assert result.created == 0
    assert result.exception_count == 0
    assert await list_exceptions(
        db_session, t.company_id, TODAY, TODAY + timedelta(days=7)
    ) == []


async def test_deriving_twice_creates_nothing_the_second_time(
    db_session: AsyncSession
):
    """The page calls derive on every load; it has to be free the second
    time."""
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start)
    window = (TODAY - timedelta(days=7), TODAY)

    first = await derive_time_entries(db_session, t.company_id, *window)
    second = await derive_time_entries(db_session, t.company_id, *window)

    assert first.created == 1 and first.existing == 0
    assert second.created == 0 and second.existing == 1
    assert len((await db_session.execute(select(TimeEntry))).scalars().all()) == 1


async def test_another_companys_shift_is_invisible(db_session: AsyncSession):
    mine = await _tenant(db_session)
    theirs = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, theirs, start)
    await _check_in(db_session, theirs, shift_id, start)

    result = await derive_time_entries(
        db_session, mine.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert result.created == 0


async def test_a_shift_with_no_check_in_lands_in_the_exception_queue(
    db_session: AsyncSession
):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)

    rows = await list_exceptions(
        db_session, t.company_id, TODAY - timedelta(days=7), TODAY
    )

    assert [r.shift_id for r in rows] == [shift_id]
    assert rows[0].employee_name == "Dana Okafor"
    assert rows[0].paid_minutes == 480
    assert rows[0].role_name == t.role_name


async def test_a_midnight_crossing_shift_pays_on_its_start_date(
    db_session: AsyncSession
):
    """22:00 local Sunday -> 06:00 Monday is paid ENTIRELY on Sunday: included
    in full by a range ending Sunday, excluded entirely by one starting
    Monday. The location is America/New_York, so 22:00 local is 02:00 UTC the
    NEXT day — which is exactly the case a naive .date() gets wrong."""
    t = await _tenant(db_session)
    # 02:00 UTC on `local_start_date + 1` == 22:00 the previous evening in NY
    # during EST. Pick a fixed winter date in the past so the offset is -05:00
    # regardless of when the suite runs.
    local_start = date(2026, 1, 4)          # a Sunday
    start = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)   # 22:00 EST Jan 4
    shift_id = await _shift(db_session, t, start, hours=8)
    await _check_in(db_session, t, shift_id, start)

    included = await derive_time_entries(
        db_session, t.company_id, local_start, local_start
    )
    assert included.created == 1

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.pay_date == local_start

    rows = await list_time_entries(
        db_session, t.company_id, local_start + timedelta(days=1),
        local_start + timedelta(days=7),
    )
    assert rows == []


async def test_listing_filters_by_approval_state(db_session: AsyncSession):
    t = await _tenant(db_session)
    start = _yesterday_at(9)
    shift_id = await _shift(db_session, t, start)
    await _check_in(db_session, t, shift_id, start)
    window = (TODAY - timedelta(days=7), TODAY)
    await derive_time_entries(db_session, t.company_id, *window)

    assert len(await list_time_entries(
        db_session, t.company_id, *window, approved=False)) == 1
    assert await list_time_entries(
        db_session, t.company_id, *window, approved=True) == []
