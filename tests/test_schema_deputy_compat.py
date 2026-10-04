"""Test schema compatibility for Deputy imports.

Verifies that the database schema supports all operations required by the
Deputy import process, including nullable columns, type conversions, and
Deputy-specific features like unavailability tracking.
"""

import pytest
from datetime import date, datetime, timezone
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    EmployeeRole,
    Location,
    Region,
    Role,
    EmployeeDayBlackout,
)


class TestDeputySchemaColumns:
    """Verify columns required for Deputy imports."""

    @pytest.mark.asyncio
    async def test_employee_deputy_nullable_fields(self, db: AsyncSession) -> None:
        """Employee should support Deputy import fields: external_id, email, hire_date."""
        inspector = inspect(Employee)
        columns = {col.name: col for col in inspector.columns}

        for field in ["external_id", "email", "hire_date"]:
            assert field in columns, f"{field} column missing"
            assert columns[field].nullable, f"{field} must be nullable for imports"

    @pytest.mark.asyncio
    async def test_location_deputy_nullable_fields(self, db: AsyncSession) -> None:
        """Location should support Deputy import fields."""
        inspector = inspect(Location)
        columns = {col.name: col for col in inspector.columns}

        for field in ["external_id", "address"]:
            assert field in columns
            assert columns[field].nullable

        # timezone is required (not nullable)
        assert "timezone" in columns

    @pytest.mark.asyncio
    async def test_employee_day_blackout_table_exists(self, db: AsyncSession) -> None:
        """EmployeeDayBlackout table should exist for Deputy recurring unavailability."""
        inspector = inspect(EmployeeDayBlackout)
        columns = {col.name: col for col in inspector.columns}

        assert "employee_id" in columns
        assert "day_of_week" in columns
        assert "start_time" in columns
        assert "end_time" in columns


class TestDeputyDataCreation:
    """Test creating records as Deputy imports would."""

    @pytest.mark.asyncio
    async def test_create_location_from_deputy(
        self, db: AsyncSession, test_company: Company, test_region: Region
    ) -> None:
        """Create location from Deputy 'Company' data."""
        # Deputy provides: external_id, name, address
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Downtown Store",
            address="456 Oak Ave, Springfield, IL",
            timezone="America/Chicago",  # Required; use company default or Deputy data
            external_id="deputy_comp_789",
        )
        db.add(loc)
        await db.flush()

        assert loc.id is not None
        assert loc.external_id == "deputy_comp_789"

    @pytest.mark.asyncio
    async def test_create_employee_from_deputy(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Create employee from Deputy 'Employee' record."""
        # Deputy provides: external_id, first_name, last_name, email, hire_date
        emp = Employee(
            company_id=test_company.id,
            full_name="Alice Johnson",
            email="alice@deputyapp.com",
            hire_date=date(2022, 3, 10),
            external_id="deputy_emp_456",
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.hire_date == date(2022, 3, 10)
        assert retrieved.external_id == "deputy_emp_456"

    @pytest.mark.asyncio
    async def test_create_role_from_deputy(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Create role from Deputy 'Trade' data."""
        role = Role(
            company_id=test_company.id,
            name="Barista",
            external_id="deputy_trade_123",
        )
        db.add(role)
        await db.flush()

        assert role.external_id == "deputy_trade_123"

    @pytest.mark.asyncio
    async def test_create_employee_role_from_deputy(
        self,
        db: AsyncSession,
        test_company: Company,
        test_employee: Employee,
    ) -> None:
        """Create EmployeeRole from Deputy assignment."""
        role = Role(
            company_id=test_company.id,
            name="Barista",
        )
        db.add(role)
        await db.flush()

        emp_role = EmployeeRole(
            company_id=test_company.id,
            employee_id=test_employee.id,
            role_id=role.id,
            skill_level=2,
        )
        db.add(emp_role)
        await db.flush()

        assert emp_role.id is not None


class TestDeputyUnavailability:
    """Test Deputy unavailability/recurring blackout patterns."""

    @pytest.mark.asyncio
    async def test_employee_day_blackout_columns(self, db: AsyncSession) -> None:
        """EmployeeDayBlackout should have employee_id, day_of_week, start/end times."""
        inspector = inspect(EmployeeDayBlackout)
        columns = {col.name: col for col in inspector.columns}

        assert "employee_id" in columns
        assert "day_of_week" in columns
        assert "start_time" in columns
        assert "end_time" in columns

    @pytest.mark.asyncio
    async def test_create_employee_day_blackout(
        self, db: AsyncSession, test_employee: Employee
    ) -> None:
        """Create recurring blackout pattern from Deputy data."""
        blackout = EmployeeDayBlackout(
            company_id=test_employee.company_id,
            employee_id=test_employee.id,
            day_of_week=1,  # Tuesday (0=Monday)
            start_time="14:00",
            end_time="16:00",
        )
        db.add(blackout)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(EmployeeDayBlackout).where(
                EmployeeDayBlackout.employee_id == test_employee.id
            )
        )
        retrieved = result.scalar_one()

        assert retrieved.day_of_week == 1
        assert retrieved.start_time == "14:00"
        assert retrieved.end_time == "16:00"


