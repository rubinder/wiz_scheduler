import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import (
    Company,
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeCompany,
    EmployeeDayBlackout,
    EmployeeInvite,
    EmployeeRole,
    EmployeeRoleMinutes,
    Location,
    OwnershipGroup,
    Region,
    Role,
    Shift,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import"])

IMPORTED_REGION_NAME = "Deputy Imported"

# Day-of-week names matching Python datetime.weekday(): 0=Mon...6=Sun
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Deputy uses MO, TU, WE, TH, FR, SA, SU in recurrence BYDAY
DEPUTY_BYDAY_MAP = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3,
    "FR": 4, "SA": 5, "SU": 6,
}


class DeputyImportRequest(BaseModel):
    access_token: str
    deputy_base_url: str  # e.g. "https://7b309013021026.na.deputy.com"


class DeputyImportResult(BaseModel):
    locations: dict[str, int]
    roles: dict[str, int]
    employees: dict[str, int]
    unavailability: dict[str, int]
    errors: list[str]


async def _deputy_get(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    headers: dict[str, str],
) -> list[dict[str, Any]] | dict[str, Any]:
    """GET a Deputy API endpoint. Returns parsed JSON."""
    url = f"{base_url.rstrip('/')}/api{path}"
    resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Deputy API error ({resp.status_code}): {resp.text[:500]}",
        )
    return resp.json()


async def _get_or_create_import_region(
    db: AsyncSession, company_id: str
) -> str:
    result = await db.execute(
        select(Region).where(
            Region.company_id == company_id,
            Region.name == IMPORTED_REGION_NAME,
        )
    )
    region = result.scalar_one_or_none()
    if region:
        return region.id
    region = Region(company_id=company_id, name=IMPORTED_REGION_NAME)
    db.add(region)
    await db.flush()
    return region.id


async def _get_or_create_ownership_group(
    db: AsyncSession, company: Company, group_name: str
) -> str:
    if company.ownership_group_id is not None:
        return company.ownership_group_id
    og = OwnershipGroup(name=group_name)
    db.add(og)
    await db.flush()
    company.ownership_group_id = og.id
    return og.id


