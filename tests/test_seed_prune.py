"""seed.py's prune of pre-free-plan demo data.

A database seeded before the free plan existed holds 17 employees across 2
locations, which puts the demo group over the free caps. get_plan_state then
sets over_limit, which disables local generation as well as AI, so the public
demo cannot produce a schedule at all. These tests build that old shape and
assert the prune brings it back inside the caps.

The prune no longer removes EMPLOYEES: the roster grew to 21 and the free cap
to 25, so the old surplus ids (empl0005..empl0017) are part of the intended
roster now and LEGACY_EMPLOYEE_IDS is empty. Only the second location and its
template are still pruned. test_prune_keeps_the_whole_roster is the guard
against that list being repopulated — the prune runs last in the seed
transaction, so anything listed there is deleted after being created.
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
    DEMO_EMPLOYEE_COUNT,
    EMPLOYEE_IDS,
    LOCATION_ID_UPTOWN,
    LEGACY_EMPLOYEE_IDS,
    LEGACY_LOCATION_IDS,
    LEGACY_SHIFT_TEMPLATE_IDS,
    LOCATION_ID,
    OWNERSHIP_GROUP_ID,
    REGION_ID,
    ROLE_FLOOR_ID,
    ROLE_LEAD_ID,
    SHIFT_TEMPLATE_ID,
    _employee_location_ids,
    _prune_over_limit_demo_data,
    _role_assignments,
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


async def test_old_demo_shape_now_fits_the_free_limits(
    db_session: AsyncSession, old_demo: dict
):
    """The bug this file was written for is gone — the CAPS grew past it.

    The old shape (17 employees across 2 locations) was over a 5-employee,
    1-location free plan, which set over_limit and disabled generation
    entirely. At 25 employees and 2 locations it is legal, so the demo is no
    longer broken by its own history.

    Asserted rather than deleted: it pins the relationship between the seeded
    shape and the caps, so shrinking either one fails here and names why.
    """
    state = await get_plan_state(db_session, COMPANY_ID)
    assert state["plan"] == "free"
    assert state["over_limit"] is False
    assert state["can_generate_local"] is True
    assert state["block_reason"] is None


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


async def test_prune_keeps_the_whole_roster(
    db_session: AsyncSession, old_demo: dict
):
    """The prune must not touch a single seeded employee or their dependents.

    It runs LAST in the seed transaction, so any employee id it deletes is one
    the seed created moments earlier. This broke exactly that way when the
    roster grew past empl0004 into ids LEGACY_EMPLOYEE_IDS still claimed.
    """
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    kept = (await db_session.execute(
        select(Employee.id).where(Employee.company_id == COMPANY_ID)
    )).scalars().all()
    assert sorted(kept) == sorted(EMPLOYEE_IDS)

    for model in (EmployeeRole, EmployeeAvailability, EmployeeCompany):
        surviving = await _count(
            db_session, model, model.employee_id.in_(EMPLOYEE_IDS)
        )
        assert surviving == len(EMPLOYEE_IDS), (
            f"{model.__tablename__} lost rows for seeded employees"
        )


async def test_prune_restores_the_two_location_split(
    db_session: AsyncSession, old_demo: dict
):
    """Employees seeded by an older run carry that run's location list, and
    ON CONFLICT DO NOTHING would leave it stale.

    The repair is per employee: a blanket reset to [LOCATION_ID] — which is
    what this did while the demo had one location — would flatten the
    Downtown/Uptown/both split on every seed run.
    """
    await _prune_over_limit_demo_data(db_session)
    await db_session.commit()

    for emp_idx, emp_id in enumerate(EMPLOYEE_IDS):
        location_ids = (await db_session.execute(
            select(Employee.location_ids).where(Employee.id == emp_id)
        )).scalar_one()
        assert location_ids == _employee_location_ids(emp_idx)

    # And the split is a real one: each location has a roster, and some
    # employees are shared between them.
    at_downtown = {
        e for i, e in enumerate(EMPLOYEE_IDS)
        if LOCATION_ID in _employee_location_ids(i)
    }
    at_uptown = {
        e for i, e in enumerate(EMPLOYEE_IDS)
        if LOCATION_ID_UPTOWN in _employee_location_ids(i)
    }
    assert at_downtown and at_uptown
    assert at_downtown & at_uptown, "no shared employees — availability_draft is never exercised"
    assert at_downtown | at_uptown == set(EMPLOYEE_IDS), "an employee works nowhere"


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
        # A current employee at the pruned location: employees are no longer
        # pruned, so the location is what forces the shift to be deleted first.
        employee_id=EMPLOYEE_IDS[0], role_id=ROLE_FLOOR_ID,
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
                employee_id=EMPLOYEE_IDS[0], role_id=ROLE_FLOOR_ID,
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


def test_seeded_roster_fits_the_free_plan():
    """The demo is a free-plan tenant. Seed more employees than the cap and
    get_plan_state sets over_limit, which turns off local generation too — the
    demo then cannot produce a schedule at all, which is the exact failure the
    prune above was written to undo.

    Not a fixture test: it guards a pair of constants that live in different
    files and are edited for unrelated reasons.
    """
    assert DEMO_EMPLOYEE_COUNT == len(EMPLOYEE_IDS)
    assert DEMO_EMPLOYEE_COUNT <= settings.FREE_PLAN_MAX_EMPLOYEES, (
        f"seed puts {DEMO_EMPLOYEE_COUNT} employees in the demo but the free "
        f"plan allows {settings.FREE_PLAN_MAX_EMPLOYEES}"
    )


def test_both_locations_can_field_a_team_lead():
    """Every location's template asks for a Team Lead each weekday, so both
    rosters need eligible leads.

    The lead modulus and the location split are independent choices in
    seed.py; picking the same modulus for both would pile every lead into one
    location and leave the other's Team Lead slot permanently unfillable —
    the demo would still generate, just short-staffed, with nothing to say
    why.
    """
    leads = {emp_idx for emp_idx, role_id, _ in _role_assignments()
             if role_id == ROLE_LEAD_ID}
    assert leads, "no Team Leads on the roster at all"

    for location in (LOCATION_ID, LOCATION_ID_UPTOWN):
        eligible = {
            emp_idx for emp_idx in leads
            if location in _employee_location_ids(emp_idx)
        }
        assert eligible, f"{location} has no employee who can lead"


def test_role_assignment_ids_are_unique_per_employee_and_role():
    """Ids are keyed on (employee, role), not on position in the list.

    Positional ids (er000000, er000001, ...) are only stable while the list
    is. When the roster grew from 4 to 21 they silently remapped onto
    different pairs, and ON CONFLICT DO NOTHING preserved the stale rows.
    """
    ids = [f"er{emp_idx:03d}{role_id[-2:]}"
           for emp_idx, role_id, _ in _role_assignments()]

    assert len(ids) == len(set(ids)), "two assignments share an id"
    assert all(len(i) <= 8 for i in ids), "id overflows the String(8) column"

    # Stable under a roster that grows: employee 0's ids do not depend on how
    # many employees come after it.
    assert ids[0] == f"er000{ROLE_FLOOR_ID[-2:]}"

