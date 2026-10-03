"""Test schema compatibility for 7shifts imports.

Verifies that the database schema supports all operations required by the
7shifts import process, including nullable columns, type conversions, and
the complete import workflow.
"""

import pytest
from datetime import date
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Department,
    Employee,
    EmployeeRole,
    Location,
    Region,
    Role,
)


class TestSchemaColumnsExist:
    """Verify all import-required columns exist with correct types."""

    @pytest.mark.asyncio
    async def test_employee_nullable_columns(self, db: AsyncSession) -> None:
        """Employee should have nullable external_id, email, location_ids for imports."""
        inspector = inspect(Employee)
        columns = {col.name: col for col in inspector.columns}

        # Required for imports
        assert "external_id" in columns
        assert columns["external_id"].nullable

        assert "email" in columns
        assert columns["email"].nullable

        assert "location_ids" in columns
        assert columns["location_ids"].nullable

        # Cost/seniority fields from #134
        assert "pay_rate" in columns
        assert columns["pay_rate"].nullable

        assert "hire_date" in columns
        assert columns["hire_date"].nullable

        assert "seniority_rank" in columns
        assert columns["seniority_rank"].nullable

    @pytest.mark.asyncio
    async def test_location_nullable_columns(self, db: AsyncSession) -> None:
        """Location should have nullable columns for optional import data."""
        inspector = inspect(Location)
        columns = {col.name: col for col in inspector.columns}

        # Optional import fields
        assert "address" in columns
        assert columns["address"].nullable

        assert "geo_coord" in columns
        assert columns["geo_coord"].nullable

        assert "external_id" in columns
        assert columns["external_id"].nullable

        # Overtime fields
        assert "overtime_threshold_hours" in columns
        assert columns["overtime_threshold_hours"].nullable

        assert "overtime_premium_multiplier" in columns
        assert columns["overtime_premium_multiplier"].nullable

    @pytest.mark.asyncio
    async def test_employee_role_skill_level_exists(self, db: AsyncSession) -> None:
        """EmployeeRole.skill_level column exists for role assignments."""
        inspector = inspect(EmployeeRole)
        columns = {col.name: col for col in inspector.columns}

        assert "skill_level" in columns


