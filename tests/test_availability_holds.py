"""Availability holds: approving must not destroy availability (#84 stage 1).

The gate for this change is that generation output does not move. Approve
stops carving employee_availability and the pipeline starts subtracting Shift
rows instead; if the schedules that come out differ, the change has altered
scheduling behaviour rather than relocating where consumption is computed.
"""

import json
from datetime import date, datetime, timedelta, timezone

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
    EMPLOYEE2_ID,
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


async def test_committed_shift_face_is_read_via_the_shift_location_not_the_utc_normalised_instant(
):
    """Postgres-shaped regression (round-1 review, Critical 1).

    Shift.start_time/end_time are timestamptz columns -- true instants.
    local_scheduler.py emits an aware datetime carrying the location's real
    offset (e.g. "09:00:00-04:00"); approve parses that and POSTGRES
    NORMALISES IT TO UTC ON STORAGE, so a read gives back "13:00:00+00:00".
    Stripping that tag (what _wall_clock does, correctly, for availability)
    reads the WRONG face here -- 13:00, not the 09:00 the shift actually
    covers.

    This deliberately does NOT round-trip through db_session/SQLite. SQLite's
    DateTime(timezone=True) columns drop tzinfo on read but PRESERVE the
    local face (no normalisation happens); Postgres preserves the tag and
    MOVES the face. SQLite is therefore the one storage engine where the old,
    buggy tag-stripping code reads correctly -- no test that goes through it
    can catch this bug. Constructing the row directly reproduces exactly what
    a real Postgres read returns: an aware datetime already normalised to
    UTC.
    """
    from backend.models import Location, Shift
    from backend.scheduling.graph import _shift_local_face, _subtract_committed_shifts

    location = Location(
        id="locpgtst", company_id=COMPANY_ID, region_id="regnpgts",
        name="Postgres-shaped NYC Store", timezone="America/New_York",
    )
    shift = Shift(
        id="shftpgts", company_id=COMPANY_ID, shift_schedule_id="schdpgts",
        location_id="locpgtst", employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=date(2026, 9, 4),
        # The real shift is 09:00-17:00 local (America/New_York, -04:00 in
        # September) -- this is what Postgres actually hands back for it.
        start_time=datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc),
    )

    face = _shift_local_face(shift, location)
    assert face is not None, "a valid shift + location must resolve a face"
    start, end = face
    assert (start, end) == (
        datetime(2026, 9, 4, 9, 0), datetime(2026, 9, 4, 17, 0),
    ), "the face must be the shift's LOCAL hours, not the UTC-normalised instant"
    assert start.tzinfo is None and end.tzinfo is None

    # End to end: an availability window covering exactly this shift must be
    # consumed to nothing -- not left with a phantom 09:00-13:00 remainder,
    # which is what reading the wrong (UTC) face produces.
    emp_avail_map = {
        EMPLOYEE1_ID: [{
            "start": "2026-09-04T09:00:00+00:00",
            "end": "2026-09-04T17:00:00+00:00",
        }],
    }
    result = _subtract_committed_shifts(emp_avail_map, {EMPLOYEE1_ID: [(start, end)]})
    assert result[EMPLOYEE1_ID] == [], (
        "a shift covering the full local window must consume it entirely; "
        "a phantom leftover means the wrong wall-clock face was read"
    )


async def test_a_malformed_shift_timestamp_does_not_raise(
    db_session: AsyncSession, approved_schedule_fixture,
):
    """The scheduling graph degrades; it never throws.

    A row this code cannot safely convert -- e.g. a location whose timezone
    has been corrupted into something zoneinfo cannot resolve -- must be
    skipped like any other unreadable row, not raised on. Because it is
    skipped, it must not hold the employee's time either: the full window
    comes back exactly as it does when the shift is deleted (see
    test_deleting_the_shift_releases_the_hold).

    A truly malformed *string* in the start_time/end_time columns can't be
    used to exercise this: SQLite's own DATETIME parser raises while
    materialising the row, before this module's code ever sees the value (a
    real, pre-existing characteristic of the plain DateTime column type,
    unrelated to this change). An unresolvable location.timezone is the
    actually-reachable way a malformed shift hits the
    _shift_local_face try/except in practice.
    """
    from sqlalchemy import text
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture

    await db_session.execute(
        text("UPDATE locations SET timezone = :bad WHERE id = :location_id"),
        {"bad": "Not/AZone", "location_id": LOCATION_ID},
    )
    await db_session.commit()
    # The session's identity map holds the Location object loaded earlier by
    # the fixture with its original, valid timezone; the raw UPDATE above
    # bypassed the ORM, so without expiring it the next SELECT would still
    # hand back the stale in-memory value instead of the corrupted one.
    db_session.expire_all()

    # Should not raise even though the shift's location can no longer be
    # resolved to a real timezone.
    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    assert isinstance(avail, dict)

    windows = avail.get(emp_id, [])
    assert len(windows) == 1
    assert windows[0]["end"].endswith("T17:00:00+00:00"), (
        "a shift that cannot be safely read must not hold the employee's "
        "time -- the full window returns, same as when the shift is deleted"
    )


@pytest_asyncio.fixture
async def second_employee_id(
    db_session: AsyncSession, approved_schedule_fixture, seed_employees,
) -> str:
    """A second employee in the same company as `approved_schedule_fixture`'s
    employee, available 09:00-17:00 on that same `work_date`.

    `seed_employees` already creates EMPLOYEE2_ID ("Bob Smith") in
    COMPANY_ID; this just gives them the matching availability window so a
    reassigned shift has something to consume.
    """
    _, work_date = approved_schedule_fixture
    db_session.add(EmployeeAvailability(
        id="avholds2",
        company_id=COMPANY_ID,
        employee_id=EMPLOYEE2_ID,
        year=work_date.year,
        month=work_date.month,
        day=work_date.day,
        start_time=datetime(work_date.year, work_date.month, work_date.day, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(work_date.year, work_date.month, work_date.day, 17, 0, tzinfo=timezone.utc),
    ))
    await db_session.commit()
    return EMPLOYEE2_ID


async def test_reassigning_moves_the_hold(
    db_session: AsyncSession, approved_schedule_fixture, second_employee_id,
):
    """Changing employee_id frees one employee and commits the other, with no
    explicit release step -- the hold IS the row.

    approved_schedule_fixture's shift is 13:00-21:00 for EMPLOYEE1_ID.
    Reassigning it to second_employee_id (also available 09:00-17:00) must:
    fully restore EMPLOYEE1_ID's window (nothing references those hours any
    more), and carve the same 13:00-17:00 overlap out of second_employee_id's
    window instead -- no separate "release" call, just the row changing.
    """
    from backend.models import Shift
    from backend.scheduling.graph import _load_employee_availability

    emp_id, work_date = approved_schedule_fixture
    shift = (await db_session.execute(
        select(Shift).where(Shift.employee_id == emp_id)
    )).scalars().first()
    shift.employee_id = second_employee_id
    await db_session.commit()

    avail = await _load_employee_availability(
        db_session, company_id=COMPANY_ID, week_start_date=work_date.isoformat(), num_days=7,
    )
    assert avail[emp_id][0]["end"].endswith("T17:00:00+00:00"), "original employee freed"
    assert avail[second_employee_id][0]["end"].endswith("T13:00:00+00:00"), "new employee committed"
