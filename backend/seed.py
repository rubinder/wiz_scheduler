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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import async_session_factory, engine
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
    ShiftTemplate,
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
LOCATION_ID_2 = "locn0002"

EMPLOYEE_IDS = [f"empl{str(i).zfill(4)}" for i in range(1, 18)]

SHIFT_TEMPLATE_ID = "shft0001"
SHIFT_TEMPLATE_ID_2 = "shft0002"


def _hash(password: str) -> str:
    return pwd_context.hash(password)


def _current_week_monday() -> datetime:
    """Return the Monday of the current week at midnight ET."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


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

        # --- Location 2 ---
        await db.execute(
            text(
                "INSERT INTO locations (id, company_id, region_id, name, timezone) "
                "VALUES (:id, :company_id, :region_id, :name, :timezone) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": LOCATION_ID_2,
                "company_id": COMPANY_ID,
                "region_id": REGION_ID,
                "name": "Uptown Store",
                "timezone": "America/New_York",
            },
        )

        # --- Employees ---
        employee_names = [
            "Alice Johnson",
            "Bob Smith",
            "Carol Davis",
            "Dan Wilson",
            "Eve Martinez",
            "Frank Brown",
            "Grace Lee",
            "Hannah Kim",
            "Isaac Torres",
            "Julia Chen",
            "Kevin Patel",
            "Laura Nguyen",
            "Marcus Wright",
            "Nina Rodriguez",
            "Oscar Yamamoto",
            "Paula Singh",
            "Quinn Murphy",
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
                    "location_ids": json.dumps([LOCATION_ID, LOCATION_ID_2]),
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
            (0, ROLE_FLOOR_ID, 3),
            (0, ROLE_LEAD_ID, 2),
            (1, ROLE_FLOOR_ID, 2),
            (2, ROLE_FLOOR_ID, 4),
            (2, ROLE_LEAD_ID, 3),
            (3, ROLE_FLOOR_ID, 2),
            (4, ROLE_FLOOR_ID, 1),
            (5, ROLE_LEAD_ID, 4),
            (6, ROLE_FLOOR_ID, 3),
            (7, ROLE_FLOOR_ID, 3),
            (7, ROLE_LEAD_ID, 2),
            (8, ROLE_FLOOR_ID, 2),
            (9, ROLE_FLOOR_ID, 4),
            (9, ROLE_LEAD_ID, 3),
            (10, ROLE_FLOOR_ID, 2),
            (11, ROLE_FLOOR_ID, 3),
            (11, ROLE_LEAD_ID, 2),
            (12, ROLE_FLOOR_ID, 1),
            (13, ROLE_FLOOR_ID, 4),
            (13, ROLE_LEAD_ID, 4),
            (14, ROLE_FLOOR_ID, 3),
            (15, ROLE_FLOOR_ID, 2),
            (15, ROLE_LEAD_ID, 1),
            (16, ROLE_FLOOR_ID, 3),
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
        weekly_schedule = []
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            weekly_schedule.append(
                {
                    "day": day,
                    "role_name": "Floor Associate",
                    "role_id": ROLE_FLOOR_ID,
                    "headcount": 3,
                    "start_time": "09:00",
                    "end_time": "17:00",
                }
            )
            weekly_schedule.append(
                {
                    "day": day,
                    "role_name": "Team Lead",
                    "role_id": ROLE_LEAD_ID,
                    "headcount": 1,
                    "start_time": "09:00",
                    "end_time": "17:00",
                }
            )

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

        # --- Shift Template 2 (Uptown Store, same schedule) ---
        await db.execute(
            text(
                "INSERT INTO shift_templates (id, company_id, location_id, name, weekly_schedule) "
                "VALUES (:id, :company_id, :location_id, :name, CAST(:weekly_schedule AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": SHIFT_TEMPLATE_ID_2,
                "company_id": COMPANY_ID,
                "location_id": LOCATION_ID_2,
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

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
