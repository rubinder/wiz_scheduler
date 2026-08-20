"""seed.py's prune of pre-free-plan demo data.

A database seeded before the free plan existed holds 17 employees across 2
locations, which puts the demo group over the free caps. get_plan_state then
sets over_limit, which disables local generation as well as AI, so the public
demo cannot produce a schedule at all. These tests build that old shape and
assert the prune brings it back inside the caps.
"""

from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Company,
    Employee,
    EmployeeAvailability,
    EmployeeCompany,
    EmployeeRole,
    Location,
    OwnershipGroup,
    Region,
    Role,
    Shift,
    ShiftSchedule,
    ShiftTemplate,
)
from backend.seed import (
    COMPANY_ID,
    EMPLOYEE_IDS,
    LEGACY_EMPLOYEE_IDS,
    LEGACY_LOCATION_IDS,
    LEGACY_SHIFT_TEMPLATE_IDS,
    LOCATION_ID,
    OWNERSHIP_GROUP_ID,
    REGION_ID,
    ROLE_FLOOR_ID,
    ROLE_LEAD_ID,
    SHIFT_TEMPLATE_ID,
    _prune_over_limit_demo_data,
    _weekly_schedule,
)
from backend.services.plan import get_plan_state

pytestmark = pytest.mark.asyncio


async def _count(db: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return (await db.execute(stmt)).scalar_one()


@pytest.fixture
def old_shape_ids() -> dict:
    """The demo as an older seed.py left it: 17 employees, 2 locations."""
    return {
        "employees": EMPLOYEE_IDS + LEGACY_EMPLOYEE_IDS,
        "locations": [LOCATION_ID] + LEGACY_LOCATION_IDS,
        "templates": [SHIFT_TEMPLATE_ID] + LEGACY_SHIFT_TEMPLATE_IDS,
    }


@pytest_asyncio.fixture
async def old_demo(db_session: AsyncSession, old_shape_ids: dict) -> dict:
    db_session.add(OwnershipGroup(id=OWNERSHIP_GROUP_ID, name="Acme Corp Group"))
    await db_session.flush()
    db_session.add(Company(id=COMPANY_ID, name="Acme Corp", slug="acme-corp",
                           ownership_group_id=OWNERSHIP_GROUP_ID))
    db_session.add(Region(id=REGION_ID, company_id=COMPANY_ID, name="East"))
    await db_session.flush()

    for loc_id in old_shape_ids["locations"]:
        db_session.add(Location(id=loc_id, company_id=COMPANY_ID,
                                region_id=REGION_ID, name=loc_id,
                                timezone="America/New_York"))
    for role_id in (ROLE_FLOOR_ID, ROLE_LEAD_ID):
        db_session.add(Role(id=role_id, company_id=COMPANY_ID, name=role_id))
    await db_session.flush()

    for tpl_id, loc_id in zip(old_shape_ids["templates"],
                              old_shape_ids["locations"]):
        db_session.add(ShiftTemplate(
            id=tpl_id, company_id=COMPANY_ID, location_id=loc_id,
            name="Weekday Standard",
            # The old headcount, which the prune must correct on the survivor.
            weekly_schedule=[{"day": "Monday", "role_name": "Floor Associate",
                              "role_id": ROLE_FLOOR_ID, "headcount": 3,
                              "start_time": "09:00", "end_time": "17:00"}],
        ))

    for emp_id in old_shape_ids["employees"]:
        db_session.add(Employee(
            id=emp_id, company_id=COMPANY_ID, full_name=emp_id,
            email=f"{emp_id}@example.com",
            # Every employee listed BOTH locations under the old shape.
            location_ids=old_shape_ids["locations"],
        ))
    await db_session.flush()

    # Dependents that must not block the delete.
    for emp_id in old_shape_ids["employees"]:
        db_session.add(EmployeeCompany(
            id=f"ec{emp_id[-6:]}", employee_id=emp_id, company_id=COMPANY_ID
        ))
        db_session.add(EmployeeRole(
            id=f"er{emp_id[-6:]}", company_id=COMPANY_ID, employee_id=emp_id,
            role_id=ROLE_FLOOR_ID, skill_level=2,
        ))
        db_session.add(EmployeeAvailability(
            id=f"av{emp_id[-6:]}", company_id=COMPANY_ID, employee_id=emp_id,
            year=2026, month=8, day=17,
            start_time=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
            end_time=datetime(2026, 8, 17, 17, tzinfo=timezone.utc),
        ))
    await db_session.commit()
    return old_shape_ids


async def test_old_demo_starts_over_the_free_limits(
    db_session: AsyncSession, old_demo: dict
):
    """Establishes the bug: the demo cannot generate anything."""
    state = await get_plan_state(db_session, COMPANY_ID)
    assert state["plan"] == "free"
    assert state["over_limit"] is True
    assert state["can_generate_local"] is False
    assert state["can_generate_ai"] is False
    assert state["block_reason"] == "plan_limit_exceeded"


async def test_prune_brings_the_demo_inside_the_free_limits(
    db_session: AsyncSession, old_demo: dict
):
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    state = await get_plan_state(db_session, COMPANY_ID)
    assert state["over_limit"] is False
    assert state["can_generate_local"] is True
    assert state["block_reason"] is None
    assert state["employees"]["count"] <= settings.FREE_PLAN_MAX_EMPLOYEES
    assert state["locations"]["count"] <= settings.FREE_PLAN_MAX_LOCATIONS


async def test_prune_keeps_the_intended_rows(
    db_session: AsyncSession, old_demo: dict
):
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    kept = (await db_session.execute(
        select(Employee.id).where(Employee.company_id == COMPANY_ID)
    )).scalars().all()
    assert sorted(kept) == sorted(EMPLOYEE_IDS)

    locs = (await db_session.execute(
        select(Location.id).where(Location.company_id == COMPANY_ID)
    )).scalars().all()
    assert locs == [LOCATION_ID]

    tpls = (await db_session.execute(
        select(ShiftTemplate.id).where(ShiftTemplate.company_id == COMPANY_ID)
    )).scalars().all()
    assert tpls == [SHIFT_TEMPLATE_ID]


async def test_prune_removes_employee_dependents(
    db_session: AsyncSession, old_demo: dict
):
    """Rows hanging off deleted employees must go too, or the delete would
    have failed on the foreign key."""
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    for model in (EmployeeRole, EmployeeAvailability, EmployeeCompany):
        leftover = await _count(
            db_session, model, model.employee_id.in_(LEGACY_EMPLOYEE_IDS)
        )
        assert leftover == 0, f"{model.__tablename__} still has orphan rows"


async def test_prune_fixes_survivors_location_list(
    db_session: AsyncSession, old_demo: dict
):
    """Surviving employees listed the removed location; ON CONFLICT DO NOTHING
    would have left that stale."""
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    rows = (await db_session.execute(
        select(Employee.location_ids).where(Employee.company_id == COMPANY_ID)
    )).scalars().all()
    for location_ids in rows:
        assert location_ids == [LOCATION_ID]


async def test_prune_fixes_survivor_template_headcount(
    db_session: AsyncSession, old_demo: dict
):
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    tpl = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == SHIFT_TEMPLATE_ID)
    )).scalar_one()
    assert tpl.weekly_schedule == _weekly_schedule()


