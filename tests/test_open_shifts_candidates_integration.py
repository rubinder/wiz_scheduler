"""Ranking who fits an open shift (#174)."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeAvailability, EmployeeDayBlackout, EmployeeRole,
    Location, Shift, ShiftSchedule,
)
from backend.services.candidates import rank_candidates, slot_span, tier_for
from tests.conftest import COMPANY_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

NY = ZoneInfo("America/New_York")
DAY = date(2026, 8, 31)  # a Monday
SPAN = (datetime(2026, 8, 31, 9), datetime(2026, 8, 31, 17))


def test_tiers_follow_the_agreed_order():
    assert tier_for(available=True, has_role=True, at_location=True) == 1
    assert tier_for(available=False, has_role=True, at_location=True) == 2
    assert tier_for(available=True, has_role=False, at_location=True) == 3
    assert tier_for(available=False, has_role=False, at_location=True) == 4
    assert tier_for(available=True, has_role=True, at_location=False) == 4


def test_slot_span_rolls_an_overnight_end_to_the_next_day():
    start, end = slot_span(DAY, "22:00", "06:00")
    assert start == datetime(2026, 8, 31, 22)
    assert end == datetime(2026, 9, 1, 6)


def _availability(emp_id: str, start_h: int = 0, end_h: int = 23) -> EmployeeAvailability:
    """Local wall-clock tagged UTC (#61)."""
    return EmployeeAvailability(
        id=_id(), company_id=COMPANY_ID, employee_id=emp_id,
        year=2026, month=8, day=31,
        start_time=datetime(2026, 8, 31, start_h, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, end_h, 59 if end_h == 23 else 0, tzinfo=timezone.utc),
    )


async def _employee(
    db: AsyncSession, name: str, *, at_location=True, role=True,
    available=True, timezone_name: str | None = None,
) -> str:
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name=name,
        location_ids=[LOCATION_ID] if at_location else [],
        timezone=timezone_name,
    )
    db.add(emp)
    await db.flush()
    if role:
        db.add(EmployeeRole(
            id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
            role_id=ROLE_FLOOR_ID, skill_level=3,
        ))
    if available:
        db.add(_availability(emp.id))
    await db.flush()
    return emp.id


@pytest_asyncio.fixture
async def roster(db_session: AsyncSession, seed_location: Location, seed_roles) -> dict:
    ids = {
        "ana": await _employee(db_session, "Ana Best"),
        "ben": await _employee(db_session, "Ben Unavailable", available=False),
        "cara": await _employee(db_session, "Cara NoRole", role=False),
        "dan": await _employee(db_session, "Dan Elsewhere", at_location=False),
        "eve": await _employee(db_session, "Eve Blackout"),
        "finn": await _employee(db_session, "Finn Booked"),
    }
    db_session.add(EmployeeDayBlackout(
        id=_id(), company_id=COMPANY_ID, employee_id=ids["eve"],
        day_of_week=0, start_time="12:00", end_time="13:00",
    ))
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    ids["finn_shift"] = _id()
    db_session.add(Shift(
        id=ids["finn_shift"], company_id=COMPANY_ID, shift_schedule_id=sid,
        location_id=LOCATION_ID, employee_id=ids["finn"], role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=DAY,
        start_time=datetime(2026, 8, 31, 10, tzinfo=NY),
        end_time=datetime(2026, 8, 31, 14, tzinfo=NY),
    ))
    await db_session.commit()
    return ids


async def _rank(db, exclude=None):
    location = await db.get(Location, LOCATION_ID)
    return await rank_candidates(
        db, COMPANY_ID, location, ROLE_FLOOR_ID, *SPAN, exclude_shift_id=exclude,
    )


async def test_each_tier_gets_the_right_people_in_order(db_session, roster):
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [
        ("Ana Best", 1),
        ("Ben Unavailable", 2),
        ("Eve Blackout", 2),
        ("Finn Booked", 2),
        ("Cara NoRole", 3),
    ]
    # Dan is not at the location: tier 4 is not offered.
    assert "Dan Elsewhere" not in {c.full_name for c in ranked}
    cara = next(c for c in ranked if c.full_name == "Cara NoRole")
    assert (cara.available, cara.has_role, cara.at_location) == (True, False, True)


async def test_exclude_shift_id_stops_a_shift_clashing_with_itself(db_session, roster):
    ranked = await _rank(db_session, exclude=roster["finn_shift"])
    finn = next(c for c in ranked if c.full_name == "Finn Booked")
    assert finn.tier == 1


async def test_employee_timezone_is_converted(db_session, seed_location, seed_roles):
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name="Lou LA",
        location_ids=[LOCATION_ID], timezone="America/Los_Angeles",
    )
    db_session.add(emp)
    await db_session.flush()
    db_session.add_all([
        EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                     role_id=ROLE_FLOOR_ID, skill_level=3),
        # 06:00-14:00 Los Angeles = 09:00-17:00 New York.
        _availability(emp.id, 6, 14),
    ])
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [("Lou LA", 1)]


async def test_invalid_employee_timezone_degrades_to_no_timezone(
    db_session, seed_location, seed_roles,
):
    await _employee(db_session, "Zed BadZone", timezone_name="Not/AZone")
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [("Zed BadZone", 1)]


async def test_another_companys_employees_never_appear(
    db_session, roster, other_company_employee_id,
):
    ranked = await _rank(db_session)
    assert other_company_employee_id not in {c.employee_id for c in ranked}
