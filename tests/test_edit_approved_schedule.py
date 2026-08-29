"""Editing an approved schedule (#84 stage 2).

Two hard refusals guard the data: a shift someone has checked into cannot be
touched at all, and the whole schedule freezes a month after created_date.
Everything else is a warning the manager can override.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, EmployeeCheckIn, Location, Region, Role, Shift, ShiftSchedule
from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    LOCATION_ID,
    ROLE_FLOOR_ID,
    ROLE_LEAD_ID,
    _id,
)

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/schedules"

# A fixed instant used to test the edit-window tie exactly. Wall-clock
# time always advances between a fixture writing created_at and the
# request computing cutoff, so a real request can never observe
# created_at == cutoff -- the clock has to be frozen to produce that tie.
FROZEN_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """Patched in for backend.routers.schedules's `datetime` name so
    `datetime.now(timezone.utc)` inside edit_approved_shifts returns
    FROZEN_NOW instead of the real time.
    """

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW if tz is not None else FROZEN_NOW.replace(tzinfo=None)


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
async def schedule_created_exactly_at_the_window_edge_id(
    db_session: AsyncSession, seed_location: Location,
) -> str:
    """created_at is exactly APPROVED_SCHEDULE_EDIT_DAYS days before FROZEN_NOW.

    Paired with a test that also freezes datetime.now() in
    backend.routers.schedules to FROZEN_NOW, so the request's cutoff and this
    created_at land on the exact same instant -- the only way to actually
    observe the tie, since real wall-clock time always advances between
    fixture setup and the request that follows it.
    """
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 5),
        status="approved",
        created_at=FROZEN_NOW - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS),
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
async def seed_role_id(seed_roles) -> str:
    return ROLE_FLOOR_ID


@pytest_asyncio.fixture
async def approved_shift(
    db_session: AsyncSession, seed_employees: list[Employee],
) -> tuple[str, str]:
    """An approved schedule with one shift, no check-in attached, so it is
    free to be edited or deleted. week_start_date matches the date the
    week-endpoint visibility test queries."""
    sched_id = _id()
    shift_id = _id()
    db_session.add(ShiftSchedule(
        id=sched_id,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 8, 31),
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
        date=date(2026, 8, 31),
        start_time=datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    ))
    await db_session.commit()
    return sched_id, shift_id


@pytest_asyncio.fixture
async def held_lock(db_session: AsyncSession, seed_manager) -> None:
    """Acquires the company schedule lock as a different user, simulating a
    second manager already mid-edit. `approve_schedule` and this endpoint
    both take this same lock, so an edit request made while it is held must
    be refused rather than silently racing the other manager's changes."""
    from backend.models import ScheduleLock

    db_session.add(ScheduleLock(
        company_id=COMPANY_ID,
        locked_by_user_id=_id(),
        operation="edit_approved",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()


@pytest_asyncio.fixture
async def other_company_role_id(db_session: AsyncSession) -> str:
    """A Role belonging to a DIFFERENT company than `manager_token`.

    Used to assert that reassigning a shift's role_id refuses to point it at
    another tenant's role (the modify-path counterpart of
    other_company_employee_id)."""
    other_company_id = _id()
    db_session.add(Company(id=other_company_id, name="Other Role Co", slug=_id()))
    await db_session.flush()
    role = Role(id=_id(), company_id=other_company_id, name="Outsider Role")
    db_session.add(role)
    await db_session.commit()
    return role.id


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


async def test_a_schedule_created_exactly_at_the_window_edge_is_still_editable(
    client: AsyncClient, manager_token: str, schedule_created_exactly_at_the_window_edge_id: str,
):
    """At the exact tie -- created_at == cutoff -- the comparison
    `created_at < cutoff` is False, so the schedule remains editable. That is
    the intended semantic: "editable for APPROVED_SCHEDULE_EDIT_DAYS days"
    naturally includes the Nth day itself, not just the (N-1) days before it.

    A real request can never land exactly on this tie (wall-clock time always
    advances between the fixture writing created_at and the request computing
    cutoff, pushing created_at a hair before cutoff), so the clock is frozen
    here via `_FrozenDatetime` to force the exact instant and make the tie
    observable at all.
    """
    with patch("backend.routers.schedules.datetime", _FrozenDatetime):
        resp = await client.put(
            f"{BASE}/{schedule_created_exactly_at_the_window_edge_id}/approved-shifts",
            headers={"Authorization": f"Bearer {manager_token}"},
            json={"edits": []},
        )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 0


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


async def test_a_shift_from_another_schedule_cannot_be_edited_via_this_one(
    client: AsyncClient, manager_token: str, approved_shift, approved_schedule_id: str, db_session,
):
    """`shift_id` alone is not enough: it must also belong to the schedule
    named in the URL. Without pinning the lookup to `schedule.id`, a manager
    could pass a DIFFERENT (also approved, same-tenant) schedule's id and
    still reach a shift that belongs elsewhere -- including one whose own
    30-day edit window has already closed, defeating that refusal entirely.

    `approved_shift`'s shift belongs to its own schedule, not
    `approved_schedule_id` -- exactly the cross-schedule case."""
    from sqlalchemy import select
    from backend.models import Shift

    _other_schedule_id, foreign_shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": foreign_shift_id, "deleted": True}]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_edit"

    # Must not have been touched.
    shift = (await db_session.execute(
        select(Shift).where(Shift.id == foreign_shift_id)
    )).scalar_one_or_none()
    assert shift is not None


# ---------------------------------------------------------------------------
# Applying edits (#84 stage 2)
# ---------------------------------------------------------------------------


async def test_deleting_a_shift_removes_the_row(
    client: AsyncClient, manager_token: str, approved_shift, db_session,
):
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1

    remaining = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one_or_none()
    assert remaining is None


async def test_reassigning_changes_the_employee(
    client: AsyncClient, manager_token: str, approved_shift, second_employee_id, db_session,
):
    """The hold moves with the row — no separate release step (stage 1)."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": second_employee_id}]},
    )
    assert resp.status_code == 200

    shift = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one()
    assert shift.employee_id == second_employee_id


async def test_an_edit_is_visible_to_the_week_endpoint(
    client: AsyncClient, manager_token: str, approved_shift,
):
    """Proves the edit wrote to the shifts table, not raw_llm_output.

    The week endpoint reads materialised Shift rows — the same source
    export_schedules.py and the approved-schedule calendar read. An edit that
    only touched the blob would leave this response unchanged."""
    schedule_id, shift_id = approved_shift
    await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "deleted": True}]},
    )
    week = await client.get(
        f"{BASE}/week/2026-08-31?status=approved",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    ids = [s["id"] for sched in week.json() for s in sched["shifts"]]
    assert shift_id not in ids


async def test_adding_a_shift_creates_a_row(
    client: AsyncClient, manager_token: str, approved_schedule_id, second_employee_id, seed_role_id,
):
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{
            "shift_id": None,
            "employee_id": second_employee_id,
            "role_id": seed_role_id,
            "date": "2026-08-31",
            "start_time": "2026-08-31T09:00:00-04:00",
            "end_time": "2026-08-31T13:00:00-04:00",
        }]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1


async def test_two_concurrent_edits_conflict(
    client: AsyncClient, manager_token: str, approved_schedule_id, held_lock,
):
    """Approve takes the same lock; without it two managers editing one week
    silently overwrite each other."""
    resp = await client.put(
        f"{BASE}/{approved_schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": []},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "schedule_locked"


async def test_reassigning_to_another_companys_employee_is_not_found(
    client: AsyncClient, manager_token: str, approved_shift, other_company_employee_id, db_session,
):
    """A manager must not be able to schedule another tenant's staff."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "employee_id": other_company_employee_id}]},
    )
    assert resp.status_code == 404

    shift = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one()
    assert shift.employee_id == EMPLOYEE1_ID


# ---------------------------------------------------------------------------
# Fix round 1: role_id cross-tenant IDOR, stale role_name, all-or-nothing
# batch behaviour, and accurate `applied` counting.
# ---------------------------------------------------------------------------


async def test_reassigning_the_role_updates_role_id_and_role_name(
    client: AsyncClient, manager_token: str, approved_shift, db_session,
):
    """role_name is denormalised onto Shift and read by the UI and the
    7shifts export — it must move with role_id, not go stale next to it."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "role_id": ROLE_LEAD_ID}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1

    shift = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one()
    assert shift.role_id == ROLE_LEAD_ID
    assert shift.role_name == "Team Lead"


async def test_reassigning_to_another_companys_role_is_not_found(
    client: AsyncClient, manager_token: str, approved_shift, other_company_role_id, db_session,
):
    """A manager must not be able to point a shift at another tenant's role.

    Critical finding: the modify path built the new role_id directly from
    the request with no company filter, unlike the employee check and the
    add-path role check right next to it."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id, "role_id": other_company_role_id}]},
    )
    assert resp.status_code == 404

    shift = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one()
    assert shift.role_id == ROLE_FLOOR_ID
    assert shift.role_name == "Floor Associate"


