"""
Idempotent seed script for WizScheduler.

Usage:
    cd backend && python seed.py

Uses deterministic string IDs so re-runs are safe (ON CONFLICT DO NOTHING).
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from passlib.context import CryptContext
from sqlalchemy import delete, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session_factory, engine
from backend.models import (
    Company,
    Department,
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeCompany,
    EmployeeDayBlackout,
    EmployeeInvite,
    EmployeeRole,
    EmployeeRoleMinutes,
    IntegrationImport,
    Location,
    OwnershipGroup,
    Region,
    Role,
    Shift,
    ShiftSchedule,
    ShiftTemplate,
    SpecialHoursDay,
    User,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Password for the demo manager/employee logins. In deployed environments this
# arrives via DEMO_SEED_PASSWORD, injected from AWS Secrets Manager. The
# fallback exists so a fresh `python seed.py` works locally with no setup; it
# must never reach production, hence the guard in main().
_LOCAL_DEMO_PASSWORD = "example"
DEMO_PASSWORD = settings.DEMO_SEED_PASSWORD or _LOCAL_DEMO_PASSWORD

# Deterministic short-string IDs for idempotent seeding
OWNERSHIP_GROUP_ID = "owngrp01"
COMPANY_ID = "comp0001"
MANAGER_USER_ID = "user0001"
EMPLOYEE_USER_ID = "user0002"
ROLE_FLOOR_ID = "role0001"
ROLE_LEAD_ID = "role0002"
REGION_ID = "regn0001"
LOCATION_ID = "locn0001"
# The demo runs two locations, which is exactly FREE_PLAN_MAX_LOCATIONS. Two
# is the interesting number: processing a second location is what exercises
# `availability_draft`, the deep copy the pipeline mutates after each location
# so one employee cannot be booked into overlapping shifts at both. A
# one-location demo never reaches that code.
#
# NOT locn0002 — that id is in LEGACY_LOCATION_IDS and the prune deletes it.
LOCATION_ID_UPTOWN = "locn0003"

# The demo tenant has no Stripe subscription, so services.plan resolves it to
# the free plan. Keep it AT OR UNDER the free caps: past them, get_plan_state
# sets over_limit, which turns off local generation too and leaves the demo
# unable to produce a schedule at all.
#
# 21 employees against FREE_PLAN_MAX_EMPLOYEES = 25. The roster is fixed —
# services.plan.assert_roster_editable refuses adds and removes for this
# ownership group — so the 4 spare slots are headroom against the cap being
# lowered, not room for a visitor to grow into. If FREE_PLAN_MAX_EMPLOYEES
# ever drops below 21, this seed puts the demo over_limit and silently breaks
# generation; tests/test_seed_prune.py asserts the two stay consistent.
DEMO_EMPLOYEE_COUNT = 21
EMPLOYEE_IDS = [f"empl{str(i).zfill(4)}" for i in range(1, DEMO_EMPLOYEE_COUNT + 1)]

# How far ahead availability is materialized, and the daily window.
#
# employee_availability stores one row per CONCRETE date (year/month/day);
# there is no recurrence rule, so "available every day, forever" cannot be
# expressed as data — it has to be written out date by date. Two years of
# 7-day windows for the whole roster is ~15k rows (a few hundred KB) and
# covers any week a visitor can reach from the schedule picker.
#
# This is a horizon, NOT perpetuity: it is anchored to the day the seed runs
# and does not extend itself. Re-running `python seed.py` rolls it forward.
AVAILABILITY_HORIZON_DAYS = 730
AVAILABILITY_START_HOUR = 9
AVAILABILITY_END_HOUR = 17

SHIFT_TEMPLATE_ID = "shft0001"
# Likewise not shft0002, which the prune deletes.
SHIFT_TEMPLATE_ID_UPTOWN = "shft0003"

# Seeded by earlier versions of this file, before the free plan existed. Listed
# so _prune_over_limit_demo_data can remove them from an already-seeded
# database; a fresh seed never creates them.
LEGACY_LOCATION_IDS = ["locn0002"]
LEGACY_SHIFT_TEMPLATE_IDS = ["shft0002"]

# Empty since the roster grew to 21. This held empl0005..empl0017 — surplus
# employees from the pre-free-plan seed that had to go to fit a 5-employee
# cap. Those same ids are now part of the intended roster, and the prune runs
# LAST in the seed transaction, so leaving them listed would delete 13 of the
# employees seeded moments earlier. The assertion below keeps the two lists
# from ever overlapping again.
LEGACY_EMPLOYEE_IDS: list[str] = []

assert not (set(LEGACY_EMPLOYEE_IDS) & set(EMPLOYEE_IDS)), (
    "LEGACY_EMPLOYEE_IDS overlaps EMPLOYEE_IDS: _prune_over_limit_demo_data "
    "would delete employees this seed just created."
)


def _hash(password: str) -> str:
    return pwd_context.hash(password)


def _weekly_schedule(floor_headcount: int = 2) -> list[dict]:
    """A demo location's weekly shift template.

    *floor_headcount* Floor Associates + 1 Team Lead per weekday. Downtown
    runs 2 (15 slots a week against 14 eligible employees) and Uptown 1 (10
    slots against 14), so neither location can be filled by pinning the same
    people to every day and the scheduler has real choices to make.

    The two locations are deliberately different sizes: the seven employees
    who work BOTH are contended for, which is what makes the second location's
    pass over `availability_draft` do something.

    Weekdays only, deliberately. The seeded availability covers all 7 days, so
    the template can be widened to weekends without touching availability.
    """
    schedule: list[dict] = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        schedule.append(
            {
                "day": day,
                "role_name": "Floor Associate",
                "role_id": ROLE_FLOOR_ID,
                "headcount": floor_headcount,
                "start_time": "09:00",
                "end_time": "17:00",
            }
        )
        schedule.append(
            {
                "day": day,
                "role_name": "Team Lead",
                "role_id": ROLE_LEAD_ID,
                "headcount": 1,
                "start_time": "09:00",
                "end_time": "17:00",
            }
        )
    return schedule


def _role_assignments() -> list[tuple[int, str, int]]:
    """(employee_index, role_id, skill_level) for the whole roster.

    Everyone can work the floor; every third employee can also lead, so the
    daily Team Lead slot has 7 candidates to rotate between instead of pinning
    one person to every day. Skill levels cycle 2-5 so the scheduler has
    something to rank on.

    The lead modulus (3) and the location split in _employee_location_ids
    (thirds by index) are chosen so that BOTH locations end up with eligible
    leads — see that function. tests/test_seed_prune.py asserts it, because
    getting it wrong leaves one location's Team Lead slot unfillable and the
    demo silently generating short-staffed schedules.
    """
    assignments: list[tuple[int, str, int]] = []
    for emp_idx in range(len(EMPLOYEE_IDS)):
        assignments.append((emp_idx, ROLE_FLOOR_ID, 2 + (emp_idx % 4)))
        if emp_idx % 3 == 0:
            assignments.append((emp_idx, ROLE_LEAD_ID, 2 + (emp_idx % 3)))
    return assignments


def _employee_location_ids(emp_idx: int) -> list[str]:
    """Which locations employee *emp_idx* works at.

    The roster splits into thirds by index:

        0-6    Downtown only
        7-13   Uptown only
        14-20  BOTH

    The shared third is the point of the split. Those seven are eligible at
    both locations in the same run, so the scheduler has to decide where to
    put them and `availability_draft` has to stop the second location handing
    them a shift that overlaps one they already picked up at the first. With
    a cleanly partitioned roster that guard is never exercised.

    Deliberately NOT interleaved with the Team Lead assignment, which takes
    every third employee (0, 3, 6, ...). Splitting on the same modulus would
    put every lead in one bucket and leave the other location with an
    unfillable Team Lead slot. As laid out here Downtown can field leads
    0/3/6/15/18 and Uptown 9/12/15/18.
    """
    third = len(EMPLOYEE_IDS) // 3
    if emp_idx < third:
        return [LOCATION_ID]
    if emp_idx < third * 2:
        return [LOCATION_ID_UPTOWN]
    return [LOCATION_ID, LOCATION_ID_UPTOWN]


_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int, width: int) -> str:
    """Fixed-width base36, for packing ids into the 8-char id column."""
    out = ""
    while n:
        n, rem = divmod(n, 36)
        out = _B36[rem] + out
    return out.rjust(width, "0")[-width:]


def _availability_id(emp_idx: int, day: date) -> str:
    """Deterministic 8-char id: "av" + employee index + date ordinal.

    Both fields are base36 so they fit: 2 chars hold 1,295 employees and 4
    hold ordinals up to 1,679,615 (the year 4600). Keying on the date's
    ordinal rather than an offset from "today" means a given employee-day
    always lands on the same id, so re-running the seed on a later date
    rewrites the overlapping rows instead of duplicating them.
    """
    return f"av{_b36(emp_idx, 2)}{_b36(day.toordinal(), 4)}"


async def _seed_availability(db: AsyncSession) -> None:
    """Give every demo employee a 9-5 window on every day of the horizon.

    Timezone: written as local wall-clock tagged UTC, matching what the
    availability endpoint stores when the UI posts a naive "2026-08-24T09:00"
    — Postgres reads a naive timestamptz literal in the session zone (UTC).
    The scheduling validator compares availability to shift times as naive
    HH:MM without converting either (backend/scheduling/nodes.py), so this is
    the representation it expects.

    This replaces a version that tagged the windows -05:00. That survives on
    SQLite, which keeps the offset, but Postgres normalizes the value to UTC
    on write and hands back 14:00-22:00 — five hours off the 09:00-17:00
    template slot, so the validator rejected every shift and the demo could
    not fill a single one. See tests/test_avail_local_wallclock.py for the
    same trap on the API path.

    Deletes the company's availability first rather than relying on ON
    CONFLICT: the pre-existing rows use a different id scheme, so conflict
    resolution would leave them behind as duplicate windows. A demo reset
    discarding whatever a visitor edited is the intended behaviour.
    """
    await db.execute(
        delete(EmployeeAvailability).where(
            EmployeeAvailability.company_id == COMPANY_ID
        )
    )

    start_day = datetime.now(timezone.utc).date()
    rows: list[dict] = []
    for emp_idx, eid in enumerate(EMPLOYEE_IDS):
        for offset in range(AVAILABILITY_HORIZON_DAYS):
            day = start_day + timedelta(days=offset)
            rows.append(
                {
                    "id": _availability_id(emp_idx, day),
                    "company_id": COMPANY_ID,
                    "employee_id": eid,
                    "year": day.year,
                    "month": day.month,
                    "day": day.day,
                    "start_time": datetime(
                        day.year, day.month, day.day,
                        AVAILABILITY_START_HOUR, 0, tzinfo=timezone.utc,
                    ),
                    "end_time": datetime(
                        day.year, day.month, day.day,
                        AVAILABILITY_END_HOUR, 0, tzinfo=timezone.utc,
                    ),
                }
            )

    # Core executemany in chunks: one statement per row is ~15k round trips,
    # and a single statement of that width risks the driver's parameter cap.
    CHUNK = 2_000
    for i in range(0, len(rows), CHUNK):
        await db.execute(insert(EmployeeAvailability), rows[i : i + CHUNK])

    print(
        f"  availability: {len(rows)} windows "
        f"({len(EMPLOYEE_IDS)} employees x {AVAILABILITY_HORIZON_DAYS} days, "
        f"{AVAILABILITY_START_HOUR:02d}:00-{AVAILABILITY_END_HOUR:02d}:00)"
    )


async def _prune_over_limit_demo_data(db: AsyncSession) -> None:
    """Delete demo rows seeded before the free plan existed.

    Seeding is idempotent through ON CONFLICT DO NOTHING, which never removes
    anything, so a database seeded by an older version of this file still holds
    17 employees across 2 locations. That puts the demo group over the free
    caps, and get_plan_state then sets over_limit, which turns off local
    generation as well as AI — the demo cannot produce a schedule at all.

    Scoped to the demo company's own deterministic ids, and ordered by foreign
    key: shifts reference shift_schedules, locations and employees at once, so
    they go first; locations go last. Written with SQLAlchemy constructs rather
    than raw SQL so it runs on SQLite under test as well as on Postgres.
    """
    emp_ids = LEGACY_EMPLOYEE_IDS
    loc_ids = LEGACY_LOCATION_IDS
    tpl_ids = LEGACY_SHIFT_TEMPLATE_IDS
    if not (emp_ids or loc_ids or tpl_ids):
        return

    removed: list[str] = []

    async def _run(stmt, label: str) -> None:
        result = await db.execute(stmt)
        if result.rowcount:
            removed.append(f"{label}={result.rowcount}")

    # Generated schedule contents first — a shift points at a schedule, a
    # location and an employee simultaneously.
    await _run(
        delete(Shift).where(
            Shift.company_id == COMPANY_ID,
            or_(Shift.employee_id.in_(emp_ids), Shift.location_id.in_(loc_ids)),
        ),
        "shifts",
    )
    await _run(
        delete(ShiftSchedule).where(
            ShiftSchedule.company_id == COMPANY_ID,
            ShiftSchedule.location_id.in_(loc_ids),
        ),
        "shift_schedules",
    )

    # Everything hanging off the surplus employees.
    await _run(
        delete(EmployeeAffinity).where(
            EmployeeAffinity.company_id == COMPANY_ID,
            or_(
                EmployeeAffinity.employee_id.in_(emp_ids),
                EmployeeAffinity.target_employee_id.in_(emp_ids),
            ),
        ),
        "employee_affinities",
    )
    for model, label in (
        (EmployeeAvailability, "employee_availability"),
        (EmployeeRoleMinutes, "employee_role_minutes"),
        (EmployeeRole, "employee_roles"),
        (EmployeeInvite, "employee_invites"),
        (EmployeeDayBlackout, "employee_day_blackouts"),
        (EmployeeCompany, "employee_companies"),
    ):
        await _run(
            delete(model).where(
                model.company_id == COMPANY_ID, model.employee_id.in_(emp_ids)
            ),
            label,
        )
    await _run(
        delete(Employee).where(
            Employee.company_id == COMPANY_ID, Employee.id.in_(emp_ids)
        ),
        "employees",
    )

    # Everything hanging off the surplus location.
    #
    # special_hours_days goes BEFORE shift_templates: it carries a nullable
    # shift_template_id, so deleting the templates first trips
    # special_hours_days_shift_template_id_fkey and aborts the whole seed
    # transaction. It used to sit in the loop below, after the templates.
    #
    # Matched on the template reference as well as the location, so a special
    # hours row at a SURVIVING location that points at a pruned template goes
    # too — location_id alone would leave it behind holding the same FK.
    doomed_templates = (
        select(ShiftTemplate.id)
        .where(
            ShiftTemplate.company_id == COMPANY_ID,
            or_(
                ShiftTemplate.id.in_(tpl_ids),
                ShiftTemplate.location_id.in_(loc_ids),
            ),
        )
        .scalar_subquery()
    )
    await _run(
        delete(SpecialHoursDay).where(
            or_(
                SpecialHoursDay.location_id.in_(loc_ids),
                SpecialHoursDay.shift_template_id.in_(doomed_templates),
            )
        ),
        "special_hours_days",
    )
    await _run(
        delete(ShiftTemplate).where(
            ShiftTemplate.company_id == COMPANY_ID,
            or_(
                ShiftTemplate.id.in_(tpl_ids),
                ShiftTemplate.location_id.in_(loc_ids),
            ),
        ),
        "shift_templates",
    )
    for model, label in (
        (Department, "departments"),
        (IntegrationImport, "integration_imports"),
    ):
        await _run(delete(model).where(model.location_id.in_(loc_ids)), label)
    await _run(
        delete(Location).where(
            Location.company_id == COMPANY_ID, Location.id.in_(loc_ids)
        ),
        "locations",
    )

    # An employee seeded by an older run still carries that run's location
    # list, and a template seeded then still carries its headcount: ON
    # CONFLICT DO NOTHING left both untouched, so correct them explicitly.
    #
    # Per employee, not one blanket UPDATE. This used to force the whole
    # roster to [LOCATION_ID], which was right when the demo had one location
    # and would now flatten the Downtown/Uptown/both split on every seed run.
    for emp_idx, emp_id in enumerate(EMPLOYEE_IDS):
        await db.execute(
            update(Employee)
            .where(Employee.company_id == COMPANY_ID, Employee.id == emp_id)
            .values(location_ids=_employee_location_ids(emp_idx))
        )
    for tpl_id, schedule in (
        (SHIFT_TEMPLATE_ID, _weekly_schedule()),
        (SHIFT_TEMPLATE_ID_UPTOWN, _weekly_schedule(floor_headcount=1)),
    ):
        await db.execute(
            update(ShiftTemplate)
            .where(ShiftTemplate.id == tpl_id)
            .values(weekly_schedule=schedule)
        )

    if removed:
        print("Pruned pre-free-plan demo rows: " + ", ".join(removed))


async def main() -> None:
    if settings.ENV == "production" and not settings.DEMO_SEED_PASSWORD:
        raise SystemExit(
            "Refusing to seed: ENV=production but DEMO_SEED_PASSWORD is unset, "
            "which would create the demo accounts with the well-known default "
            "password. Set it from AWS Secrets Manager "
            "(wizscheduler/prod/DEMO_SEED_PASSWORD) and re-run."
        )

    async with async_session_factory() as db:
        db: AsyncSession

        # --- Ownership Group ---
        await db.execute(
            text(
                "INSERT INTO ownership_groups (id, name) "
                "VALUES (:id, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": OWNERSHIP_GROUP_ID, "name": "Acme Corp Group"},
        )

        # --- Company ---
        await db.execute(
            text(
                "INSERT INTO companies (id, ownership_group_id, name, slug) "
                "VALUES (:id, :ownership_group_id, :name, :slug) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": COMPANY_ID,
                "ownership_group_id": OWNERSHIP_GROUP_ID,
                "name": "Acme Corp",
                "slug": "acme-corp",
            },
        )

        # --- Users ---
        hashed_pw_manager = _hash(DEMO_PASSWORD)
        hashed_pw_employee = _hash(DEMO_PASSWORD)
        for uid, email, full_name, role, hashed_pw in [
            (MANAGER_USER_ID, "abc@example.com", "Manager User", "manager", hashed_pw_manager),
            (EMPLOYEE_USER_ID, "employee1@example.com", "Employee One", "employee", hashed_pw_employee),
        ]:
            await db.execute(
                text(
                    "INSERT INTO users (id, company_id, email, hashed_password, full_name, user_role) "
                    "VALUES (:id, :company_id, :email, :hashed_password, :full_name, :user_role) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": uid,
                    "company_id": COMPANY_ID,
                    "email": email,
                    "hashed_password": hashed_pw,
                    "full_name": full_name,
                    "user_role": role,
                },
            )

        # --- Roles ---
        for rid, name, desc in [
            (ROLE_FLOOR_ID, "Floor Associate", "General floor duties"),
            (ROLE_LEAD_ID, "Team Lead", "Shift supervision"),
        ]:
            await db.execute(
                text(
                    "INSERT INTO roles (id, company_id, name, description) "
                    "VALUES (:id, :company_id, :name, :description) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": rid, "company_id": COMPANY_ID, "name": name, "description": desc},
            )

        # --- Region ---
        await db.execute(
            text(
                "INSERT INTO regions (id, company_id, name) "
                "VALUES (:id, :company_id, :name) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": REGION_ID, "company_id": COMPANY_ID, "name": "East Coast"},
        )

        # --- Location ---
        await db.execute(
            text(
                "INSERT INTO locations (id, company_id, region_id, name, timezone) "
                "VALUES (:id, :company_id, :region_id, :name, :timezone) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": LOCATION_ID,
                "company_id": COMPANY_ID,
                "region_id": REGION_ID,
                "name": "Downtown Store",
                "timezone": "America/New_York",
            },
        )
        await db.execute(
            text(
                "INSERT INTO locations (id, company_id, region_id, name, timezone) "
                "VALUES (:id, :company_id, :region_id, :name, :timezone) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": LOCATION_ID_UPTOWN,
                "company_id": COMPANY_ID,
                "region_id": REGION_ID,
                "name": "Uptown Store",
                # Same zone as Downtown on purpose: a cross-location overlap
                # is only a real double-booking if both are in one timezone,
                # and that is the case availability_draft has to catch.
                "timezone": "America/New_York",
            },
        )

        # --- Employees ---
        # 21 names, one per EMPLOYEE_IDS entry. Distinct first+last pairs so
        # the derived e-mail addresses stay unique.
        employee_names = [
            "Alice Johnson",
            "Bob Smith",
            "Carol Davis",
            "Dan Wilson",
            "Erin Nakamura",
            "Frank Okafor",
            "Grace Lindqvist",
            "Hugo Martins",
            "Isabel Ferrer",
            "Jamal Haddad",
            "Kiara Devlin",
            "Liam Petrov",
            "Maya Krishnan",
            "Noah Bergstrom",
            "Olivia Santos",
            "Priya Raman",
            "Quentin Morris",
            "Rosa Delgado",
            "Sam Whitfield",
            "Tara Nwosu",
            "Uma Chandra",
        ]
        assert len(employee_names) == len(EMPLOYEE_IDS), (
            f"{len(employee_names)} names for {len(EMPLOYEE_IDS)} employee ids"
        )
        for i, (eid, name) in enumerate(zip(EMPLOYEE_IDS, employee_names)):
            user_id = EMPLOYEE_USER_ID if i == 0 else None
            await db.execute(
                text(
                    "INSERT INTO employees (id, company_id, user_id, full_name, email, location_ids) "
                    "VALUES (:id, :company_id, :user_id, :full_name, :email, CAST(:location_ids AS jsonb)) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": eid,
                    "company_id": COMPANY_ID,
                    "user_id": user_id,
                    "full_name": name,
                    "email": f"{name.lower().replace(' ', '.')}@example.com",
                    "location_ids": json.dumps(_employee_location_ids(i)),
                },
            )

            # --- Employee-Company assignment (default: assigned to their own company) ---
            ec_id = f"ec{str(i).zfill(6)}"
            await db.execute(
                text(
                    "INSERT INTO employee_companies (id, employee_id, company_id) "
                    "VALUES (:id, :employee_id, :company_id) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": ec_id,
                    "employee_id": eid,
                    "company_id": COMPANY_ID,
                },
            )

        # --- Employee Roles (mix of assignments) ---
        role_assignments = _role_assignments()
        # Cleared and rewritten rather than ON CONFLICT DO NOTHING.
        #
        # These ids used to be POSITIONAL (er000000, er000001, ... by index
        # into role_assignments), which is only stable while the list is. When
        # the roster grew from 4 employees to 21 the list changed shape, every
        # id after the first few pointed at a different (employee, role) pair,
        # and DO NOTHING kept the stale row — leaving empl0003 holding a Team
        # Lead it should not have and empl0004 missing the one it should. The
        # count still came to 28, so nothing looked wrong.
        #
        # Ids are keyed on (employee, role) below, so they are stable across
        # roster changes; the delete clears the old positional rows, which
        # would otherwise survive under the new scheme as duplicates.
        await db.execute(
            delete(EmployeeRole).where(EmployeeRole.company_id == COMPANY_ID)
        )
        for emp_idx, role_id, skill in role_assignments:
            await db.execute(
                text(
                    "INSERT INTO employee_roles (id, company_id, employee_id, role_id, skill_level) "
                    "VALUES (:id, :company_id, :employee_id, :role_id, :skill_level) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": f"er{emp_idx:03d}{role_id[-2:]}",
                    "company_id": COMPANY_ID,
                    "employee_id": EMPLOYEE_IDS[emp_idx],
                    "role_id": role_id,
                    "skill_level": skill,
                },
            )

        # --- Shift Template (Mon-Fri) ---
        weekly_schedule = _weekly_schedule()

        await db.execute(
            text(
                "INSERT INTO shift_templates (id, company_id, location_id, name, weekly_schedule) "
                "VALUES (:id, :company_id, :location_id, :name, CAST(:weekly_schedule AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": SHIFT_TEMPLATE_ID,
                "company_id": COMPANY_ID,
                "location_id": LOCATION_ID,
                "name": "Weekday Standard",
                "weekly_schedule": str(weekly_schedule).replace("'", '"'),
            },
        )

        uptown_schedule = _weekly_schedule(floor_headcount=1)
        await db.execute(
            text(
                "INSERT INTO shift_templates (id, company_id, location_id, name, weekly_schedule) "
                "VALUES (:id, :company_id, :location_id, :name, CAST(:weekly_schedule AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": SHIFT_TEMPLATE_ID_UPTOWN,
                "company_id": COMPANY_ID,
                "location_id": LOCATION_ID_UPTOWN,
                "name": "Weekday Light",
                "weekly_schedule": str(uptown_schedule).replace("'", '"'),
            },
        )

        # --- Availability (every day, 9am-5pm, whole roster) ---
        await _seed_availability(db)

        # Run last, inside the same transaction: the inserts above have
        # re-established the intended shape, so anything left over from an
        # older, larger demo can now go.
        await _prune_over_limit_demo_data(db)

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