class TestNullableFieldHandling:
    """Test that Deputy import handles NULL fields correctly."""

    @pytest.mark.asyncio
    async def test_employee_email_optional(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Deputy employees may not have email."""
        emp = Employee(
            company_id=test_company.id,
            full_name="No Email Employee",
            email=None,
            external_id="deputy_emp_no_email",
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.email is None

    @pytest.mark.asyncio
    async def test_location_address_optional(
        self,
        db: AsyncSession,
        test_company: Company,
        test_region: Region,
    ) -> None:
        """Deputy location may not have address."""
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Location Without Address",
            timezone="UTC",
            address=None,
            external_id="deputy_loc_no_addr",
        )
        db.add(loc)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Location).where(Location.id == loc.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.address is None

    @pytest.mark.asyncio
    async def test_employee_hire_date_optional(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Deputy employee may not have hire_date in their system."""
        emp = Employee(
            company_id=test_company.id,
            full_name="Unknown Hire Date",
            hire_date=None,
            external_id="deputy_emp_no_hire_date",
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.hire_date is None


class TestDeputyImportWorkflow:
    """Test complete Deputy import workflow."""

    @pytest.mark.asyncio
    async def test_deputy_import_complete_flow(
        self,
        db: AsyncSession,
        test_company: Company,
        test_region: Region,
    ) -> None:
        """End-to-end Deputy import: locations → roles → employees → assignments."""
        # Step 1: Create location
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Main Branch",
            timezone="Australia/Sydney",
            address="123 Collins St",
            external_id="deputy_comp_main",
        )
        db.add(loc)
        await db.flush()

        # Step 2: Create role
        role = Role(
            company_id=test_company.id,
            name="Manager",
            external_id="deputy_trade_manager",
        )
        db.add(role)
        await db.flush()

        # Step 3: Create employee
        emp = Employee(
            company_id=test_company.id,
            full_name="Bob Manager",
            email="bob@example.com",
            hire_date=date(2020, 1, 1),
            external_id="deputy_emp_bob",
        )
        db.add(emp)
        await db.flush()

        # Step 4: Assign role
        emp_role = EmployeeRole(
            company_id=test_company.id,
            employee_id=emp.id,
            role_id=role.id,
            skill_level=4,
        )
        db.add(emp_role)
        await db.flush()

        # Step 5: Add recurring blackout pattern
        blackout = EmployeeDayBlackout(
            company_id=emp.company_id,
            employee_id=emp.id,
            day_of_week=4,  # Friday
            start_time="18:00",
            end_time="22:00",
        )
        db.add(blackout)
        await db.flush()

        from sqlalchemy import select
        # Verify all created
        emp_result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        emp_check = emp_result.scalar_one()
        assert emp_check.hire_date == date(2020, 1, 1)

        role_result = await db.execute(
            select(EmployeeRole).where(EmployeeRole.employee_id == emp.id)
        )
        role_check = role_result.scalar_one()
        assert role_check.skill_level == 4

        blackout_result = await db.execute(
            select(EmployeeDayBlackout).where(
                EmployeeDayBlackout.employee_id == emp.id
            )
        )
        blackout_check = blackout_result.scalar_one()
        assert blackout_check.day_of_week == 4
        assert blackout_check.start_time == "18:00"
