import secrets
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import (
    Company,
    Department,
    Employee,
    EmployeeAffinity,
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
    User,
)

router = APIRouter(prefix="/import", tags=["import"])

SEVEN_SHIFTS_BASE = "https://api.7shifts.com/v2"
# Sentinel region name for locations imported without a region
IMPORTED_REGION_NAME = "7shifts Imported"


class ImportRequest(BaseModel):
    access_token: str


class ImportResult(BaseModel):
    companies: dict[str, int]
    locations: dict[str, int]
    departments: dict[str, int]
    roles: dict[str, int]
    employees: dict[str, int]
    user_assignments: dict[str, int]
    errors: list[str]


async def _fetch_all_pages(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all pages from a 7shifts paginated endpoint."""
    all_data: list[dict[str, Any]] = []
    params = dict(params or {})
    params.setdefault("limit", 200)

    while True:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"7shifts API error ({resp.status_code}): {resp.text[:500]}",
            )
        body = resp.json()
        data = body.get("data", [])
        all_data.extend(data)

        meta = body.get("meta", {})
        cursor = meta.get("cursor", {})
        next_cursor = cursor.get("next")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

    return all_data


async def _get_or_create_import_region(
    db: AsyncSession, company_id: uuid.UUID
) -> uuid.UUID:
    """Get or create a default region for imported locations."""
    result = await db.execute(
        select(Region).where(
            Region.company_id == company_id,
            Region.name == IMPORTED_REGION_NAME,
        )
    )
    region = result.scalar_one_or_none()
    if region:
        return region.id

    region = Region(
        company_id=company_id,
        name=IMPORTED_REGION_NAME,
    )
    db.add(region)
    await db.flush()
    return region.id


async def _get_or_create_ownership_group(
    db: AsyncSession, company: Company, group_name: str
) -> uuid.UUID:
    """Ensure the company belongs to an ownership group; create one if needed."""
    if company.ownership_group_id is not None:
        return company.ownership_group_id

    og = OwnershipGroup(name=group_name)
    db.add(og)
    await db.flush()
    company.ownership_group_id = og.id
    return og.id


async def _get_or_create_wiz_company(
    db: AsyncSession,
    ownership_group_id: uuid.UUID,
    ext_company_id: str,
    company_name: str,
) -> Company:
    """Find an existing WizScheduler company by external_id, or create a new one."""
    result = await db.execute(
        select(Company).where(
            Company.ownership_group_id == ownership_group_id,
            Company.external_id == ext_company_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.name = company_name
        return existing

    new_company = Company(
        name=company_name,
        slug=secrets.token_hex(3),
        ownership_group_id=ownership_group_id,
        external_id=ext_company_id,
    )
    db.add(new_company)
    await db.flush()
    return new_company


async def _import_single_company(
    client: httpx.AsyncClient,
    db: AsyncSession,
    headers: dict[str, str],
    wiz_company_id: uuid.UUID,
    ext_company_id: str,
    counts: dict[str, dict[str, int]],
    errors: list[str],
) -> None:
    """Import locations, departments, roles, employees, and assignments for one 7shifts company."""

    # ── Locations ──
    seven_locations = await _fetch_all_pages(
        client,
        f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/locations",
        headers,
    )

    import_region_id = await _get_or_create_import_region(db, wiz_company_id)

    loc_result = await db.execute(
        select(Location).where(
            Location.company_id == wiz_company_id,
            Location.external_id.isnot(None),
        )
    )
    existing_locs: dict[str, Location] = {
        loc.external_id: loc for loc in loc_result.scalars().all()  # type: ignore[union-attr]
    }

    seen_loc_ext_ids: set[str] = set()
    loc_ext_to_wiz: dict[str, uuid.UUID] = {}

    for sl in seven_locations:
        if sl.get("deleted"):
            continue
        ext_id = str(sl["id"])
        seen_loc_ext_ids.add(ext_id)

        address_parts = [
            sl.get("formatted_address") or "",
        ]
        address = ", ".join(p for p in address_parts if p) or None

        geo_coord = None
        if sl.get("lat") is not None and sl.get("lng") is not None:
            geo_coord = {"lat": sl["lat"], "lng": sl["lng"]}

        tz = sl.get("timezone", "UTC")

        if ext_id in existing_locs:
            loc = existing_locs[ext_id]
            loc.name = sl.get("name", loc.name)
            if address is not None:
                loc.address = address
            if geo_coord is not None:
                loc.geo_coord = geo_coord
            loc.timezone = tz
            loc_ext_to_wiz[ext_id] = loc.id
            counts["locations"]["updated"] += 1
        else:
            loc = Location(
                company_id=wiz_company_id,
                region_id=import_region_id,
                name=sl.get("name", "Unknown Location"),
                address=address,
                geo_coord=geo_coord,
                timezone=tz,
                external_id=ext_id,
            )
            db.add(loc)
            await db.flush()
            loc_ext_to_wiz[ext_id] = loc.id
            counts["locations"]["created"] += 1

    for ext_id, loc in existing_locs.items():
        if ext_id not in seen_loc_ext_ids:
            # Clean up all records referencing this location
            loc_schedules = await db.execute(
                select(ShiftSchedule.id).where(ShiftSchedule.location_id == loc.id)
            )
            schedule_ids = [sid for (sid,) in loc_schedules.all()]
            if schedule_ids:
                await db.execute(
                    delete(Shift).where(Shift.shift_schedule_id.in_(schedule_ids))
                )
                await db.execute(
                    delete(ShiftSchedule).where(ShiftSchedule.id.in_(schedule_ids))
                )
            await db.execute(
                delete(ShiftTemplate).where(ShiftTemplate.location_id == loc.id)
            )
            await db.execute(
                delete(Department).where(Department.location_id == loc.id)
            )
            await db.delete(loc)
            counts["locations"]["deleted"] += 1

    # ── Departments ──
    seven_departments = await _fetch_all_pages(
        client,
        f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/departments",
        headers,
    )

    dept_result = await db.execute(
        select(Department).where(
            Department.company_id == wiz_company_id,
            Department.external_id.isnot(None),
        )
    )
    existing_depts: dict[str, Department] = {
        d.external_id: d for d in dept_result.scalars().all()  # type: ignore[union-attr]
    }

    seen_dept_ext_ids: set[str] = set()

    for sd in seven_departments:
        if sd.get("deleted"):
            continue
        ext_id = str(sd["id"])
        seen_dept_ext_ids.add(ext_id)

        loc_ext_id = str(sd.get("location_id", ""))
        wiz_loc_id = loc_ext_to_wiz.get(loc_ext_id)
        if not wiz_loc_id:
            errors.append(
                f"Department '{sd.get('name')}' references unknown location {loc_ext_id}, skipped"
            )
            continue

        if ext_id in existing_depts:
            dept = existing_depts[ext_id]
            dept.name = sd.get("name", dept.name)
            dept.location_id = wiz_loc_id
            counts["departments"]["updated"] += 1
        else:
            dept = Department(
                company_id=wiz_company_id,
                location_id=wiz_loc_id,
                name=sd.get("name", "Unknown Department"),
                external_id=ext_id,
            )
            db.add(dept)
            counts["departments"]["created"] += 1

    for ext_id, dept in existing_depts.items():
        if ext_id not in seen_dept_ext_ids:
            await db.delete(dept)
            counts["departments"]["deleted"] += 1

    # ── Roles ──
    try:
        resp = await client.get(
            f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/roles",
            headers=headers,
        )
        if resp.status_code != 200:
            errors.append(f"Failed to fetch roles: {resp.text[:200]}")
            seven_roles: list[dict[str, Any]] = []
        else:
            seven_roles = resp.json().get("data", [])
    except httpx.RequestError as e:
        errors.append(f"Failed to fetch roles: {e}")
        seven_roles = []

    role_result = await db.execute(
        select(Role).where(
            Role.company_id == wiz_company_id,
            Role.external_id.isnot(None),
        )
    )
    existing_roles: dict[str, Role] = {
        r.external_id: r for r in role_result.scalars().all()  # type: ignore[union-attr]
    }

    seen_role_ext_ids: set[str] = set()
    role_ext_to_wiz: dict[str, uuid.UUID] = {}

    for sr in seven_roles:
        ext_id = str(sr["id"])
        seen_role_ext_ids.add(ext_id)

        if ext_id in existing_roles:
            role = existing_roles[ext_id]
            role.name = sr.get("name", role.name)
            role_ext_to_wiz[ext_id] = role.id
            counts["roles"]["updated"] += 1
        else:
            role = Role(
                company_id=wiz_company_id,
                name=sr.get("name", "Unknown Role"),
                external_id=ext_id,
            )
            db.add(role)
            await db.flush()
            role_ext_to_wiz[ext_id] = role.id
            counts["roles"]["created"] += 1

    for ext_id, role in existing_roles.items():
        if ext_id not in seen_role_ext_ids:
            await db.execute(
                delete(Shift).where(Shift.role_id == role.id)
            )
            await db.execute(
                delete(EmployeeRole).where(EmployeeRole.role_id == role.id)
            )
            await db.delete(role)
            counts["roles"]["deleted"] += 1

    # ── Employees ──
    seven_users = await _fetch_all_pages(
        client,
        f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/users",
        headers,
    )

    emp_result = await db.execute(
        select(Employee).where(
            Employee.company_id == wiz_company_id,
            Employee.external_id.isnot(None),
        )
    )
    existing_emps: dict[str, Employee] = {
        e.external_id: e for e in emp_result.scalars().all()  # type: ignore[union-attr]
    }

    seen_emp_ext_ids: set[str] = set()
    emp_ext_to_wiz: dict[str, uuid.UUID] = {}

    for su in seven_users:
        if not su.get("active", True):
            continue
        ext_id = str(su["id"])
        seen_emp_ext_ids.add(ext_id)

        first = su.get("first_name", "")
        last = su.get("last_name", "")
        full_name = f"{first} {last}".strip() or "Unknown"
        email = su.get("email") or None

        if ext_id in existing_emps:
            emp = existing_emps[ext_id]
            emp.full_name = full_name
            if email is not None:
                emp.email = email
            emp_ext_to_wiz[ext_id] = emp.id
            counts["employees"]["updated"] += 1
        else:
            emp = Employee(
                company_id=wiz_company_id,
                full_name=full_name,
                email=email,
                external_id=ext_id,
            )
            db.add(emp)
            await db.flush()
            emp_ext_to_wiz[ext_id] = emp.id
            # Default employee-company assignment
            ec = EmployeeCompany(employee_id=emp.id, company_id=wiz_company_id)
            db.add(ec)
            counts["employees"]["created"] += 1

    for ext_id, emp in existing_emps.items():
        if ext_id not in seen_emp_ext_ids:
            await db.execute(
                delete(Shift).where(Shift.employee_id == emp.id)
            )
            await db.execute(
                delete(EmployeeAffinity).where(
                    (EmployeeAffinity.employee_id == emp.id)
                    | (EmployeeAffinity.target_employee_id == emp.id)
                )
            )
            await db.execute(
                delete(EmployeeAvailability).where(EmployeeAvailability.employee_id == emp.id)
            )
            await db.execute(
                delete(EmployeeRole).where(EmployeeRole.employee_id == emp.id)
            )
            await db.execute(
                delete(EmployeeCompany).where(EmployeeCompany.employee_id == emp.id)
            )
            await db.delete(emp)
            counts["employees"]["deleted"] += 1

    # ── User assignments (locations + roles) ──
    SKILL_MAP = {0: 1, 1: 2, 2: 3, 3: 5}

    for su in seven_users:
        if not su.get("active", True):
            continue
        ext_user_id = str(su["id"])
        wiz_emp_id = emp_ext_to_wiz.get(ext_user_id)
        if not wiz_emp_id:
            continue

        try:
            resp = await client.get(
                f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/users/{ext_user_id}/assignments",
                headers=headers,
            )
            if resp.status_code != 200:
                errors.append(
                    f"Failed to fetch assignments for user {ext_user_id}: {resp.status_code}"
                )
                continue
            assignments_data = resp.json().get("data", {})
        except httpx.RequestError as e:
            errors.append(f"Failed to fetch assignments for user {ext_user_id}: {e}")
            continue

        # Location assignments → employee.location_ids
        loc_assignments = assignments_data.get("locations", [])
        assigned_loc_ids: list[uuid.UUID] = []
        for la in loc_assignments:
            loc_ext = str(la.get("id", ""))
            wiz_loc = loc_ext_to_wiz.get(loc_ext)
            if wiz_loc:
                assigned_loc_ids.append(wiz_loc)

        emp_row = await db.execute(
            select(Employee).where(Employee.id == wiz_emp_id)
        )
        employee = emp_row.scalar_one_or_none()
        if employee and assigned_loc_ids:
            employee.location_ids = assigned_loc_ids

        # Role assignments → employee_roles
        role_assignments = assignments_data.get("roles", [])

        await db.execute(
            delete(EmployeeRole).where(
                EmployeeRole.employee_id == wiz_emp_id
            )
        )

        best_skill: dict[str, int] = {}
        for ra in role_assignments:
            role_ext = str(ra.get("id", ""))
            skill = ra.get("skill_level", 2)
            if role_ext not in best_skill or skill > best_skill[role_ext]:
                best_skill[role_ext] = skill

        for role_ext, seven_skill in best_skill.items():
            wiz_role_id = role_ext_to_wiz.get(role_ext)
            if not wiz_role_id:
                continue
            er = EmployeeRole(
                company_id=wiz_company_id,
                employee_id=wiz_emp_id,
                role_id=wiz_role_id,
                skill_level=SKILL_MAP.get(seven_skill, 3),
            )
            db.add(er)
            counts["user_assignments"]["created"] += 1


@router.post("/7shifts", response_model=ImportResult)
async def import_from_7shifts(
    body: ImportRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "Accept": "application/json",
    }
    errors: list[str] = []
    counts: dict[str, dict[str, int]] = {
        "companies": {"created": 0, "updated": 0, "deleted": 0},
        "locations": {"created": 0, "updated": 0, "deleted": 0},
        "departments": {"created": 0, "updated": 0, "deleted": 0},
        "roles": {"created": 0, "updated": 0, "deleted": 0},
        "employees": {"created": 0, "updated": 0, "deleted": 0},
        "user_assignments": {"created": 0, "updated": 0, "deleted": 0},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ── 1. Fetch all 7shifts companies ──
        try:
            resp = await client.get(
                f"{SEVEN_SHIFTS_BASE}/companies", headers=headers
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to fetch 7shifts companies: {resp.text[:500]}",
                )
            seven_companies: list[dict[str, Any]] = resp.json().get("data", [])
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not reach 7shifts API: {e}",
            )

        if not seven_companies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No companies found in 7shifts account",
            )

        # ── 2. Ensure the current company has an ownership group ──
        current_company_result = await db.execute(
            select(Company).where(Company.id == current_user.company_id)
        )
        current_company = current_company_result.scalar_one()
        ownership_group_id = await _get_or_create_ownership_group(
            db, current_company, f"{current_company.name} Group"
        )

        # ── 3. Process each 7shifts company ──
        for idx, seven_company in enumerate(seven_companies):
            ext_company_id = str(seven_company["id"])
            company_name = seven_company.get("name", f"Company {ext_company_id}")

            if idx == 0:
                # First company maps to the current WizScheduler company
                wiz_company = current_company
                wiz_company.name = company_name
                wiz_company.external_id = ext_company_id
                counts["companies"]["updated"] += 1
            else:
                # Additional companies are created under the same ownership group
                wiz_company = await _get_or_create_wiz_company(
                    db, ownership_group_id, ext_company_id, company_name
                )
                if wiz_company.id == current_company.id:
                    counts["companies"]["updated"] += 1
                else:
                    counts["companies"]["created"] += 1

            # Import all data for this company
            await _import_single_company(
                client=client,
                db=db,
                headers=headers,
                wiz_company_id=wiz_company.id,
                ext_company_id=ext_company_id,
                counts=counts,
                errors=errors,
            )

    await db.commit()
    return ImportResult(
        companies=counts["companies"],
        locations=counts["locations"],
        departments=counts["departments"],
        roles=counts["roles"],
        employees=counts["employees"],
        user_assignments=counts["user_assignments"],
        errors=errors,
    )