async def test_prune_deletes_generated_shifts_and_schedules(
    db_session: AsyncSession, old_demo: dict
):
    """Shifts reference schedules, locations and employees at once, so they
    must be deleted first or the location delete fails."""
    sched_id = "sch00001"
    db_session.add(ShiftSchedule(
        id=sched_id, company_id=COMPANY_ID,
        location_id=LEGACY_LOCATION_IDS[0],
        week_start_date=date(2026, 8, 10), status="draft",
    ))
    await db_session.flush()
    db_session.add(Shift(
        id="shf00001", company_id=COMPANY_ID, shift_schedule_id=sched_id,
        location_id=LEGACY_LOCATION_IDS[0],
        employee_id=LEGACY_EMPLOYEE_IDS[0], role_id=ROLE_FLOOR_ID,
        role_name="Floor Associate", date=date(2026, 8, 10),
        start_time=datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
        end_time=datetime(2026, 8, 10, 17, tzinfo=timezone.utc),
    ))
    await db_session.commit()

    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    assert await _count(db_session, Shift, Shift.company_id == COMPANY_ID) == 0
    assert await _count(
        db_session, ShiftSchedule, ShiftSchedule.company_id == COMPANY_ID
    ) == 0


async def test_prune_is_idempotent(db_session: AsyncSession, old_demo: dict):
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    state = await get_plan_state(db_session, COMPANY_ID)
    assert state["over_limit"] is False
    kept = (await db_session.execute(
        select(Employee.id).where(Employee.company_id == COMPANY_ID)
    )).scalars().all()
    assert sorted(kept) == sorted(EMPLOYEE_IDS)


async def test_prune_leaves_other_tenants_alone(
    db_session: AsyncSession, old_demo: dict
):
    """The prune is scoped to the demo company's deterministic ids. A real
    tenant that happens to have many employees must be untouched."""
    other_og, other_co = "othergrp", "othercomp"
    db_session.add(OwnershipGroup(id=other_og, name="Other"))
    await db_session.flush()
    db_session.add(Company(id=other_co, name="Other Co", slug="other-co",
                           ownership_group_id=other_og))
    await db_session.flush()
    for i in range(6):
        db_session.add(Employee(
            id=f"oth{i:05d}", company_id=other_co, full_name="E",
            email=f"other{i}@example.com",
        ))
    await db_session.commit()

    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    survivors = await _count(
        db_session, Employee, Employee.company_id == other_co
    )
    assert survivors == 6