class TestNullableColumnHandling:
    """Test that nullable columns accept and preserve NULL values."""

    @pytest.mark.asyncio
    async def test_employee_nullable_values(
        self, db: AsyncSession, test_company: Company, test_region: Region
    ) -> None:
        """Create employee with all nullable fields as NULL."""
        emp = Employee(
            company_id=test_company.id,
            full_name="John Doe",
            email=None,  # nullable
            external_id=None,  # nullable
            location_ids=None,  # nullable JSON
            pay_rate=None,
            hire_date=None,
            seniority_rank=None,
        )
        db.add(emp)
        await db.flush()

        # Retrieve and verify NULLs are preserved
        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.email is None
        assert retrieved.external_id is None
        assert retrieved.location_ids is None
        assert retrieved.pay_rate is None
        assert retrieved.hire_date is None
        assert retrieved.seniority_rank is None

    @pytest.mark.asyncio
    async def test_location_nullable_values(
        self, db: AsyncSession, test_company: Company, test_region: Region
    ) -> None:
        """Create location with optional fields as NULL."""
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Test Location",
            timezone="UTC",
            address=None,
            geo_coord=None,
            external_id=None,
            overtime_threshold_hours=None,
            overtime_premium_multiplier=None,
        )
        db.add(loc)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Location).where(Location.id == loc.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.address is None
        assert retrieved.geo_coord is None
        assert retrieved.external_id is None
        assert retrieved.overtime_threshold_hours is None
        assert retrieved.overtime_premium_multiplier is None


class TestTypeConversions:
    """Test conversions of import data types to database types."""

    @pytest.mark.asyncio
    async def test_employee_location_ids_json(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """location_ids should store and retrieve as JSON list."""
        location_ids = ["loc_abc", "loc_def", "loc_ghi"]
        emp = Employee(
            company_id=test_company.id,
            full_name="Test",
            location_ids=location_ids,
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.location_ids == location_ids

    @pytest.mark.asyncio
    async def test_location_geo_coord_json(
        self, db: AsyncSession, test_company: Company, test_region: Region
    ) -> None:
        """geo_coord should store and retrieve as JSON object."""
        geo = {"lat": 40.7128, "lng": -74.0060}
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="NYC HQ",
            timezone="America/New_York",
            geo_coord=geo,
        )
        db.add(loc)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Location).where(Location.id == loc.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.geo_coord == geo
        assert retrieved.geo_coord["lat"] == 40.7128

    @pytest.mark.asyncio
    async def test_employee_date_conversion(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """hire_date should convert strings to date and store properly."""
        hire_date_obj = date(2020, 6, 15)
        emp = Employee(
            company_id=test_company.id,
            full_name="Jane Smith",
            hire_date=hire_date_obj,
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.hire_date == hire_date_obj
        assert isinstance(retrieved.hire_date, date)

    @pytest.mark.asyncio
    async def test_employee_numeric_conversions(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """pay_rate (Numeric) and seniority_rank (SmallInteger) conversions."""
        pay_rate = 25.50
        seniority = 3

        emp = Employee(
            company_id=test_company.id,
            full_name="High Earner",
            pay_rate=pay_rate,
            seniority_rank=seniority,
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        # pay_rate comes back as Decimal, but should convert to float
        assert float(retrieved.pay_rate) == pay_rate
        assert retrieved.seniority_rank == seniority


class TestImportDataFlow:
    """Test realistic 7shifts import scenarios."""

    @pytest.mark.asyncio
    async def test_import_employee_minimal(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Create employee with only required fields (like 7shifts import does)."""
        # This simulates: Employee(company_id=..., full_name=..., email=..., external_id=...)
        emp = Employee(
            company_id=test_company.id,
            full_name="John Doe",
            email="john@example.com",
            external_id="7shifts_user_123",
        )
        db.add(emp)
        await db.flush()

        assert emp.id is not None
        assert emp.location_ids is None
        assert emp.pay_rate is None

    @pytest.mark.asyncio
    async def test_import_location_minimal(
        self, db: AsyncSession, test_company: Company, test_region: Region
    ) -> None:
        """Create location with minimal fields from 7shifts."""
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Main Store",
            timezone="America/Chicago",
            address="123 Main St",
            external_id="7shifts_loc_456",
        )
        db.add(loc)
        await db.flush()

        assert loc.id is not None
        assert loc.geo_coord is None

    @pytest.mark.asyncio
    async def test_import_employee_role_assignment(
        self,
        db: AsyncSession,
        test_company: Company,
        test_employee: Employee,
    ) -> None:
        """Create EmployeeRole with skill_level (7shifts import skill mapping)."""
        role = Role(
            company_id=test_company.id,
            name="Cashier",
        )
        db.add(role)
        await db.flush()

        # SKILL_MAP in import_7shifts: {0: 1, 1: 2, 2: 3, 3: 5}
        emp_role = EmployeeRole(
            company_id=test_company.id,
            employee_id=test_employee.id,
            role_id=role.id,
            skill_level=3,  # 7shifts skill 2 maps to WizScheduler skill 3
        )
        db.add(emp_role)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(EmployeeRole).where(EmployeeRole.id == emp_role.id)
        )
        retrieved = result.scalar_one()

        assert retrieved.skill_level == 3

    @pytest.mark.asyncio
    async def test_import_department_hierarchy(
        self,
        db: AsyncSession,
        test_company: Company,
        test_location: Location,
    ) -> None:
        """Create Department under Location (7shifts import structure)."""
        dept = Department(
            company_id=test_company.id,
            location_id=test_location.id,
            name="Kitchen",
            external_id="7shifts_dept_789",
        )
        db.add(dept)
        await db.flush()

        assert dept.id is not None
        assert dept.location_id == test_location.id

    @pytest.mark.asyncio
    async def test_import_with_cost_seniority_data(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """Employee with pay_rate/hire_date/seniority_rank from #134."""
        emp = Employee(
            company_id=test_company.id,
            full_name="Senior Manager",
            pay_rate=85.00,
            hire_date=date(2018, 1, 15),
            seniority_rank=1,
            external_id="7shifts_mgr_001",
        )
        db.add(emp)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(Employee).where(Employee.id == emp.id)
        )
        retrieved = result.scalar_one()

        assert float(retrieved.pay_rate) == 85.00
        assert retrieved.hire_date == date(2018, 1, 15)
        assert retrieved.seniority_rank == 1


class TestSchemaConstraints:
    """Test schema-level constraints that imports must respect."""

    @pytest.mark.asyncio
    async def test_seniority_rank_positive(
        self, db: AsyncSession, test_company: Company
    ) -> None:
        """seniority_rank must be positive (> 0) per schema constraint."""
        emp = Employee(
            company_id=test_company.id,
            full_name="Employee",
            seniority_rank=0,  # Should violate CHECK constraint
        )
        db.add(emp)

        with pytest.raises(Exception):  # IntegrityError
            await db.flush()

    @pytest.mark.asyncio
    async def test_overtime_multiplier_minimum(
        self,
        db: AsyncSession,
        test_company: Company,
        test_region: Region,
    ) -> None:
        """overtime_premium_multiplier must be >= 1 or NULL."""
        loc = Location(
            company_id=test_company.id,
            region_id=test_region.id,
            name="Test",
            timezone="UTC",
            overtime_premium_multiplier=0.5,  # Should violate CHECK constraint
        )
        db.add(loc)

        with pytest.raises(Exception):  # IntegrityError
            await db.flush()
