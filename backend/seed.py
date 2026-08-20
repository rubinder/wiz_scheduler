"""
Idempotent seed script for WizScheduler.

Usage:
    cd backend && python seed.py

Uses deterministic string IDs so re-runs are safe (ON CONFLICT DO NOTHING).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from sqlalchemy import delete, or_, text, update
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

# The demo tenant has no Stripe subscription, so services.plan resolves it to
# the free plan. Keep it AT OR UNDER the free caps: past them, get_plan_state
# sets over_limit, which turns off local generation too and leaves the demo
# unable to produce a schedule at all. One under the employee cap so a visitor
# can still add someone and see the limit land honestly.
EMPLOYEE_IDS = [f"empl{str(i).zfill(4)}" for i in range(1, 5)]

SHIFT_TEMPLATE_ID = "shft0001"

# Seeded by earlier versions of this file, before the free plan existed. Listed
# so _prune_over_limit_demo_data can remove them from an already-seeded
# database; a fresh seed never creates them.
LEGACY_LOCATION_IDS = ["locn0002"]
LEGACY_SHIFT_TEMPLATE_IDS = ["shft0002"]
LEGACY_EMPLOYEE_IDS = [f"empl{str(i).zfill(4)}" for i in range(5, 18)]


def _hash(password: str) -> str:
    return pwd_context.hash(password)


def _current_week_monday() -> datetime:
    """Return the Monday of the current week at midnight ET."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


def _weekly_schedule() -> list[dict]:
    """The demo location's weekly shift template.

    2 Floor Associates + 1 Team Lead per weekday: 3 of the 4 seeded employees,
    so the roster rotates and everyone gets a day off. Filling all four would
    leave the scheduler nothing to decide.
    """
    schedule: list[dict] = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        schedule.append(
            {
                "day": day,
                "role_name": "Floor Associate",
                "role_id": ROLE_FLOOR_ID,
                "headcount": 2,
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
        (SpecialHoursDay, "special_hours_days"),
    ):
        await _run(delete(model).where(model.location_id.in_(loc_ids)), label)
    await _run(
        delete(Location).where(
            Location.company_id == COMPANY_ID, Location.id.in_(loc_ids)
        ),
        "locations",
    )

    # The surviving employees still list the removed location, and the
    # surviving template still carries the old headcount: ON CONFLICT DO
    # NOTHING left both untouched, so correct them explicitly.
    await db.execute(
        update(Employee)
        .where(Employee.company_id == COMPANY_ID, Employee.id.in_(EMPLOYEE_IDS))
        .values(location_ids=[LOCATION_ID])
    )
    await db.execute(
        update(ShiftTemplate)
        .where(ShiftTemplate.id == SHIFT_TEMPLATE_ID)
        .values(weekly_schedule=_weekly_schedule())
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

        # --- Employees ---
        employee_names = [
            "Alice Johnson",
            "Bob Smith",
            "Carol Davis",
            "Dan Wilson",
        ]
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
                    "location_ids": json.dumps([LOCATION_ID]),
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
        role_assignments = [
            # (employee_index, role_id, skill_level)
            # Two of the four can lead, so the daily Team Lead slot can rotate
            # instead of pinning the same person to all five days.
            (0, ROLE_FLOOR_ID, 3),
            (0, ROLE_LEAD_ID, 2),
            (1, ROLE_FLOOR_ID, 2),
            (2, ROLE_FLOOR_ID, 4),
            (2, ROLE_LEAD_ID, 3),
            (3, ROLE_FLOOR_ID, 2),
        ]
        for ra_idx, (emp_idx, role_id, skill) in enumerate(role_assignments):
            er_id = f"er{str(ra_idx).zfill(6)}"
            await db.execute(
                text(
                    "INSERT INTO employee_roles (id, company_id, employee_id, role_id, skill_level) "
                    "VALUES (:id, :company_id, :employee_id, :role_id, :skill_level) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": er_id,
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

        # --- Availability (current week, Mon-Fri, 9am-5pm ET for all employees) ---
        monday = _current_week_monday()
        et_offset = timezone(timedelta(hours=-5))  # EST (simplified)

        for emp_idx, eid in enumerate(EMPLOYEE_IDS):
            for day_offset in range(5):  # Mon-Fri
                day_date = monday + timedelta(days=day_offset)
                start_dt = day_date.replace(hour=9, minute=0, second=0, tzinfo=et_offset)
                end_dt = day_date.replace(hour=17, minute=0, second=0, tzinfo=et_offset)
                avail_id = f"av{emp_idx}{day_offset}".ljust(8, "0")[:8]

                await db.execute(
                    text(
                        "INSERT INTO employee_availability "
                        "(id, company_id, employee_id, year, month, day, start_time, end_time) "
                        "VALUES (:id, :company_id, :employee_id, :year, :month, :day, :start_time, :end_time) "
                        "ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": avail_id,
                        "company_id": COMPANY_ID,
                        "employee_id": eid,
                        "year": day_date.year,
                        "month": day_date.month,
                        "day": day_date.day,
                        "start_time": start_dt,
                        "end_time": end_dt,
                    },
                )

        # Run last, inside the same transaction: the inserts above have
        # re-established the intended shape, so anything left over from an
        # older, larger demo can now go.
        await _prune_over_limit_demo_data(db)

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
