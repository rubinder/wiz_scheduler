#!/usr/bin/env python3
"""
Setup test data for scheduling signals verification (PR #135).
Run from repo root: python setup_scheduling_signals_test.py

Creates:
- Location with shift templates
- Roles
- Employees with varying pay rates and seniority (to test pay rate, overtime, seniority signals)
- Employee availability
"""
import asyncio
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from backend.database import async_session_factory, engine
from backend.models import (
    User, Company, OwnershipGroup, Region, Location, Role, ShiftTemplate,
    Employee, EmployeeRole, EmployeeAvailability
)
from sqlalchemy import select

async def setup_test_data():
    """Setup comprehensive test data for scheduling signals testing."""

    async with async_session_factory() as session:
        print("🔧 Setting up scheduling signals test data...\n")

        # 1. Find test user
        result = await session.execute(
            select(User).where(User.email == "test-scheduler-signals@wizscheduler.com")
        )
        user = result.scalars().first()
        if not user:
            print("❌ Test user not found")
            return False

        print(f"✅ Found test user: {user.email}")

        # 2. Add AI credits
        result = await session.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == user.company_id)
        )
        ownership_group = result.scalars().first()
        if ownership_group:
            ownership_group.ai_credits_usd = 1000.0
            session.add(ownership_group)

        # 3. Get or create region
        result = await session.execute(
            select(Region).where(Region.company_id == user.company_id).limit(1)
        )
        region = result.scalars().first()
        if not region:
            region = Region(company_id=user.company_id, name="Test Region")
            session.add(region)
            await session.flush()

        print(f"✅ Region: {region.name}")

        # 4. Get or create location
        result = await session.execute(
            select(Location).where(Location.company_id == user.company_id).limit(1)
        )
        location = result.scalars().first()
        if not location:
            location = Location(
                company_id=user.company_id,
                region_id=region.id,
                name="Test Cafe",
                timezone="America/New_York"
            )
            session.add(location)
            await session.flush()

        print(f"✅ Location: {location.name} (ID: {location.id})")

        # 5. Create roles if not exist
        result = await session.execute(
            select(Role).where(Role.company_id == user.company_id)
        )
        roles = result.scalars().all()

        role_map = {}
        if not roles:
            for role_name in ["Cashier", "Barista", "Manager"]:
                role = Role(company_id=user.company_id, name=role_name)
                session.add(role)
                role_map[role_name] = role
            await session.flush()
        else:
            for role in roles:
                role_map[role.name] = role

        print(f"✅ Roles: {', '.join(role_map.keys())}")

        # 6. Create shift templates if not exist
        result = await session.execute(
            select(ShiftTemplate).where(ShiftTemplate.location_id == location.id)
        )
        templates = result.scalars().all()

        if not templates:
            # Create morning shift template
            morning = ShiftTemplate(
                company_id=user.company_id,
                location_id=location.id,
                name="Morning",
                weekly_schedule={
                    "Monday": {"start": "06:00", "end": "14:00"},
                    "Tuesday": {"start": "06:00", "end": "14:00"},
                    "Wednesday": {"start": "06:00", "end": "14:00"},
                    "Thursday": {"start": "06:00", "end": "14:00"},
                    "Friday": {"start": "06:00", "end": "14:00"},
                    "Saturday": None,
                    "Sunday": None
                }
            )
            session.add(morning)

            # Create afternoon shift template
            afternoon = ShiftTemplate(
                company_id=user.company_id,
                location_id=location.id,
                name="Afternoon",
                weekly_schedule={
                    "Monday": {"start": "14:00", "end": "22:00"},
                    "Tuesday": {"start": "14:00", "end": "22:00"},
                    "Wednesday": {"start": "14:00", "end": "22:00"},
                    "Thursday": {"start": "14:00", "end": "22:00"},
                    "Friday": {"start": "14:00", "end": "22:00"},
                    "Saturday": None,
                    "Sunday": None
                }
            )
            session.add(afternoon)

            await session.flush()
            templates = [morning, afternoon]

        print(f"✅ Shift templates: {', '.join(t.name for t in templates)}")

        # 7. Create test employees with different pay rates and seniority
        result = await session.execute(
            select(Employee).where(Employee.company_id == user.company_id)
        )
        existing_employees = result.scalars().all()

        employees_to_create = [
            ("Alice Senior", "alice@test.com", 25.00, 10),    # Senior, high pay
            ("Bob Mid", "bob@test.com", 18.00, 3),            # Mid-level
            ("Charlie Junior", "charlie@test.com", 16.00, 0),  # Junior, low pay
        ]

        employees = []
        for name, email, pay_rate, years_employed in employees_to_create:
            # Check if exists
            result = await session.execute(
                select(Employee).where(
                    Employee.company_id == user.company_id,
                    Employee.name == name
                )
            )
            emp = result.scalars().first()

            if not emp:
                emp = Employee(
                    company_id=user.company_id,
                    name=name,
                    email=email,
                    pay_rate_usd=pay_rate,
                    years_employed=years_employed,
                    is_active=True
                )
                session.add(emp)
                await session.flush()

                # Assign role
                emp_role = EmployeeRole(
                    employee_id=emp.id,
                    role_id=list(role_map.values())[0].id
                )
                session.add(emp_role)

            employees.append(emp)

        await session.flush()

        print(f"✅ Employees created:")
        for emp in employees:
            print(f"   - {emp.name}: ${emp.pay_rate_usd}/hr, {emp.years_employed} years employed")

        # 8. Set employee availability for next week
        today = datetime.now(timezone.utc).date()
        week_start = today + timedelta(days=(7 - today.weekday()))  # Next Monday

        for emp in employees:
            for day_offset in range(5):  # Monday-Friday
                shift_date = week_start + timedelta(days=day_offset)

                # Check if availability exists
                result = await session.execute(
                    select(EmployeeAvailability).where(
                        EmployeeAvailability.employee_id == emp.id,
                        EmployeeAvailability.date == shift_date
                    )
                )
                avail = result.scalars().first()

                if not avail:
                    availability = EmployeeAvailability(
                        employee_id=emp.id,
                        date=shift_date,
                        is_available=True,
                        start_time=time(6, 0),
                        end_time=time(22, 0)
                    )
                    session.add(availability)

        print(f"\n✅ Availability set for {week_start.strftime('%A, %B %d, %Y')} - {(week_start + timedelta(days=4)).strftime('%A, %B %d, %Y')}")

        # Commit all changes
        await session.commit()

        print(f"\n✅ Test data setup complete!")
        print(f"\n📋 Summary:")
        print(f"   Account: test-scheduler-signals@wizscheduler.com")
        print(f"   AI Credits: $1000.00")
        print(f"   Location: {location.name}")
        print(f"   Employees: {len(employees)}")
        print(f"   Ready for schedule generation test!")

        return True

if __name__ == "__main__":
    try:
        success = asyncio.run(setup_test_data())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
