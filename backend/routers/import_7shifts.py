import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
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
    EmployeeInvite,
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

logger = logging.getLogger(__name__)

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
    db: AsyncSession, company_id: str
) -> str:
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
) -> str:
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
    ownership_group_id: str,
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
    wiz_company_id: str,
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
    loc_ext_to_wiz: dict[str, str] = {}

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
    role_ext_to_wiz: dict[str, str] = {}

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
    # Maps company-user id (from assignments endpoint) -> wiz employee ID
    emp_ext_to_wiz: dict[str, str] = {}

    for su in seven_users:
        if not su.get("active", True):
            continue
        company_user_id = str(su["id"])
        # Use 7shifts user_id as external_id (this is the stable ID used across
        # endpoints like availabilities). Fall back to company-user id if missing.
        ext_id = str(su["user_id"]) if su.get("user_id") else company_user_id
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
            emp_ext_to_wiz[company_user_id] = emp.id
            counts["employees"]["updated"] += 1
        elif company_user_id in existing_emps:
            # Migrate from old external_id (company-user id) to new (user_id)
            emp = existing_emps[company_user_id]
            emp.external_id = ext_id
            emp.full_name = full_name
            if email is not None:
                emp.email = email
            emp_ext_to_wiz[company_user_id] = emp.id
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
            emp_ext_to_wiz[company_user_id] = emp.id
            # Default employee-company assignment
            ec = EmployeeCompany(employee_id=emp.id, company_id=wiz_company_id)
            db.add(ec)
            counts["employees"]["created"] += 1

    # Build set of wiz employee IDs we've seen (updated or created) to avoid deleting them
    seen_wiz_emp_ids = set(emp_ext_to_wiz.values())
    for ext_id, emp in existing_emps.items():
        if emp.id in seen_wiz_emp_ids:
            continue
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
                delete(EmployeeInvite).where(EmployeeInvite.employee_id == emp.id)
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
        assigned_loc_ids: list[str] = []
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


# ── 7shifts Availability Import ──


class ImportAvailabilitiesRequest(BaseModel):
    access_token: str
    week_start: date
    week_end: date

    @model_validator(mode="after")
    def validate_date_range(self) -> "ImportAvailabilitiesRequest":
        today = date.today()
        # Monday of the current week
        current_week_monday = today - timedelta(days=today.weekday())
        max_end = current_week_monday + timedelta(weeks=9)  # 8 weeks ahead (end of 8th week)

        if self.week_start < current_week_monday:
            raise ValueError(
                f"week_start cannot be before the current week ({current_week_monday.isoformat()})"
            )
        if self.week_end > max_end:
            raise ValueError(
                f"week_end cannot be more than 8 weeks ahead ({max_end.isoformat()})"
            )
        if self.week_start >= self.week_end:
            raise ValueError("week_start must be before week_end")
        return self


class ImportAvailabilitiesResult(BaseModel):
    created: int
    skipped: int
    outside_range: int
    cleared: int
    errors: list[str]


# 7shifts availability uses a week-based format:
#   week: "YYYY-MM-DD" (Monday of the week)
#   mon/tue/wed/thu/fri/sat/sun: availability flag per day (0=unavailable, non-zero=available)
#   mon_from/mon_to, tue_from/tue_to, ...: time strings "HH:MM:SS" (partial availability)
#   repeat: whether the pattern repeats weekly
#   week_to: end date for repeating entries

_DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _expand_7shifts_availability(
    avail: dict[str, Any],
    range_start: date,
    range_end: date,
) -> list[tuple[date, str, str]]:
    """Expand a 7shifts week-based availability entry into per-day tuples.

    Returns a list of (date, start_time_str, end_time_str) for each available day
    within the requested range.
    """
    results: list[tuple[date, str, str]] = []

    week_str = avail.get("week")
    if not week_str:
        return results

    is_repeating = bool(avail.get("repeat"))

    try:
        week_monday = date.fromisoformat(str(week_str)[:10])
    except (ValueError, TypeError):
        if is_repeating:
            # "0000-00-00" or invalid date on recurring entries means
            # "always recurring" — start from the requested range
            week_monday = range_start - timedelta(days=range_start.weekday())
        else:
            return results

    # Determine the end of the repeating pattern
    week_to_str = avail.get("week_to")
    repeat_end: date | None = None
    if week_to_str:
        try:
            repeat_end = date.fromisoformat(str(week_to_str)[:10])
        except (ValueError, TypeError):
            pass

    # Iterate over each week the pattern applies to
    current_week = week_monday
    while True:
        # Check if this week overlaps with the requested range
        week_end_date = current_week + timedelta(days=6)
        if current_week > range_end:
            break
        if week_end_date < range_start:
            if is_repeating:
                current_week += timedelta(days=7)
                if repeat_end and current_week > repeat_end:
                    break
                continue
            else:
                break

        # Process each day of the week
        for day_offset, day_key in enumerate(_DAY_KEYS):
            day_date = current_week + timedelta(days=day_offset)

            # Skip days outside requested range
            if day_date < range_start or day_date > range_end:
                continue

            # Check if this day is marked as available
            day_flag = avail.get(day_key)
            if not day_flag:
                continue

            # Get time range for this day
            from_time = avail.get(f"{day_key}_from") or ""
            to_time = avail.get(f"{day_key}_to") or ""

            # Normalize: empty, null, or both-midnight means all-day
            if not from_time or from_time == "null":
                from_time = "00:00:00"
            if not to_time or to_time == "null":
                to_time = "23:59:00"
            # Both midnight (00:00:00) means all-day availability in 7shifts
            if from_time == to_time:
                from_time = "00:00:00"
                to_time = "23:59:00"
            # Midnight end time (00:00:00) means "until end of day", not start of day
            if to_time == "00:00:00" and from_time != "00:00:00":
                to_time = "23:59:00"

            results.append((day_date, str(from_time), str(to_time)))

        if not is_repeating:
            break
        current_week += timedelta(days=7)
        if repeat_end and current_week > repeat_end:
            break

    return results