# ---------------------------------------------------------------------------
# Foreign-key ordering
#
# The shared test engine does not switch on SQLite's foreign_keys pragma, so
# the tests above prove the prune removes the right rows but not that it
# removes them in an order Postgres will accept — which is the actual risk in
# production, where the constraints are real and mostly NO ACTION. This test
# stands up its own engine with enforcement on, so a wrong order fails here
# instead of halfway through a production run.
# ---------------------------------------------------------------------------


async def test_prune_delete_order_satisfies_foreign_keys():
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from backend.database import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///", poolclass=StaticPool, echo=False
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_fks(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
        # Same shim conftest installs: server_default=text("now()") needs it.
        dbapi_connection.create_function(
            "now", 0, lambda: datetime.now(timezone.utc).isoformat()
        )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            # Confirm the pragma really is on — otherwise this test would
            # pass vacuously and prove nothing.
            enforced = (
                await db.execute(text("PRAGMA foreign_keys"))
            ).scalar_one()
            assert enforced == 1, "foreign key enforcement is not active"

            employees = EMPLOYEE_IDS + LEGACY_EMPLOYEE_IDS
            locations = [LOCATION_ID] + LEGACY_LOCATION_IDS
            templates = [SHIFT_TEMPLATE_ID] + LEGACY_SHIFT_TEMPLATE_IDS

            db.add(OwnershipGroup(id=OWNERSHIP_GROUP_ID, name="G"))
            await db.flush()
            db.add(Company(id=COMPANY_ID, name="Acme", slug="acme",
                           ownership_group_id=OWNERSHIP_GROUP_ID))
            db.add(Region(id=REGION_ID, company_id=COMPANY_ID, name="East"))
            await db.flush()
            for loc_id in locations:
                db.add(Location(id=loc_id, company_id=COMPANY_ID,
                                region_id=REGION_ID, name=loc_id,
                                timezone="America/New_York"))
            for role_id in (ROLE_FLOOR_ID, ROLE_LEAD_ID):
                db.add(Role(id=role_id, company_id=COMPANY_ID, name=role_id))
            await db.flush()
            for tpl_id, loc_id in zip(templates, locations):
                db.add(ShiftTemplate(id=tpl_id, company_id=COMPANY_ID,
                                     location_id=loc_id, name="T",
                                     weekly_schedule=[]))
            for emp_id in employees:
                db.add(Employee(id=emp_id, company_id=COMPANY_ID,
                                full_name=emp_id,
                                email=f"{emp_id}@example.com",
                                location_ids=locations))
            await db.flush()

            # A dependent in every table the prune touches for employees.
            for emp_id in employees:
                db.add(EmployeeCompany(id=f"ec{emp_id[-6:]}",
                                       employee_id=emp_id,
                                       company_id=COMPANY_ID))
                db.add(EmployeeRole(id=f"er{emp_id[-6:]}",
                                    company_id=COMPANY_ID, employee_id=emp_id,
                                    role_id=ROLE_FLOOR_ID, skill_level=2))
                db.add(EmployeeAvailability(
                    id=f"av{emp_id[-6:]}", company_id=COMPANY_ID,
                    employee_id=emp_id, year=2026, month=8, day=17,
                    start_time=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
                    end_time=datetime(2026, 8, 17, 17, tzinfo=timezone.utc),
                ))
            await db.flush()

            # A schedule and a shift on the location being removed: the shift
            # points at a schedule, a location and an employee at once, so it
            # is what forces the ordering.
            db.add(ShiftSchedule(id="sch00001", company_id=COMPANY_ID,
                                 location_id=LEGACY_LOCATION_IDS[0],
                                 week_start_date=date(2026, 8, 10),
                                 status="draft"))
            await db.flush()
            db.add(Shift(
                id="shf00001", company_id=COMPANY_ID,
                shift_schedule_id="sch00001",
                location_id=LEGACY_LOCATION_IDS[0],
                employee_id=LEGACY_EMPLOYEE_IDS[0], role_id=ROLE_FLOOR_ID,
                role_name="Floor Associate", date=date(2026, 8, 10),
                start_time=datetime(2026, 8, 10, 9, tzinfo=timezone.utc),
                end_time=datetime(2026, 8, 10, 17, tzinfo=timezone.utc),
            ))
            await db.commit()

            # The assertion is that this does not raise an IntegrityError.
            await _prune_over_limit_demo_data(db)
            await db.commit()

            kept = (await db.execute(
                select(Employee.id).where(Employee.company_id == COMPANY_ID)
            )).scalars().all()
            assert sorted(kept) == sorted(EMPLOYEE_IDS)
    finally:
        await engine.dispose()