async def test_a_failed_edit_in_a_batch_leaves_earlier_edits_unapplied(
    client: AsyncClient, manager_token: str, approved_shift, db_session,
):
    """Partial application is the worst outcome for a scheduling tool: the
    manager would believe the schedule says something it does not. The first
    edit here is perfectly valid on its own; the second references a shift
    that doesn't exist. Neither may land."""
    from sqlalchemy import select
    from backend.models import Shift

    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [
            {"shift_id": shift_id, "deleted": True},
            {"shift_id": "doesnotexist", "deleted": True},
        ]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_edit"
    assert resp.json()["detail"]["index"] == 1

    # The first edit (deleting shift_id) must NOT have been applied.
    remaining = (await db_session.execute(
        select(Shift).where(Shift.id == shift_id)
    )).scalar_one_or_none()
    assert remaining is not None


async def test_an_edit_with_no_fields_set_is_not_counted_as_applied(
    client: AsyncClient, manager_token: str, approved_shift,
):
    """A matched, non-deleted shift_id edit whose optional fields are all
    None changes nothing on the row — it must not inflate `applied`, which
    the UI reports back to the manager as a count of real changes."""
    schedule_id, shift_id = approved_shift
    resp = await client.put(
        f"{BASE}/{schedule_id}/approved-shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"edits": [{"shift_id": shift_id}]},
    )
    assert resp.status_code == 200
    assert resp.json()["applied"] == 0