@router.post("/deputy", response_model=DeputyImportResult)
async def import_from_deputy(
    body: DeputyImportRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> DeputyImportResult:
    base_url = body.deputy_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "Accept": "application/json",
    }
    errors: list[str] = []
    counts: dict[str, dict[str, int]] = {
        "locations": {"created": 0, "updated": 0, "deleted": 0},
        "roles": {"created": 0, "updated": 0, "deleted": 0},
        "employees": {"created": 0, "updated": 0, "deleted": 0},
        "unavailability": {"created": 0, "skipped": 0, "deleted": 0},
    }

    wiz_company_id = current_user.company_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        # ── 1. Ensure ownership group ──
        company_result = await db.execute(
            select(Company).where(Company.id == wiz_company_id)
        )
        current_company = company_result.scalar_one()
        ownership_group_id = await _get_or_create_ownership_group(
            db, current_company, f"{current_company.name} Group"
        )

        # ── 2. Import locations (Deputy "Company" = Wiz "Location") ──
        try:
            deputy_locations = await _deputy_get(
                client, base_url, "/v1/resource/Company", headers
            )
            if not isinstance(deputy_locations, list):
                deputy_locations = [deputy_locations]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not reach Deputy API: {e}",
            )

        region_id = await _get_or_create_import_region(db, wiz_company_id)

        # Load existing Deputy-imported locations
        loc_result = await db.execute(
            select(Location).where(
                Location.company_id == wiz_company_id,
                Location.external_id.isnot(None),
            )
        )
        existing_locs: dict[str, Location] = {
            loc.external_id: loc for loc in loc_result.scalars().all()
            if loc.external_id and loc.external_id.startswith("deputy:")
        }

        seen_loc_ext_ids: set[str] = set()
        loc_deputy_to_wiz: dict[int, str] = {}  # Deputy Company.Id -> Wiz Location.id

        for dl in deputy_locations:
            if not dl.get("Active", True):
                continue
            deputy_id = dl["Id"]
            ext_id = f"deputy:{deputy_id}"
            seen_loc_ext_ids.add(ext_id)

            name = dl.get("CompanyName") or f"Location {deputy_id}"
            addr_obj = (dl.get("_DPMetaData") or {}).get("AddressObject") or {}
            address = addr_obj.get("PrintFull") or addr_obj.get("Print") or ""
            # Deputy doesn't expose timezone on locations; default to America/New_York
            timezone = "America/New_York"

            if ext_id in existing_locs:
                loc = existing_locs[ext_id]
                loc.name = name
                loc.address = address
                loc_deputy_to_wiz[deputy_id] = loc.id
                counts["locations"]["updated"] += 1
            else:
                loc = Location(
                    company_id=wiz_company_id,
                    region_id=region_id,
                    name=name,
                    address=address,
                    timezone=timezone,
                    external_id=ext_id,
                )
                db.add(loc)
                await db.flush()
                loc_deputy_to_wiz[deputy_id] = loc.id
                counts["locations"]["created"] += 1

        # ── 3. Import roles (Deputy OperationalUnit = Wiz Role) ──
        try:
            deputy_areas = await _deputy_get(
                client, base_url, "/v1/resource/OperationalUnit", headers
            )
            if not isinstance(deputy_areas, list):
                deputy_areas = [deputy_areas]
        except Exception as e:
            errors.append(f"Failed to fetch Deputy areas: {e}")
            deputy_areas = []

        role_result = await db.execute(
            select(Role).where(
                Role.company_id == wiz_company_id,
                Role.external_id.isnot(None),
            )
        )
        existing_roles: dict[str, Role] = {
            r.external_id: r for r in role_result.scalars().all()
            if r.external_id and r.external_id.startswith("deputy:")
        }

        seen_role_ext_ids: set[str] = set()
        role_deputy_to_wiz: dict[int, str] = {}
        # Map Deputy Company (location) ID → list of Wiz role IDs at that location.
        # Used later to auto-assign employees to all roles at their locations.
        loc_to_role_ids: dict[int, list[str]] = {}

        for da in deputy_areas:
            if not da.get("Active", True):
                continue
            deputy_id = da["Id"]
            ext_id = f"deputy:{deputy_id}"
            seen_role_ext_ids.add(ext_id)
            deputy_loc_id = da.get("Company")  # OperationalUnit belongs to a location

            name = da.get("OperationalUnitName") or f"Area {deputy_id}"

            if ext_id in existing_roles:
                role = existing_roles[ext_id]
                role.name = name
                role_deputy_to_wiz[deputy_id] = role.id
                counts["roles"]["updated"] += 1
            else:
                role = Role(
                    company_id=wiz_company_id,
                    name=name,
                    external_id=ext_id,
                )
                db.add(role)
                await db.flush()
                role_deputy_to_wiz[deputy_id] = role.id
                counts["roles"]["created"] += 1

            if deputy_loc_id is not None:
                loc_to_role_ids.setdefault(deputy_loc_id, []).append(role_deputy_to_wiz[deputy_id])

        # ── 4. Import employees ──
        try:
            deputy_employees = await _deputy_get(
                client, base_url, "/v1/supervise/employee", headers
            )
            if not isinstance(deputy_employees, list):
                deputy_employees = [deputy_employees]
        except Exception as e:
            errors.append(f"Failed to fetch Deputy employees: {e}")
            deputy_employees = []

        emp_result = await db.execute(
            select(Employee).where(
                Employee.company_id == wiz_company_id,
                Employee.external_id.isnot(None),
            )
        )
        existing_emps: dict[str, Employee] = {
            e.external_id: e for e in emp_result.scalars().all()
            if e.external_id and e.external_id.startswith("deputy:")
        }

        seen_emp_ext_ids: set[str] = set()
        emp_deputy_to_wiz: dict[int, str] = {}

        # Track Deputy location IDs per employee for role assignment later
        emp_deputy_loc_ids: dict[int, list[int]] = {}

        for de in deputy_employees:
            if not de.get("Active", True):
                continue
            deputy_id = de["Id"]
            ext_id = f"deputy:{deputy_id}"
            seen_emp_ext_ids.add(ext_id)

            first = de.get("FirstName") or ""
            last = de.get("LastName") or ""
            full_name = f"{first} {last}".strip() or "Unknown"
            # Email may be on the list or detail endpoint
            email = de.get("Email") or None
            if not email:
                try:
                    detail = await _deputy_get(
                        client, base_url,
                        f"/v1/supervise/employee/{deputy_id}",
                        headers,
                    )
                    if isinstance(detail, dict):
                        email = detail.get("Email") or None
                except Exception:
                    pass  # non-critical

            # Location assignments from WorkplaceArray
            wpa = de.get("WorkplaceArray") or []
            location_ids: list[str] = []
            dep_loc_ids: list[int] = []
            for w in wpa:
                dep_loc_id = w.get("Company")
                if dep_loc_id is not None:
                    dep_loc_ids.append(dep_loc_id)
                wiz_loc = loc_deputy_to_wiz.get(dep_loc_id)
                if wiz_loc and wiz_loc not in location_ids:
                    location_ids.append(wiz_loc)

            emp_deputy_loc_ids[deputy_id] = dep_loc_ids

            if ext_id in existing_emps:
                emp = existing_emps[ext_id]
                emp.full_name = full_name
                if email is not None:
                    emp.email = email
                emp.location_ids = location_ids if location_ids else emp.location_ids
                emp_deputy_to_wiz[deputy_id] = emp.id
                counts["employees"]["updated"] += 1
            else:
                emp = Employee(
                    company_id=wiz_company_id,
                    full_name=full_name,
                    email=email,
                    external_id=ext_id,
                    location_ids=location_ids or None,
                )
                db.add(emp)
                await db.flush()
                emp_deputy_to_wiz[deputy_id] = emp.id
                ec = EmployeeCompany(employee_id=emp.id, company_id=wiz_company_id)
                db.add(ec)
                counts["employees"]["created"] += 1

        # ── 4b. Assign employees to roles based on their locations ──
        # Deputy links OperationalUnits to locations (Company). Employees
        # are assigned to all roles at their locations with a default
        # skill level. The EmployeeOperationalUnit resource isn't accessible
        # with standard tokens, so this is the best approximation.
        for deputy_emp_id, wiz_emp_id in emp_deputy_to_wiz.items():
            # Clear existing role assignments for this employee and recreate
            await db.execute(
                delete(EmployeeRole).where(EmployeeRole.employee_id == wiz_emp_id)
            )
            assigned_role_ids: set[str] = set()
            for dep_loc_id in emp_deputy_loc_ids.get(deputy_emp_id, []):
                for wiz_role_id in loc_to_role_ids.get(dep_loc_id, []):
                    if wiz_role_id in assigned_role_ids:
                        continue
                    assigned_role_ids.add(wiz_role_id)
                    er = EmployeeRole(
                        company_id=wiz_company_id,
                        employee_id=wiz_emp_id,
                        role_id=wiz_role_id,
                        skill_level=1,  # default; manager can adjust later
                    )
                    db.add(er)

        # Delete employees no longer in Deputy
        for ext_id, emp in existing_emps.items():
            if ext_id not in seen_emp_ext_ids:
                for tbl in (Shift, EmployeeAffinity, EmployeeAvailability,
                            EmployeeInvite, EmployeeRole, EmployeeRoleMinutes,
                            EmployeeCompany, EmployeeDayBlackout):
                    if tbl == EmployeeAffinity:
                        await db.execute(
                            delete(tbl).where(
                                (tbl.employee_id == emp.id)
                                | (tbl.target_employee_id == emp.id)
                            )
                        )
                    else:
                        await db.execute(
                            delete(tbl).where(tbl.employee_id == emp.id)
                        )
                await db.delete(emp)
                counts["employees"]["deleted"] += 1

        # ── 5. Import unavailability → EmployeeDayBlackout ──
        for deputy_emp_id, wiz_emp_id in emp_deputy_to_wiz.items():
            try:
                unavail_data = await _deputy_get(
                    client, base_url,
                    f"/v1/supervise/unavail/{deputy_emp_id}",
                    headers,
                )
                if not isinstance(unavail_data, list):
                    unavail_data = []
            except Exception as e:
                errors.append(f"Unavailability fetch failed for employee {deputy_emp_id}: {e}")
                continue

            if not unavail_data:
                continue

            # Clear existing Deputy-sourced blackouts for this employee,
            # then recreate from the API response
            await db.execute(
                delete(EmployeeDayBlackout).where(
                    EmployeeDayBlackout.employee_id == wiz_emp_id,
                    EmployeeDayBlackout.company_id == wiz_company_id,
                )
            )

            for entry in unavail_data:
                recurrence = entry.get("recurrence") or {}
                start_raw = entry.get("start") or entry.get("Start")
                end_raw = entry.get("end") or entry.get("End")

                # Parse start/end (may be timestamp dict or ISO string)
                start_str = ""
                end_str = ""
                if isinstance(start_raw, dict):
                    start_str = start_raw.get("timestamp", "")
                elif isinstance(start_raw, str):
                    start_str = start_raw
                if isinstance(end_raw, dict):
                    end_str = end_raw.get("timestamp", "")
                elif isinstance(end_raw, str):
                    end_str = end_raw

                if not start_str or not end_str:
                    counts["unavailability"]["skipped"] += 1
                    continue

                try:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    counts["unavailability"]["skipped"] += 1
                    continue

                start_hm = start_dt.strftime("%H:%M")
                end_hm = end_dt.strftime("%H:%M")

                # Handle recurring unavailability → EmployeeDayBlackout
                byday = recurrence.get("BYDAY") or ""
                freq = recurrence.get("FREQ", "")

                if freq == "WEEKLY" and byday:
                    # Recurring on specific days
                    for day_code in byday.split(","):
                        day_code = day_code.strip().upper()
                        dow = DEPUTY_BYDAY_MAP.get(day_code)
                        if dow is None:
                            continue
                        bo = EmployeeDayBlackout(
                            company_id=wiz_company_id,
                            employee_id=wiz_emp_id,
                            day_of_week=dow,
                            start_time=start_hm,
                            end_time=end_hm if end_hm > start_hm else "23:59",
                        )
                        db.add(bo)
                        counts["unavailability"]["created"] += 1
                else:
                    # One-off unavailability → create blackout for that specific
                    # day of week (recurring). Not ideal but captures the intent.
                    dow = start_dt.weekday()
                    bo = EmployeeDayBlackout(
                        company_id=wiz_company_id,
                        employee_id=wiz_emp_id,
                        day_of_week=dow,
                        start_time=start_hm,
                        end_time=end_hm if end_hm > start_hm else "23:59",
                    )
                    db.add(bo)
                    counts["unavailability"]["created"] += 1

    # Mark ownership group as using Deputy integration
    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == ownership_group_id)
    )
    og = og_result.scalar_one_or_none()
    if og is not None and og.api_integration != "deputy":
        og.api_integration = "deputy"

    await db.commit()
    return DeputyImportResult(
        locations=counts["locations"],
        roles=counts["roles"],
        employees=counts["employees"],
        unavailability=counts["unavailability"],
        errors=errors,
    )


