"""Ranking who fits an open shift (#174)."""

import pytest

pytestmark = pytest.mark.integration

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeAvailability, EmployeeDayBlackout, EmployeeRole,
    Location, Shift, ShiftSchedule,
)
from backend.services.candidates import rank_candidates, slot_span, tier_for
from tests.conftest import COMPANY_ID, LOCATION_ID, ROLE_FLOOR_ID, ROLE_LEAD_ID, _id

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
    available=True,
) -> str:
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name=name,
        location_ids=[LOCATION_ID] if at_location else [],
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_exclude_shift_id_stops_a_shift_clashing_with_itself(db_session, roster):
    ranked = await _rank(db_session, exclude=roster["finn_shift"])
    finn = next(c for c in ranked if c.full_name == "Finn Booked")
    assert finn.tier == 1


@pytest.mark.asyncio
async def test_availability_is_location_local(db_session, seed_location, seed_roles):
    """#171: availability is the location's wall clock, never converted.
    06:00-14:00 does not cover a 09:00-17:00 New York slot."""
    emp = Employee(
        id=_id(), company_id=COMPANY_ID, full_name="Lou LA",
        location_ids=[LOCATION_ID],
    )
    db_session.add(emp)
    await db_session.flush()
    db_session.add_all([
        EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                     role_id=ROLE_FLOOR_ID, skill_level=3),
        # Location-local 06:00-14:00; not converted from Los Angeles.
        _availability(emp.id, 6, 14),
    ])
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier) for c in ranked] == [("Lou LA", 2)]


@pytest.mark.asyncio
async def test_another_companys_employees_never_appear(
    db_session, roster, other_company_employee_id,
):
    ranked = await _rank(db_session)
    assert other_company_employee_id not in {c.employee_id for c in ranked}


async def _condensed_group(db: AsyncSession, *member_role_ids: str) -> str:
    """A condensed role grouping the given member roles; returns its id."""
    from backend.models.condensed_role import CondensedRole, CondensedRoleMapping
    cr = CondensedRole(id=_id(), company_id=COMPANY_ID, name="Floor Group")
    db.add(cr)
    await db.flush()
    for rid in member_role_ids:
        db.add(CondensedRoleMapping(id=_id(), condensed_role_id=cr.id, role_id=rid))
    await db.flush()
    return cr.id


async def _lead_holder(db: AsyncSession, name: str) -> str:
    emp = Employee(id=_id(), company_id=COMPANY_ID, full_name=name, location_ids=[LOCATION_ID])
    db.add(emp)
    await db.flush()
    db.add_all([
        EmployeeRole(id=_id(), company_id=COMPANY_ID, employee_id=emp.id,
                     role_id=ROLE_LEAD_ID, skill_level=3),
        _availability(emp.id),
    ])
    await db.flush()
    return emp.id


@pytest.mark.asyncio
async def test_a_condensed_role_slot_ranks_a_member_role_holder_as_having_the_role(
    db_session, seed_location, seed_roles,
):
    condensed_id = await _condensed_group(db_session, ROLE_FLOOR_ID, ROLE_LEAD_ID)
    await _lead_holder(db_session, "Lee Lead")
    await db_session.commit()
    location = await db_session.get(Location, LOCATION_ID)
    ranked = await rank_candidates(db_session, COMPANY_ID, location, condensed_id, *SPAN)
    assert [(c.full_name, c.tier, c.has_role) for c in ranked] == [("Lee Lead", 1, True)]


@pytest.mark.asyncio
async def test_a_member_role_slot_ranks_a_group_mate_holder_as_the_scheduler_would(
    db_session, seed_location, seed_roles,
):
    # The scheduler (graph.py role_equivalents) treats every member of a
    # condensed group as interchangeable: a Team Lead holder fills a Floor slot.
    await _condensed_group(db_session, ROLE_FLOOR_ID, ROLE_LEAD_ID)
    await _lead_holder(db_session, "Lee Lead")
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier, c.has_role) for c in ranked] == [("Lee Lead", 1, True)]


@pytest.mark.asyncio
async def test_a_member_role_outside_any_group_does_not_match_another_role(
    db_session, seed_location, seed_roles,
):
    await _lead_holder(db_session, "Lee Lead")
    await db_session.commit()
    ranked = await _rank(db_session)
    assert [(c.full_name, c.tier, c.has_role) for c in ranked] == [("Lee Lead", 3, False)]


from datetime import timedelta

from sqlalchemy import event

from tests.conftest import _get_test_engine


async def _post_candidates(client, token, **overrides):
    body = {
        "location_id": LOCATION_ID, "role_id": ROLE_FLOOR_ID,
        "date": DAY.isoformat(), "start_time": "09:00", "end_time": "17:00",
        **overrides,
    }
    return await client.post(
        "/api/v1/schedules/candidates",
        headers={"Authorization": f"Bearer {token}"}, json=body,
    )


@pytest.mark.asyncio
async def test_candidates_endpoint_returns_the_ranking(client, manager_token, roster):
    resp = await _post_candidates(client, manager_token)
    assert resp.status_code == 200, resp.text
    first = resp.json()[0]
    assert first == {
        "employee_id": roster["ana"], "full_name": "Ana Best", "tier": 1,
        "available": True, "has_role": True, "at_location": True,
    }


@pytest.mark.asyncio
async def test_candidates_for_another_companys_location_is_404(
    client, manager_token, roster, db_session,
):
    resp = await _post_candidates(client, manager_token, location_id="nosuchlc")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_candidates_reject_a_malformed_time(client, manager_token, roster):
    resp = await _post_candidates(client, manager_token, start_time="9am")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_candidates_are_manager_only(client, employee_token, roster):
    resp = await _post_candidates(client, employee_token)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_query_count_does_not_grow_with_the_roster(
    client, manager_token, db_session, roster,
):
    engine = _get_test_engine()
    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        first = await _post_candidates(client, manager_token)
        assert first.status_code == 200, first.text
        small = len(statements)
        assert small >= 1
        for i in range(10):
            await _employee(db_session, f"Extra {i:02d}")
        await db_session.commit()
        statements.clear()
        second = await _post_candidates(client, manager_token)
        assert second.status_code == 200, second.text
        large = len(statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
    assert len(second.json()) == len(first.json()) + 10
    assert large == small


async def _open_shift(db, sid, start: datetime, company_id=COMPANY_ID, location_id=LOCATION_ID):
    db.add(Shift(
        id=_id(), company_id=company_id, shift_schedule_id=sid,
        location_id=location_id, employee_id=None, role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=start.date(),
        start_time=start, end_time=start + timedelta(hours=8),
    ))


@pytest.mark.asyncio
async def test_open_shift_count_covers_the_next_14_days_of_approved_schedules(
    client, manager_token, db_session, seed_location, seed_roles,
):
    now = datetime.now(timezone.utc)
    approved, draft = _id(), _id()
    for sid, status_ in ((approved, "approved"), (draft, "draft")):
        db_session.add(ShiftSchedule(
            id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
            week_start_date=now.date(), status=status_, created_at=now,
        ))
    await db_session.flush()
    await _open_shift(db_session, approved, now + timedelta(days=1))    # counted
    await _open_shift(db_session, approved, now + timedelta(days=13))   # counted
    await _open_shift(db_session, approved, now + timedelta(days=20))   # too far
    await _open_shift(db_session, approved, now - timedelta(days=1))    # past
    await _open_shift(db_session, draft, now + timedelta(days=1))       # not approved
    await db_session.commit()

    resp = await client.get(
        "/api/v1/schedules/open-shifts/count",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"count": 2}
