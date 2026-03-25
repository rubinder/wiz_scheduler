import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, get_ownership_group_company_ids, require_manager
from backend.models import Company, Employee, EmployeeAvailability, EmployeeCompany, EmployeeRole, Location, Role, Shift, User
from backend.schemas.employee import (
    AvailabilityCreate,
    AvailabilityResponse,
    BulkUploadResponse,
    EmployeeCreate,
    EmployeeMeLocationResponse,
    EmployeeMeResponse,
    EmployeeMeRoleResponse,
    EmployeeResponse,
    EmployeeUpdate,
)

router = APIRouter(prefix="/employees", tags=["employees"])


async def _sync_employee_roles(
    db: AsyncSession,
    employee_id: uuid.UUID,
    company_id: uuid.UUID,
    roles: list[dict],
) -> None:
    """Delete existing employee roles and recreate from the provided list."""
    await db.execute(
        delete(EmployeeRole).where(EmployeeRole.employee_id == employee_id)
    )
    for role_entry in roles:
        er = EmployeeRole(
            company_id=company_id,
            employee_id=employee_id,
            role_id=role_entry.role_id,
            skill_level=role_entry.skill_level,
        )
        db.add(er)


async def _sync_employee_companies(
    db: AsyncSession,
    employee_id: uuid.UUID,
    company_ids: list[uuid.UUID],
    allowed_company_ids: list[uuid.UUID],
) -> None:
    """Sync the employee_companies junction table.

    Only allows assignment to companies within the caller's ownership group.
    """
    allowed_set = set(allowed_company_ids)
    for cid in company_ids:
        if cid not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Company {cid} is not in your ownership group",
            )

    # Delete existing assignments
    await db.execute(
        delete(EmployeeCompany).where(EmployeeCompany.employee_id == employee_id)
    )
    # Recreate
    for cid in company_ids:
        ec = EmployeeCompany(employee_id=employee_id, company_id=cid)
        db.add(ec)


async def _get_employee_company_ids(
    db: AsyncSession, employee_id: uuid.UUID
) -> list[uuid.UUID]:
    """Get all company IDs an employee is assigned to."""
    result = await db.execute(
        select(EmployeeCompany.company_id).where(
            EmployeeCompany.employee_id == employee_id
        )
    )
    return list(result.scalars().all())


async def _to_response(db: AsyncSession, employee: Employee) -> EmployeeResponse:
    """Convert an Employee ORM object to an EmployeeResponse with company_ids."""
    company_ids = await _get_employee_company_ids(db, employee.id)
    resp = EmployeeResponse.model_validate(employee)
    resp.company_ids = company_ids
    return resp