# ── Deputy Availability Import (invert unavailability) ──


class DeputyAvailImportRequest(BaseModel):
    access_token: str
    deputy_base_url: str
    week_start: date
    week_end: date


class DeputyAvailImportResult(BaseModel):
    created: int
    skipped: int
    cleared: int
    errors: list[str]


def _invert_unavailability(
    unavail_windows: list[tuple[str, str]],
    day_start: str = "00:00",
    day_end: str = "23:59",
) -> list[tuple[str, str]]:
    """Given a list of (start_HH:MM, end_HH:MM) unavailability windows for a
    single day, return the inverted availability windows.

    Example: unavail [(08:00, 12:00)] → avail [(00:00, 08:00), (12:00, 23:59)]
    """
    if not unavail_windows:
        return [(day_start, day_end)]

    # Sort by start time
    sorted_windows = sorted(unavail_windows, key=lambda w: w[0])

    # Merge overlapping unavailability windows
    merged: list[tuple[str, str]] = []
    for s, e in sorted_windows:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Invert: gaps between merged unavailability blocks become availability
    available: list[tuple[str, str]] = []
    cursor = day_start
    for s, e in merged:
        if cursor < s:
            available.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        available.append((cursor, day_end))

    return available


@router.post("/deputy/availabilities", response_model=DeputyAvailImportResult)
async def import_availabilities_from_deputy(
    body: DeputyAvailImportRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> DeputyAvailImportResult:
    """Import employee availability from Deputy by inverting unavailability.

    For each employee and each day in the range:
    1. Fetch unavailability windows from Deputy
    2. Invert them to produce availability windows
    3. Create EmployeeAvailability records for the available time
    """
    base_url = body.deputy_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "Accept": "application/json",
    }
    errors: list[str] = []
    created = 0
    skipped = 0
    cleared = 0

    wiz_company_id = current_user.company_id
    range_start = body.week_start
    range_end = body.week_end

    if range_start >= range_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="week_start must be before week_end",
        )

    # Find Deputy-linked employees in this company
    emp_result = await db.execute(
        select(Employee).where(
            Employee.company_id == wiz_company_id,
            Employee.external_id.isnot(None),
        )
    )
    deputy_employees = [
        e for e in emp_result.scalars().all()
        if e.external_id and e.external_id.startswith("deputy:")
    ]

    if not deputy_employees:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Deputy-linked employees found. Import data from Deputy first.",
        )

    # Clear existing availability in the date range for this company
    range_start_dt = datetime(
        range_start.year, range_start.month, range_start.day, tzinfo=timezone.utc
    )
    range_end_dt = datetime(
        range_end.year, range_end.month, range_end.day, 23, 59, 59, tzinfo=timezone.utc
    )
    del_result = await db.execute(
        delete(EmployeeAvailability).where(
            EmployeeAvailability.company_id == wiz_company_id,
            EmployeeAvailability.start_time >= range_start_dt,
            EmployeeAvailability.start_time <= range_end_dt,
        )
    )
    cleared = del_result.rowcount or 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for emp in deputy_employees:
            deputy_id = emp.external_id.replace("deputy:", "")  # type: ignore[union-attr]

            # Fetch unavailability from Deputy
            try:
                unavail_data = await _deputy_get(
                    client, base_url,
                    f"/v1/supervise/unavail/{deputy_id}",
                    headers,
                )
                if not isinstance(unavail_data, list):
                    unavail_data = []
            except Exception as e:
                errors.append(f"Failed to fetch unavailability for {emp.full_name}: {e}")
                skipped += 1
                continue

            # Parse all unavailability into {date_str: [(start_HH:MM, end_HH:MM), ...]}
            unavail_by_date: dict[str, list[tuple[str, str]]] = {}

            for entry in unavail_data:
                start_raw = entry.get("start") or entry.get("Start")
                end_raw = entry.get("end") or entry.get("End")
                recurrence = entry.get("recurrence") or {}

                # Parse timestamps
                start_str = ""
                end_str = ""
                if isinstance(start_raw, dict):
                    start_str = start_raw.get("timestamp", "")
                elif isinstance(start_raw, str):
                    start_str = start_raw
                if isinstance(end_raw, dict):
                    end_str = end_raw.get("timestamp", "")
                elif isinstance(end_raw, str):
                    end_str = end_raw

                if not start_str or not end_str:
                    continue

                try:
                    start_dt = datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    )
                    end_dt = datetime.fromisoformat(
                        end_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    continue

                start_hm = start_dt.strftime("%H:%M")
                end_hm = end_dt.strftime("%H:%M")
                if end_hm == "00:00":
                    end_hm = "23:59"

                freq = recurrence.get("FREQ", "")
                byday = recurrence.get("BYDAY", "")

                if freq == "WEEKLY" and byday:
                    # Recurring: apply to every matching day in range
                    day_codes = [c.strip().upper() for c in byday.split(",")]
                    current_day = range_start
                    while current_day <= range_end:
                        weekday_idx = current_day.weekday()
                        day_name_short = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][weekday_idx]
                        if day_name_short in day_codes:
                            date_key = current_day.isoformat()
                            unavail_by_date.setdefault(date_key, []).append(
                                (start_hm, end_hm)
                            )
                        current_day += timedelta(days=1)
                else:
                    # One-off: apply to the specific date
                    date_key = start_dt.strftime("%Y-%m-%d")
                    unavail_by_date.setdefault(date_key, []).append(
                        (start_hm, end_hm)
                    )

            # For each day in the range, invert unavailability → availability
            current_day = range_start
            while current_day <= range_end:
                date_key = current_day.isoformat()
                unavail_windows = unavail_by_date.get(date_key, [])
                avail_windows = _invert_unavailability(unavail_windows)

                for avail_start, avail_end in avail_windows:
                    parts_s = avail_start.split(":")
                    parts_e = avail_end.split(":")
                    start_dt_rec = datetime(
                        current_day.year, current_day.month, current_day.day,
                        int(parts_s[0]), int(parts_s[1]), 0,
                        tzinfo=timezone.utc,
                    )
                    end_dt_rec = datetime(
                        current_day.year, current_day.month, current_day.day,
                        int(parts_e[0]), int(parts_e[1]), 0,
                        tzinfo=timezone.utc,
                    )
                    record = EmployeeAvailability(
                        company_id=wiz_company_id,
                        employee_id=emp.id,
                        year=current_day.year,
                        month=current_day.month,
                        day=current_day.day,
                        start_time=start_dt_rec,
                        end_time=end_dt_rec,
                    )
                    db.add(record)
                    created += 1

                current_day += timedelta(days=1)

    await db.commit()
    return DeputyAvailImportResult(
        created=created,
        skipped=skipped,
        cleared=cleared,
        errors=errors,
    )
