"""Editing an approved schedule (#84 stage 2).

Two hard refusals guard the data: a shift someone has checked into cannot be
touched at all, and the whole schedule freezes a month after created_date.
Everything else is a warning the manager can override.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, EmployeeCheckIn, Location, Region, Shift, ShiftSchedule
from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    LOCATION_ID,
    ROLE_FLOOR_ID,
    _id,
)

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/schedules"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def draft_schedule_id(db_session: AsyncSession, seed_location: Location) -> str:
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13),
        status="draft",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def approved_schedule_id(db_session: AsyncSession, seed_location: Location) -> str:
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13),
        status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def old_approved_schedule_id(db_session: AsyncSession, seed_location: Location) -> str:
    """created_at is 40 days ago — past the 30-day APPROVED_SCHEDULE_EDIT_DAYS window."""
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 5),
        status="approved",
        created_at=datetime.now(timezone.utc) - timedelta(days=40),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def schedule_at_exact_boundary_id(db_session: AsyncSession, seed_location: Location) -> str:
    """created_at is exactly APPROVED_SCHEDULE_EDIT_DAYS days before fixture setup.

    The endpoint computes its own cutoff (`now() - APPROVED_SCHEDULE_EDIT_DAYS`)
    at request time, strictly later than this fixture ran, and compares with a
    strict `<`. So `created_at` here is always slightly earlier than that
    later cutoff, and the strict `<` refuses it. There is no way to observe
    the reverse (allowed) side of an exact tie in a real request, because any
    elapsed wall-clock time between "created" and "now" — even a microsecond
    of test/request overhead — pushes `created_at` under the cutoff. So the
    documented, exercised convention is: a schedule is editable while strictly
    younger than APPROVED_SCHEDULE_EDIT_DAYS days; the instant it turns
    exactly that old, it is refused.
    """
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 5),
        status="approved",
        created_at=datetime.now(timezone.utc) - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def schedule_just_inside_window_id(db_session: AsyncSession, seed_location: Location) -> str:
    """created_at is one hour short of the window — must remain editable."""
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 5),
        status="approved",
        created_at=datetime.now(timezone.utc)
        - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS)
        + timedelta(hours=1),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def schedule_just_outside_window_id(db_session: AsyncSession, seed_location: Location) -> str:
    """created_at is one hour past the window — must be refused."""
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 5),
        status="approved",
        created_at=datetime.now(timezone.utc)
        - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS)
        - timedelta(hours=1),
    ))
    await db_session.commit()
    return sid


@pytest_asyncio.fixture
async def second_employee_id(seed_employees: list[Employee]) -> str:
    return seed_employees[1].id


@pytest_asyncio.fixture
async def checked_in_shift(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> tuple[str, str]:
    """An approved schedule with one shift, plus an EmployeeCheckIn row
    referencing it directly. SQLite (the test DB) doesn't enforce foreign
    keys by default, so the FK alone would not produce the refusal — the
    application check under test is what makes this a 409."""
    sched_id = _id()
    shift_id = _id()
    db_session.add(ShiftSchedule(
        id=sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13),
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
        date=date(2026, 4, 13),
        start_time=datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 13, 17, 0, tzinfo=timezone.utc),
    ))
    await db_session.flush()
    db_session.add(EmployeeCheckIn(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        employee_id=EMPLOYEE1_ID,
        shift_id=shift_id,
        checked_in_at=datetime.now(timezone.utc),
        local_date=date(2026, 4, 13),
        counter=1,
        status="matched",
        minutes_from_start=0,
    ))
    await db_session.commit()
    return sched_id, shift_id


@pytest_asyncio.fixture
async def other_company_schedule_id(db_session: AsyncSession) -> str:
    """An approved schedule belonging to a DIFFERENT company than
    `manager_token`. Used to assert the endpoint 404s instead of leaking
    another tenant's schedule."""
    other_company_id = _id()
    other_region_id = _id()
    other_location_id = _id()
    db_session.add(Company(id=other_company_id, name="Other Co", slug=_id()))
    await db_session.flush()
    db_session.add(Region(id=other_region_id, company_id=other_company_id, name="Other Region"))
    await db_session.flush()
    db_session.add(Location(
        id=other_location_id,
        company_id=other_company_id,
        region_id=other_region_id,
        name="Other Location",
        timezone="UTC",
    ))
    await db_session.flush()
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=other_company_id,
        location_id=other_location_id,
        week_start_date=date(2026, 4, 13),
        status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return sid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_editing_a_draft_still_uses_the_old_endpoint(
    client: AsyncClient, manager_token: str, draft_schedule_id: str,
):
    """This endpoint is for approved schedules only; drafts keep their path."""
    resp = await client.put(
        f"{BASE}/{draft_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_approved"


async def test_an_edit_inside_the_window_is_allowed(
    client: AsyncClient, manager_token: str, approved_schedule_id: str,
):
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 0


async def test_an_edit_past_the_window_is_refused(
    client: AsyncClient, manager_token: str, old_approved_schedule_id: str,
):
    """created_date is the basis, per #84."""
    resp = await client.put(
        f"{BASE}/{old_approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "edit_window_closed"


async def test_the_exact_boundary_instant_is_refused(
    client: AsyncClient, manager_token: str, schedule_at_exact_boundary_id: str,
):
    """Pinning the convention: the comparison is strict `<`, so a schedule
    created exactly APPROVED_SCHEDULE_EDIT_DAYS days before "now" is already
    refused — see the fixture's docstring for why an exact tie always lands
    on this side in practice."""
    resp = await client.put(
        f"{BASE}/{schedule_at_exact_boundary_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "edit_window_closed"


async def test_just_inside_the_window_is_allowed(
    client: AsyncClient, manager_token: str, schedule_just_inside_window_id: str,
):
    """An hour short of the boundary must still be editable."""
    resp = await client.put(
        f"{BASE}/{schedule_just_inside_window_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 0


async def test_just_outside_the_window_is_refused(
    client: AsyncClient, manager_token: str, schedule_just_outside_window_id: str,
):
    """An hour past the boundary must be refused."""
    resp = await client.put(
        f"{BASE}/{schedule_just_outside_window_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "edit_window_closed"


async def test_a_checked_into_shift_cannot_be_deleted(
    client: AsyncClient, manager_token: str, checked_in_shift,
):
    """A check-in is a factual record that someone worked those hours.
    Rewriting the shift beneath it would make the record describe something
    that never happened. Postgres refuses this too, via
    employee_check_ins_shift_id_fkey — the API and the constraint agree."""
    schedule_id, shift_id = checked_in_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_a_checked_into_shift_cannot_be_modified(
    client: AsyncClient, manager_token: str, checked_in_shift, second_employee_id: str,
):
    schedule_id, shift_id = checked_in_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": second_employee_id}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_locked_by_checkin"


async def test_another_companys_schedule_is_not_found(
    client: AsyncClient, manager_token: str, other_company_schedule_id: str,
):
    resp = await client.put(
        f"{BASE}/{other_company_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 404