@router.post("/7shifts/availabilities", response_model=ImportAvailabilitiesResult)
async def import_availabilities_from_7shifts(
    body: ImportAvailabilitiesRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ImportAvailabilitiesResult:
    """Import employee availabilities from 7shifts for the specified date range."""
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "Accept": "application/json",
    }
    errors: list[str] = []
    created = 0
    skipped = 0
    outside_range = 0
    cleared = 0

    # Find all companies in the ownership group with a 7shifts external_id
    og_result = await db.execute(
        select(Company.ownership_group_id).where(Company.id == current_user.company_id)
    )
    og_id = og_result.scalar_one_or_none()

    if og_id:
        comp_result = await db.execute(
            select(Company).where(
                Company.ownership_group_id == og_id,
                Company.external_id.isnot(None),
            )
        )
        all_companies = list(comp_result.scalars().all())
    else:
        # No ownership group — just check the current company
        comp_result = await db.execute(
            select(Company).where(
                Company.id == current_user.company_id,
                Company.external_id.isnot(None),
            )
        )
        single = comp_result.scalar_one_or_none()
        all_companies = [single] if single else []

    # Deduplicate by external_id — only process each 7shifts company once
    # Prefer companies in the current user's ownership group
    ext_id_to_company: dict[str, Company] = {}
    for c in all_companies:
        ext = c.external_id
        if ext not in ext_id_to_company:
            ext_id_to_company[ext] = c  # type: ignore[index]
        elif c.ownership_group_id == og_id:
            ext_id_to_company[ext] = c  # type: ignore[index]
    companies_with_ext: list[Company] = list(ext_id_to_company.values())

    if not companies_with_ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No 7shifts-linked companies found. Import company data from 7shifts first.",
        )

    range_start = body.week_start
    range_end = body.week_end

    async with httpx.AsyncClient(timeout=30.0) as client:
        for wiz_company in companies_with_ext:
            ext_company_id = wiz_company.external_id

            # Build employee external_id -> wiz employee mapping
            emp_result = await db.execute(
                select(Employee).where(
                    Employee.company_id == wiz_company.id,
                    Employee.external_id.isnot(None),
                )
            )
            ext_to_employee: dict[str, Employee] = {
                e.external_id: e for e in emp_result.scalars().all()  # type: ignore[union-attr]
            }

            if not ext_to_employee:
                errors.append(
                    f"Company '{wiz_company.name}' has no 7shifts-linked employees, skipped"
                )
                continue

            # Fetch 7shifts users to build user_id → company-user id mapping
            # Needed because employee external_id may be either the user_id or
            # the company-user id depending on when the import was last run
            user_id_to_ext: dict[str, str] = {}
            try:
                seven_users = await _fetch_all_pages(
                    client,
                    f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/users",
                    headers,
                )
                for su in seven_users:
                    cu_id = str(su.get("id", ""))
                    uid = str(su.get("user_id", ""))
                    if cu_id and uid:
                        user_id_to_ext[uid] = cu_id
                        # Also migrate employee external_id to user_id
                        emp = ext_to_employee.get(cu_id)
                        if emp and cu_id != uid:
                            emp.external_id = uid
                            # Add to lookup under new key too
                            ext_to_employee[uid] = emp
            except HTTPException:
                pass  # Fall back to direct matching

            # Fetch availabilities from 7shifts (both recurring and one-off)
            try:
                recurring = await _fetch_all_pages(
                    client,
                    f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/availabilities",
                    headers,
                    params={"repeating": "true"},
                )
                one_off = await _fetch_all_pages(
                    client,
                    f"{SEVEN_SHIFTS_BASE}/company/{ext_company_id}/availabilities",
                    headers,
                    params={"repeating": "false"},
                )
                seven_availabilities = recurring + one_off
            except HTTPException as e:
                errors.append(
                    f"Failed to fetch availabilities for company '{wiz_company.name}': {e.detail}"
                )
                continue

            # Clear existing availability in the date range for this company
            range_start_dt = datetime(
                range_start.year, range_start.month, range_start.day, tzinfo=timezone.utc
            )
            range_end_dt = datetime(
                range_end.year, range_end.month, range_end.day, 23, 59, 59, tzinfo=timezone.utc
            )
            del_result = await db.execute(
                delete(EmployeeAvailability).where(
                    EmployeeAvailability.company_id == wiz_company.id,
                    EmployeeAvailability.start_time >= range_start_dt,
                    EmployeeAvailability.start_time <= range_end_dt,
                )
            )
            cleared += del_result.rowcount  # type: ignore[assignment]

            if not seven_availabilities:
                errors.append(
                    f"Company '{wiz_company.name}': 7shifts returned 0 availability entries"
                )
                continue

            # Track skip reasons for diagnostics
            skip_no_employee = 0
            skip_type = 0
            skip_status = 0
            skip_no_days = 0
            unmatched_user_ids: set[str] = set()

            # Process each availability entry
            for avail in seven_availabilities:
                raw_user_id = str(avail.get("user_id", ""))
                # Try direct match (user_id), then fallback via mapping (company-user id)
                employee = ext_to_employee.get(raw_user_id)
                if not employee:
                    cu_id = user_id_to_ext.get(raw_user_id)
                    if cu_id:
                        employee = ext_to_employee.get(cu_id)
                if not employee:
                    skipped += 1
                    skip_no_employee += 1
                    unmatched_user_ids.add(raw_user_id)
                    continue

                # Only import "available" type entries
                avail_type = avail.get("type", "")
                # Accept common type values that indicate availability
                if isinstance(avail_type, str) and avail_type.lower() in (
                    "unavailable", "unavailability",
                ):
                    skipped += 1
                    skip_type += 1
                    continue

                # Check status — only import approved availabilities
                avail_status = avail.get("status")
                if isinstance(avail_status, dict):
                    status_name = avail_status.get("name", "")
                    if status_name.lower() in ("declined", "rejected"):
                        skipped += 1
                        skip_status += 1
                        continue
                elif isinstance(avail_status, int) and avail_status == 2:
                    # 2 = declined in some 7shifts versions
                    skipped += 1
                    skip_status += 1
                    continue

                days = _expand_7shifts_availability(avail, range_start, range_end)
                if not days:
                    outside_range += 1
                    skip_no_days += 1
                    continue
                for day_date, start_t, end_t in days:
                    try:
                        # Parse time strings (HH:MM:SS or HH:MM)
                        parts_s = start_t.split(":")
                        parts_e = end_t.split(":")
                        start_dt = datetime(
                            day_date.year, day_date.month, day_date.day,
                            int(parts_s[0]), int(parts_s[1]),
                            int(parts_s[2]) if len(parts_s) > 2 else 0,
                            tzinfo=timezone.utc,
                        )
                        end_dt = datetime(
                            day_date.year, day_date.month, day_date.day,
                            int(parts_e[0]), int(parts_e[1]),
                            int(parts_e[2]) if len(parts_e) > 2 else 0,
                            tzinfo=timezone.utc,
                        )
                    except (ValueError, IndexError):
                        errors.append(
                            f"Invalid time format for user {raw_user_id} on {day_date}: "
                            f"{start_t} - {end_t}"
                        )
                        continue

                    record = EmployeeAvailability(
                        company_id=wiz_company.id,
                        employee_id=employee.id,
                        year=day_date.year,
                        month=day_date.month,
                        day=day_date.day,
                        start_time=start_dt,
                        end_time=end_dt,
                    )
                    db.add(record)
                    created += 1

            # Add skip breakdown to errors for diagnostics
            skip_parts = []
            if skip_no_employee:
                skip_parts.append(f"{skip_no_employee} unmatched employees")
            if skip_type:
                skip_parts.append(f"{skip_type} unavailable type")
            if skip_status:
                skip_parts.append(f"{skip_status} declined status")
            if skip_no_days:
                skip_parts.append(f"{skip_no_days} outside date range")
            if skip_parts:
                errors.append(
                    f"Company '{wiz_company.name}' skip breakdown: {', '.join(skip_parts)}. "
                    f"{len(ext_to_employee)} employees linked."
                )
            if unmatched_user_ids and skip_no_employee:
                sample = sorted(unmatched_user_ids)[:5]
                errors.append(
                    f"Sample unmatched user_ids: {sample}. "
                    f"Known ext_ids: {sorted(ext_to_employee.keys())[:5]}"
                )

    await db.commit()
    return ImportAvailabilitiesResult(
        created=created,
        skipped=skipped,
        outside_range=outside_range,
        cleared=cleared,
        errors=errors,
    )
