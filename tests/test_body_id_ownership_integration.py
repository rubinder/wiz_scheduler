"""IDs named in a request body must belong to the caller's company.

Each endpoint below stores a reference to another row taken from the body.
Pointing one at another company's row must 404 and write nothing.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company, Employee, Location, Region, Role, Shift, ShiftSchedule, ShiftTemplate, User,
)
from backend.models.condensed_role import CondensedRole
from backend.services.passwords import hash_password
from tests.conftest import COMPANY2_ID, COMPANY_ID, LOCATION_ID, ROLE_FLOOR_ID, _id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Foreign:
    region_id: str
    location_id: str
    role_id: str
    user_id: str
    employee_id: str


@pytest_asyncio.fixture
async def foreign(db_session: AsyncSession) -> Foreign:
    """A second company, outside the caller's ownership group, with one of
    every row the endpoints below can reference."""
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    f = Foreign(_id(), _id(), _id(), _id(), _id())
    db_session.add_all([
        Region(id=f.region_id, company_id=COMPANY2_ID, name="Their Region"),
        Role(id=f.role_id, company_id=COMPANY2_ID, name="Their Role"),
        User(
            id=f.user_id, company_id=COMPANY2_ID, email="them@other.test",
            hashed_password=hash_password("x"), full_name="Them", user_role="employee",
        ),
    ])
    await db_session.flush()
    db_session.add_all([
        Location(
            id=f.location_id, company_id=COMPANY2_ID, region_id=f.region_id,
            name="Their Store", timezone="America/New_York",
        ),
        Employee(id=f.employee_id, company_id=COMPANY2_ID, full_name="Their Person"),
    ])
    await db_session.commit()
    return f


async def _count(db: AsyncSession, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ── Employees ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["role", "location", "user"])
async def test_create_employee_refuses_foreign_references(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    seed_location, seed_roles, foreign: Foreign, field: str,
) -> None:
    body: dict = {"full_name": "New Hire"}
    if field == "role":
        body["roles"] = [{"role_id": foreign.role_id, "skill_level": 1}]
    elif field == "location":
        body["location_ids"] = [LOCATION_ID, foreign.location_id]
    else:
        body["user_id"] = foreign.user_id

    resp = await client.post("/api/v1/employees/", json=body, headers=_h(manager_token))

    assert resp.status_code == 404, resp.text
    assert await _count(db_session, Employee, Employee.full_name == "New Hire") == 0


async def test_create_employee_accepts_own_references(
    client: AsyncClient, manager_token: str, seed_location, seed_roles, foreign: Foreign,
) -> None:
    resp = await client.post(
        "/api/v1/employees/",
        json={
            "full_name": "Own Refs",
            "roles": [{"role_id": ROLE_FLOOR_ID, "skill_level": 2}],
            "location_ids": [LOCATION_ID],
        },
        headers=_h(manager_token),
    )
    assert resp.status_code == 201, resp.text


async def test_update_employee_refuses_a_foreign_role(
    client: AsyncClient, manager_token: str, seed_location, seed_roles, seed_employees,
    foreign: Foreign,
) -> None:
    resp = await client.put(
        f"/api/v1/employees/{seed_employees[0].id}",
        json={"roles": [{"role_id": foreign.role_id, "skill_level": 1}]},
        headers=_h(manager_token),
    )
    assert resp.status_code == 404, resp.text


# ── Shift templates, locations, condensed roles ──────────────────────────────

async def test_create_shift_template_refuses_a_foreign_location(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, seed_location,
    foreign: Foreign,
) -> None:
    resp = await client.post(
        "/api/v1/shift-templates/",
        json={"location_id": foreign.location_id, "name": "Planted", "weekly_schedule": []},
        headers=_h(manager_token),
    )
    assert resp.status_code == 404, resp.text
    assert await _count(db_session, ShiftTemplate, ShiftTemplate.name == "Planted") == 0


async def test_location_refuses_a_foreign_region(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, seed_location,
    foreign: Foreign,
) -> None:
    created = await client.post(
        "/api/v1/locations/",
        json={"region_id": foreign.region_id, "name": "Planted", "timezone": "America/New_York"},
        headers=_h(manager_token),
    )
    assert created.status_code == 404, created.text
    assert await _count(db_session, Location, Location.name == "Planted") == 0

    updated = await client.put(
        f"/api/v1/locations/{LOCATION_ID}",
        json={"region_id": foreign.region_id},
        headers=_h(manager_token),
    )
    assert updated.status_code == 404, updated.text


async def test_condensed_role_refuses_a_foreign_role(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, seed_roles,
    foreign: Foreign,
) -> None:
    resp = await client.post(
        "/api/v1/condensed-roles/",
        json={"name": "Planted", "role_ids": [ROLE_FLOOR_ID, foreign.role_id]},
        headers=_h(manager_token),
    )
    assert resp.status_code == 404, resp.text
    assert await _count(db_session, CondensedRole, CondensedRole.name == "Planted") == 0


# ── Draft schedules and approval ─────────────────────────────────────────────

def _entry(employee_id: str | None, location_id: str = LOCATION_ID) -> dict:
    return {
        "employee_id": employee_id, "employee_name": "Someone",
        "role_id": ROLE_FLOOR_ID, "role_name": "Floor Associate",
        "location_id": location_id, "date": "2026-04-13",
        "start_time": "2026-04-13T09:00:00-04:00",
        "end_time": "2026-04-13T17:00:00-04:00", "status": "ok",
    }


async def _draft(db: AsyncSession, shifts: list[dict]) -> str:
    sid = _id()
    db.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13), status="draft",
        raw_llm_output=json.dumps(shifts),
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return sid


@pytest.mark.parametrize("planted", ["employee", "location"])
async def test_draft_save_refuses_foreign_ids(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    seed_location, seed_roles, seed_employees, foreign: Foreign, planted: str,
) -> None:
    own = seed_employees[0].id
    sid = await _draft(db_session, [_entry(own)])
    entry = (
        _entry(foreign.employee_id) if planted == "employee"
        else _entry(own, location_id=foreign.location_id)
    )

    resp = await client.put(
        f"/api/v1/schedules/{sid}/shifts", json={"shifts": [entry]}, headers=_h(manager_token),
    )

    assert resp.status_code == 404, resp.text
    stored = (await db_session.execute(
        select(ShiftSchedule.raw_llm_output).where(ShiftSchedule.id == sid)
    )).scalar_one()
    assert foreign.employee_id not in stored and foreign.location_id not in stored


async def test_draft_save_accepts_open_and_own_entries(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seed_location, seed_roles, seed_employees, foreign: Foreign,
) -> None:
    sid = await _draft(db_session, [_entry(seed_employees[0].id)])
    resp = await client.put(
        f"/api/v1/schedules/{sid}/shifts",
        json={"shifts": [_entry(seed_employees[0].id), _entry(None)]},
        headers=_h(manager_token),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("planted", ["employee", "location"])
async def test_approve_refuses_a_draft_naming_foreign_rows(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    seed_location, seed_roles, seed_employees, foreign: Foreign, planted: str,
) -> None:
    """A draft stored before the save-time check existed must still not
    materialise another company's employee or location."""
    entry = (
        _entry(foreign.employee_id) if planted == "employee"
        else _entry(seed_employees[0].id, location_id=foreign.location_id)
    )
    sid = await _draft(db_session, [_entry(seed_employees[0].id), entry])

    resp = await client.post(f"/api/v1/schedules/{sid}/approve", headers=_h(manager_token))

    assert resp.status_code == 404, resp.text
    assert await _count(db_session, Shift, Shift.shift_schedule_id == sid) == 0
    status = (await db_session.execute(
        select(ShiftSchedule.status).where(ShiftSchedule.id == sid)
    )).scalar_one()
    assert status == "draft"
