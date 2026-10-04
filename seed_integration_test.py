"""
Integration test seeding script for WizScheduler.

Idempotent seeding of a dedicated test company in production RDS.
Fetches credentials from AWS Parameter Store, seeds 50+ employees across 2 locations
with realistic signal distributions (normally distributed pay rates/seniority, random affinities),
and refreshes availability windows for the next 30 days before each test run.

Designed to run in AWS Lambda with proper VPC/RDS access.

Usage (local, for testing):
    python seed_integration_test.py

Usage (Lambda):
    Handler: seed_integration_test.lambda_handler
    Environment variables:
      DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (or fetch from Parameter Store)
      TEST_COMPANY_ID (optional, auto-generated if omitted)

Environment Variables:
    - AWS_REGION (default: us-east-1)
    - DB_PARAM_PATH (default: /wizscheduler/test-db) — Parameter Store path prefix
    - TEST_COMPANY_ID (optional, default: generated deterministically)
    - EMPLOYEE_COUNT (default: 55)
    - AVAILABILITY_DAYS (default: 30)
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional

import boto3
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# --- Configuration ---

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DB_PARAM_PATH = os.environ.get("DB_PARAM_PATH", "/wizscheduler/test-db")
TEST_COMPANY_ID = os.environ.get("TEST_COMPANY_ID", "integ-test-001")
TEST_OG_ID = "integ-og-001"

EMPLOYEE_COUNT = int(os.environ.get("EMPLOYEE_COUNT", "55"))
AVAILABILITY_DAYS = int(os.environ.get("AVAILABILITY_DAYS", "30"))
BOTH_LOCATIONS_COUNT = 40  # Employees at both locations

assert BOTH_LOCATIONS_COUNT <= EMPLOYEE_COUNT, "BOTH_LOCATIONS_COUNT must be ≤ EMPLOYEE_COUNT"
location_only_count = EMPLOYEE_COUNT - BOTH_LOCATIONS_COUNT


# --- AWS Parameter Store ---

def get_db_credentials() -> dict[str, str]:
    """Fetch RDS credentials from Parameter Store.

    Expects:
      - {DB_PARAM_PATH}/host
      - {DB_PARAM_PATH}/port
      - {DB_PARAM_PATH}/username
      - {DB_PARAM_PATH}/password
      - {DB_PARAM_PATH}/database

    Fails hard if credentials are missing (no insecure fallback).
    """
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    params = [
        f"{DB_PARAM_PATH}/host",
        f"{DB_PARAM_PATH}/port",
        f"{DB_PARAM_PATH}/username",
        f"{DB_PARAM_PATH}/password",
        f"{DB_PARAM_PATH}/database",
    ]

    response = ssm.get_parameters(Names=params, WithDecryption=True)
    param_dict = {p["Name"]: p["Value"] for p in response["Parameters"]}

    # Fail hard if any required credential is missing
    required_keys = [
        f"{DB_PARAM_PATH}/host",
        f"{DB_PARAM_PATH}/port",
        f"{DB_PARAM_PATH}/username",
        f"{DB_PARAM_PATH}/password",
        f"{DB_PARAM_PATH}/database",
    ]
    missing = [k for k in required_keys if k not in param_dict]
    if missing:
        raise ValueError(
            f"Missing required Parameter Store credentials: {missing}. "
            f"Set them in AWS Parameter Store before running."
        )

    return {
        "host": param_dict[f"{DB_PARAM_PATH}/host"],
        "port": param_dict[f"{DB_PARAM_PATH}/port"],
        "username": param_dict[f"{DB_PARAM_PATH}/username"],
        "password": param_dict[f"{DB_PARAM_PATH}/password"],
        "database": param_dict[f"{DB_PARAM_PATH}/database"],
    }


# --- Database Connection ---

async def get_db_session(credentials: dict[str, str]) -> AsyncGenerator[AsyncSession, None]:
    """Create async SQLAlchemy session."""
    db_url = (
        f"postgresql+asyncpg://{credentials['username']}:{credentials['password']}"
        f"@{credentials['host']}:{credentials['port']}/{credentials['database']}"
    )
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        yield session

    await engine.dispose()


# --- Data Generation ---

def _generate_employee_names(count: int) -> list[str]:
    """Generate realistic diverse names."""
    first_names = [
        "Alice", "Bob", "Carol", "Dan", "Erin", "Frank", "Grace", "Hugo",
        "Isabel", "Jamal", "Kiara", "Liam", "Maya", "Noah", "Olivia", "Priya",
        "Quentin", "Rosa", "Sam", "Tara", "Uma", "Victor", "Wendy", "Xavier",
        "Yuki", "Zoe", "Aaron", "Bella", "Carlos", "Diana", "Ethan", "Fiona",
        "George", "Hannah", "Ivan", "Jade", "Kevin", "Lily", "Marcus", "Nora",
        "Oscar", "Paula", "Quinn", "Rachel", "Steve", "Thea", "Ulysses", "Violet",
        "William", "Xena", "Yara", "Zack", "Adrian", "Brenda", "Chloe", "Derek",
        "Eleanor", "Felix", "Gemma",
    ]
    last_names = [
        "Johnson", "Smith", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
        "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
        "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
        "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
        "Young", "Allen", "King", "Wright", "Scott", "Torres", "Peterson", "Phillips",
        "Campbell", "Parker", "Evans", "Edwards", "Collins", "Reyes", "Stewart", "Morris",
        "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper",
    ]

    names = []
    for i in range(count):
        first = first_names[i % len(first_names)]
        last = last_names[(i // len(first_names)) % len(last_names)]
        names.append(f"{first} {last}")
    return names


def _generate_signal_distributions(count: int) -> dict[str, list[Any]]:
    """Generate normally distributed scheduling signals.

    Returns:
      {
        "pay_rates": [float, ...],        # normally distributed, 15–35/hr
        "seniority_ranks": [int, ...],    # normally distributed, 1–10
        "affinity_pairs": [(emp_idx, target_idx, level), ...],  # random positive/negative
      }
    """
    import random
    import statistics

    # Pay rates: mean=20, std=5, clipped to 15–35
    pay_rates = [
        max(15.0, min(35.0, random.gauss(20, 5)))
        for _ in range(count)
    ]

    # Seniority: mean=5.5, std=2.5, clipped to 1–10
    seniority = [
        max(1, min(10, int(round(random.gauss(5.5, 2.5)))))
        for _ in range(count)
    ]

    # Affinities: 30% of pairs get an affinity (positive or negative)
    affinity_pairs = []
    for i in range(count):
        for j in range(i + 1, count):
            if random.random() < 0.30:  # 30% chance
                level = random.randint(1, 5) if random.random() < 0.6 else -random.randint(1, 3)
                affinity_pairs.append((i, j, level))
                affinity_pairs.append((j, i, level))  # Bidirectional

    return {
        "pay_rates": pay_rates,
        "seniority_ranks": seniority,
        "affinity_pairs": affinity_pairs,
    }


# --- Seeding Logic ---

async def seed_integration_test(
    db: AsyncSession,
    employee_count: int = EMPLOYEE_COUNT,
    availability_days: int = AVAILABILITY_DAYS,
) -> dict[str, str]:
    """Seed integration test company and data. Idempotent.

    Returns: {"company_id": ..., "location_1_id": ..., "location_2_id": ..., ...}
    """
    from sqlalchemy.orm import Session

    # Generate names and signals
    names = _generate_employee_names(employee_count)
    signals = _generate_signal_distributions(employee_count)

    # Deterministic IDs
    region_id = "integ-reg-001"
    location_1_id = "integ-loc-001"
    location_2_id = "integ-loc-002"
    role_floor_id = "integ-role-floor"
    role_lead_id = "integ-role-lead"

    print(f"Seeding integration test: {employee_count} employees, {availability_days} days availability")

    # --- Ownership Group (idempotent) ---
    await db.execute(
        text(
            "INSERT INTO ownership_groups (id, name) "
            "VALUES (:id, :name) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": TEST_OG_ID, "name": "Integration Test Org"},
    )
    print("✓ Ownership group")

    # --- Company (idempotent) ---
    await db.execute(
        text(
            "INSERT INTO companies (id, ownership_group_id, name, slug) "
            "VALUES (:id, :og_id, :name, :slug) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": TEST_COMPANY_ID,
            "og_id": TEST_OG_ID,
            "name": "Integration Test Company",
            "slug": "integ-test-company",
        },
    )
    print("✓ Company")

    # --- Region ---
    await db.execute(
        text(
            "INSERT INTO regions (id, company_id, name) "
            "VALUES (:id, :company_id, :name) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": region_id, "company_id": TEST_COMPANY_ID, "name": "Test Region"},
    )
    print("✓ Region")

    # --- Locations ---
    for loc_id, loc_name, tz in [
        (location_1_id, "Test Location 1", "America/New_York"),
        (location_2_id, "Test Location 2", "America/Los_Angeles"),
    ]:
        await db.execute(
            text(
                "INSERT INTO locations (id, company_id, region_id, name, timezone) "
                "VALUES (:id, :company_id, :region_id, :name, :timezone) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": loc_id,
                "company_id": TEST_COMPANY_ID,
                "region_id": region_id,
                "name": loc_name,
                "timezone": tz,
            },
        )
    print(f"✓ Locations ({location_1_id}, {location_2_id})")

    # --- Roles ---
    for role_id, role_name in [
        (role_floor_id, "Floor Associate"),
        (role_lead_id, "Team Lead"),
    ]:
        await db.execute(
            text(
                "INSERT INTO roles (id, company_id, name, description) "
                "VALUES (:id, :company_id, :name, :description) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": role_id,
                "company_id": TEST_COMPANY_ID,
                "name": role_name,
                "description": f"{role_name} for integration testing",
            },
        )
    print("✓ Roles")

    # --- Employees (idempotent, update if exists) ---
    import uuid
    employee_ids = [str(uuid.uuid4()) for _ in range(employee_count)]

    for emp_idx, (emp_id, name, pay_rate, seniority) in enumerate(
        zip(
            employee_ids,
            names,
            signals["pay_rates"],
            signals["seniority_ranks"],
        )
    ):
        # Assign to locations
        if emp_idx < BOTH_LOCATIONS_COUNT:
            location_ids = [location_1_id, location_2_id]
        elif emp_idx < BOTH_LOCATIONS_COUNT + location_only_count // 2:
            location_ids = [location_1_id]
        else:
            location_ids = [location_2_id]

        loc_json = json.dumps(location_ids)

        await db.execute(
            text(
                "INSERT INTO employees "
                "(id, company_id, full_name, email, location_ids, pay_rate, seniority_rank) "
                "VALUES (:id, :company_id, :name, :email, CAST(:locations AS jsonb), :pay, :sen) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  full_name=EXCLUDED.full_name, "
                "  email=EXCLUDED.email, "
                "  location_ids=EXCLUDED.location_ids, "
                "  pay_rate=EXCLUDED.pay_rate, "
                "  seniority_rank=EXCLUDED.seniority_rank "
            ),
            {
                "id": emp_id,
                "company_id": TEST_COMPANY_ID,
                "name": name,
                "email": f"{name.lower().replace(' ', '.')}@integ-test.local",
                "locations": loc_json,
                "pay": round(pay_rate, 2),
                "sen": seniority,
            },
        )

    print(f"✓ Employees ({employee_count} total: {BOTH_LOCATIONS_COUNT} at both locations)")

    # --- Employee Roles (idempotent) ---
    for emp_idx, emp_id in enumerate(employee_ids):
        # Every employee gets Floor Associate; every 3rd gets Team Lead
        roles_to_assign = [role_floor_id]
        if emp_idx % 3 == 0:
            roles_to_assign.append(role_lead_id)

        for role_id in roles_to_assign:
            skill_level = max(1, min(5, int(round(signals["seniority_ranks"][emp_idx] / 2))))

            await db.execute(
                text(
                    "INSERT INTO employee_roles (id, company_id, employee_id, role_id, skill_level) "
                    "VALUES (:id, :company_id, :emp_id, :role_id, :skill) "
                    "ON CONFLICT (id) DO UPDATE SET skill_level=EXCLUDED.skill_level"
                ),
                {
                    "id": f"{emp_id}-{role_id}",
                    "company_id": TEST_COMPANY_ID,
                    "emp_id": emp_id,
                    "role_id": role_id,
                    "skill": skill_level,
                },
            )

    print("✓ Role assignments")

    # --- Employee Affinities (idempotent) ---
    for emp_idx_a, emp_idx_b, level in signals["affinity_pairs"]:
        await db.execute(
            text(
                "INSERT INTO employee_affinities "
                "(id, company_id, employee_id, target_employee_id, level, entry_date) "
                "VALUES (:id, :company_id, :emp_a, :emp_b, :level, :date) "
                "ON CONFLICT (id) DO UPDATE SET level=EXCLUDED.level"
            ),
            {
                "id": f"{employee_ids[emp_idx_a]}-{employee_ids[emp_idx_b]}",
                "company_id": TEST_COMPANY_ID,
                "emp_a": employee_ids[emp_idx_a],
                "emp_b": employee_ids[emp_idx_b],
                "level": level,
                "date": datetime.now(timezone.utc).date(),
            },
        )

    print(f"✓ Affinities ({len(signals['affinity_pairs'])} pairs)")

    # --- Availability (refresh for next N days) ---
    # DELETE existing, then INSERT fresh for reproducibility
    # Scoped to test company only to prevent accidental data loss
    result = await db.execute(
        text(
            "DELETE FROM employee_availability "
            "WHERE company_id = :company_id "
            "AND company_id IN (SELECT id FROM companies WHERE slug LIKE 'integ%')"
        ),
        {"company_id": TEST_COMPANY_ID},
    )
    if result.rowcount > 0:
        print(f"  (refreshed {result.rowcount} existing availability windows)")

    start_date = datetime.now(timezone.utc).date()
    rows = []
    for emp_idx, emp_id in enumerate(employee_ids):
        for day_offset in range(availability_days):
            shift_date = start_date + timedelta(days=day_offset)
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "company_id": TEST_COMPANY_ID,
                    "employee_id": emp_id,
                    "year": shift_date.year,
                    "month": shift_date.month,
                    "day": shift_date.day,
                    "start_time": datetime(
                        shift_date.year,
                        shift_date.month,
                        shift_date.day,
                        9,  # 9am
                        0,
                        tzinfo=timezone.utc,
                    ),
                    "end_time": datetime(
                        shift_date.year,
                        shift_date.month,
                        shift_date.day,
                        17,  # 5pm
                        0,
                        tzinfo=timezone.utc,
                    ),
                }
            )

    # Insert in chunks
    CHUNK_SIZE = 2000
    for i in range(0, len(rows), CHUNK_SIZE):
        # Use raw SQL for bulk inserts to avoid importing backend.models
        stmt = text(
            "INSERT INTO employee_availability "
            "(id, company_id, employee_id, year, month, day, start_time, end_time) "
            "VALUES (:id, :company_id, :employee_id, :year, :month, :day, :start_time, :end_time)"
        )
        for row in rows[i:i+CHUNK_SIZE]:
            await db.execute(stmt, row)

    print(f"✓ Availability ({len(rows)} slots, {availability_days} days)")

    await db.commit()

    return {
        "company_id": TEST_COMPANY_ID,
        "ownership_group_id": TEST_OG_ID,
        "location_1_id": location_1_id,
        "location_2_id": location_2_id,
        "employee_count": employee_count,
        "both_locations_count": BOTH_LOCATIONS_COUNT,
    }


# --- Main Entry Point ---

async def main() -> None:
    """Standalone entry point for testing."""
    # Fail hard if credentials are unavailable (no insecure fallback)
    credentials = get_db_credentials()

    async for db in get_db_session(credentials):
        result = await seed_integration_test(db)
        print(f"\n✅ Seeding complete!")
        print(json.dumps(result, indent=2))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda handler."""
    try:
        credentials = get_db_credentials()

        async def run():
            async for db in get_db_session(credentials):
                return await seed_integration_test(db)

        result = asyncio.run(run())
        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


if __name__ == "__main__":
    asyncio.run(main())