@router.get("/me", response_model=EmployeeMeResponse)
async def get_my_employee_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmployeeMeResponse:
    """Return the employee profile for the currently authenticated user, including role names."""
    result = await db.execute(
        select(Employee).where(
            Employee.user_id == current_user.id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No employee profile found")

    # Load roles with names
    er_result = await db.execute(
        select(EmployeeRole).where(EmployeeRole.employee_id == employee.id)
    )
    emp_roles = er_result.scalars().all()

    role_ids = [er.role_id for er in emp_roles]
    role_names: dict[uuid.UUID, str] = {}
    if role_ids:
        role_result = await db.execute(
            select(Role).where(Role.id.in_(role_ids))
        )
        role_names = {r.id: r.name for r in role_result.scalars().all()}

    roles = [
        EmployeeMeRoleResponse(role_id=er.role_id, role_name=role_names.get(er.role_id, ""))
        for er in emp_roles
    ]

    # Load location names
    locations: list[EmployeeMeLocationResponse] = []
    if employee.location_ids:
        loc_result = await db.execute(
            select(Location).where(Location.id.in_(employee.location_ids))
        )
        locations = [
            EmployeeMeLocationResponse(location_id=loc.id, location_name=loc.name)
            for loc in loc_result.scalars().all()
        ]

    return EmployeeMeResponse(
        id=employee.id,
        full_name=employee.full_name,
        email=employee.email,
        roles=roles,
        locations=locations,
    )


@router.get("/", response_model=list[EmployeeResponse])
async def list_employees(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeResponse]:
    result = await db.execute(
        select(Employee).where(Employee.company_id == current_user.company_id)
    )
    employees = result.scalars().all()
    return [await _to_response(db, e) for e in employees]


@router.post("/", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    body: EmployeeCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    group_company_ids: list[uuid.UUID] = Depends(get_ownership_group_company_ids),
) -> EmployeeResponse:
    employee = Employee(
        company_id=current_user.company_id,
        full_name=body.full_name,
        email=body.email,
        user_id=body.user_id,
        location_ids=body.location_ids,
    )
    db.add(employee)
    await db.flush()

    if body.roles:
        await _sync_employee_roles(db, employee.id, current_user.company_id, body.roles)

    # Set up company assignments — always include the creating company
    company_ids_to_assign = [current_user.company_id]
    if body.company_ids:
        # Merge with explicitly requested companies
        extra = [cid for cid in body.company_ids if cid != current_user.company_id]
        company_ids_to_assign.extend(extra)
    await _sync_employee_companies(db, employee.id, company_ids_to_assign, group_company_ids)

    await db.commit()
    await db.refresh(employee)
    return await _to_response(db, employee)


@router.put("/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: uuid.UUID,
    body: EmployeeUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    group_company_ids: list[uuid.UUID] = Depends(get_ownership_group_company_ids),
) -> EmployeeResponse:
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if body.full_name is not None:
        employee.full_name = body.full_name
    if body.email is not None:
        employee.email = body.email
    if body.user_id is not None:
        employee.user_id = body.user_id
    if body.location_ids is not None:
        employee.location_ids = body.location_ids

    if body.roles is not None:
        await _sync_employee_roles(db, employee.id, current_user.company_id, body.roles)

    if body.company_ids is not None:
        await _sync_employee_companies(db, employee.id, body.company_ids, group_company_ids)

    await db.commit()
    await db.refresh(employee)
    return await _to_response(db, employee)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    # Prevent deletion of demo employees in the demo company
    company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = company_result.scalar_one_or_none()
    if company and company.slug == "acme-corp" and employee.user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete demo employees in the demo company",
        )

    # Clean up all related records before deleting the employee
    await db.execute(
        delete(Shift).where(Shift.employee_id == employee_id)
    )
    await db.execute(
        delete(EmployeeAvailability).where(EmployeeAvailability.employee_id == employee_id)
    )
    await db.execute(
        delete(EmployeeRole).where(EmployeeRole.employee_id == employee_id)
    )
    await db.execute(
        delete(EmployeeCompany).where(EmployeeCompany.employee_id == employee_id)
    )
    await db.delete(employee)
    await db.commit()


@router.post("/bulk-upload", response_model=BulkUploadResponse)
async def bulk_upload(
    file: UploadFile,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    group_company_ids: list[uuid.UUID] = Depends(get_ownership_group_company_ids),
) -> BulkUploadResponse:
    """
    Accepts a multipart CSV with columns:
      full_name, email, role_names (pipe-separated), skill_levels (pipe-separated),
      location_names (pipe-separated).

    Role names are matched case-insensitively against the roles table.
    Location names are matched case-insensitively against the locations table.
    Returns a summary of created, skipped, and errors.
    """
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    # Pre-fetch company roles and locations for case-insensitive matching
    role_result = await db.execute(
        select(Role).where(Role.company_id == current_user.company_id)
    )
    roles_by_name: dict[str, Role] = {
        r.name.lower(): r for r in role_result.scalars().all()
    }

    location_result = await db.execute(
        select(Location).where(Location.company_id == current_user.company_id)
    )
    locations_by_name: dict[str, Location] = {
        loc.name.lower(): loc for loc in location_result.scalars().all()
    }

    created = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(reader, start=2):  # start=2 because row 1 is header
        full_name = (row.get("full_name") or "").strip()
        email = (row.get("email") or "").strip() or None
        raw_roles = (row.get("role_names") or "").strip()
        raw_skills = (row.get("skill_levels") or "").strip()
        raw_locations = (row.get("location_names") or "").strip()

        if not full_name:
            errors.append(f"Row {row_num}: missing full_name, skipped")
            skipped += 1
            continue

        # Check for duplicate email within company
        if email:
            dup_result = await db.execute(
                select(Employee).where(
                    Employee.company_id == current_user.company_id,
                    Employee.email == email,
                )
            )
            if dup_result.scalar_one_or_none() is not None:
                errors.append(f"Row {row_num}: employee with email {email} already exists, skipped")
                skipped += 1
                continue

        # Parse roles
        role_names = [rn.strip() for rn in raw_roles.split("|") if rn.strip()] if raw_roles else []
        skill_levels_raw = [s.strip() for s in raw_skills.split("|") if s.strip()] if raw_skills else []

        resolved_roles: list[tuple[uuid.UUID, int]] = []
        row_has_error = False
        for idx, rn in enumerate(role_names):
            role_obj = roles_by_name.get(rn.lower())
            if role_obj is None:
                errors.append(f"Row {row_num}: role '{rn}' not found, skipped")
                row_has_error = True
                break
            skill = 1  # default
            if idx < len(skill_levels_raw):
                try:
                    skill = int(skill_levels_raw[idx])
                except ValueError:
                    errors.append(f"Row {row_num}: invalid skill_level '{skill_levels_raw[idx]}', skipped")
                    row_has_error = True
                    break
            resolved_roles.append((role_obj.id, skill))

        if row_has_error:
            skipped += 1
            continue

        # Parse locations
        location_names = [ln.strip() for ln in raw_locations.split("|") if ln.strip()] if raw_locations else []
        resolved_location_ids: list[uuid.UUID] = []
        for ln in location_names:
            loc_obj = locations_by_name.get(ln.lower())
            if loc_obj is None:
                errors.append(f"Row {row_num}: location '{ln}' not found, skipped")
                row_has_error = True
                break
            resolved_location_ids.append(loc_obj.id)

        if row_has_error:
            skipped += 1
            continue

        # Create employee
        employee = Employee(
            company_id=current_user.company_id,
            full_name=full_name,
            email=email,
            location_ids=resolved_location_ids if resolved_location_ids else None,
        )
        db.add(employee)
        await db.flush()

        # Create employee roles
        for role_id, skill_level in resolved_roles:
            er = EmployeeRole(
                company_id=current_user.company_id,
                employee_id=employee.id,
                role_id=role_id,
                skill_level=skill_level,
            )
            db.add(er)

        # Default company assignment
        ec = EmployeeCompany(
            employee_id=employee.id,
            company_id=current_user.company_id,
        )
        db.add(ec)

        created += 1

    await db.commit()
    return BulkUploadResponse(created=created, skipped=skipped, errors=errors)


# ── Availability endpoints ──


@router.get("/availability/all", response_model=list[AvailabilityResponse])
async def list_all_availability(
    week_start: date | None = Query(None, description="Filter to a specific week (Monday). Returns 7 days from this date."),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[AvailabilityResponse]:
    """List all availability windows for the company (manager only).

    If week_start is provided, only returns windows within that 7-day period.
    """
    query = select(EmployeeAvailability).where(
        EmployeeAvailability.company_id == current_user.company_id,
    )
    if week_start is not None:
        week_start_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc)
        week_end_dt = week_start_dt + timedelta(days=7)
        query = query.where(
            EmployeeAvailability.start_time >= week_start_dt,
            EmployeeAvailability.start_time < week_end_dt,
        )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{employee_id}/availability", response_model=list[AvailabilityResponse])
async def list_availability(
    employee_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AvailabilityResponse]:
    """List availability windows for an employee.

    Managers can view any employee in their company.
    Employees can only view their own.
    """
    # Verify the employee belongs to the user's company
    emp_result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = emp_result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    # Non-managers can only view their own
    if current_user.user_role != "manager" and employee.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    result = await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.employee_id == employee_id,
            EmployeeAvailability.company_id == current_user.company_id,
        )
    )
    return list(result.scalars().all())


@router.post("/availability", response_model=AvailabilityResponse, status_code=status.HTTP_201_CREATED)
async def create_availability(
    body: AvailabilityCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AvailabilityResponse:
    """Create an availability window.

    Managers can create for any employee; employees only for themselves.
    """
    emp_result = await db.execute(
        select(Employee).where(
            Employee.id == body.employee_id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = emp_result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if current_user.user_role != "manager" and employee.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    avail = EmployeeAvailability(
        company_id=current_user.company_id,
        employee_id=body.employee_id,
        year=body.year,
        month=body.month,
        day=body.day,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    db.add(avail)
    await db.commit()
    await db.refresh(avail)
    return avail


@router.delete("/availability/{availability_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_availability(
    availability_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an availability window."""
    result = await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.id == availability_id,
            EmployeeAvailability.company_id == current_user.company_id,
        )
    )
    avail = result.scalar_one_or_none()
    if avail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Availability not found")

    # Non-managers can only delete their own
    if current_user.user_role != "manager":
        emp_result = await db.execute(
            select(Employee).where(Employee.id == avail.employee_id)
        )
        emp = emp_result.scalar_one_or_none()
        if emp is None or emp.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    await db.delete(avail)
    await db.commit()
