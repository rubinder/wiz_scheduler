import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

logger = logging.getLogger(__name__)
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Department, Employee, Location, Role, Shift, ShiftSchedule, User

router = APIRouter(prefix="/export", tags=["export"])

SEVEN_SHIFTS_BASE = "https://api.7shifts.com/v2"


# ── Schemas ──


class ApprovedScheduleShift(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    role_id: str
    role_name: str
    date: str
    start_time: str
    end_time: str
    exported_at: str | None


class ApprovedScheduleItem(BaseModel):
    schedule_id: str
    location_id: str
    location_name: str
    week_start_date: str
    shifts: list[ApprovedScheduleShift]
    total_shifts: int
    exported_shifts: int


class Export7ShiftsRequest(BaseModel):
    access_token: str
    shift_ids: list[uuid.UUID]


class Export7ShiftsResult(BaseModel):
    exported: int
    failed: int
    errors: list[str]


# ── Endpoints ──


@router.get("/approved-schedules", response_model=list[ApprovedScheduleItem])
async def list_approved_schedules(
    week_start_date: date | None = None,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovedScheduleItem]:
    """List approved schedules for the current week (or specified week), grouped by location."""
    if week_start_date is None:
        today = date.today()
        # Monday of current week
        week_start_date = today - timedelta(days=today.weekday())

    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.company_id == current_user.company_id,
            ShiftSchedule.status == "approved",
            ShiftSchedule.week_start_date == week_start_date,
        )
    )
    schedules = result.scalars().all()

    # Pre-load locations and employees
    loc_ids = {s.location_id for s in schedules}
    loc_map: dict[uuid.UUID, str] = {}
    if loc_ids:
        loc_result = await db.execute(
            select(Location).where(Location.id.in_(loc_ids))
        )
        for loc in loc_result.scalars().all():
            loc_map[loc.id] = loc.name

    emp_result = await db.execute(
        select(Employee).where(Employee.company_id == current_user.company_id)
    )
    emp_map: dict[uuid.UUID, str] = {}
    for emp in emp_result.scalars().all():
        emp_map[emp.id] = emp.full_name

    items: list[ApprovedScheduleItem] = []
    for sched in schedules:
        shift_result = await db.execute(
            select(Shift).where(Shift.shift_schedule_id == sched.id)
        )
        shifts = shift_result.scalars().all()

        shift_items = [
            ApprovedScheduleShift(
                id=str(s.id),
                employee_id=str(s.employee_id),
                employee_name=emp_map.get(s.employee_id, str(s.employee_id)),
                role_id=str(s.role_id),
                role_name=s.role_name,
                date=s.date.isoformat(),
                start_time=s.start_time.isoformat(),
                end_time=s.end_time.isoformat(),
                exported_at=s.exported_at.isoformat() if s.exported_at else None,
            )
            for s in shifts
        ]

        items.append(
            ApprovedScheduleItem(
                schedule_id=str(sched.id),
                location_id=str(sched.location_id),
                location_name=loc_map.get(sched.location_id, str(sched.location_id)),
                week_start_date=sched.week_start_date.isoformat(),
                shifts=shift_items,
                total_shifts=len(shift_items),
                exported_shifts=sum(1 for s in shift_items if s.exported_at),
            )
        )

    return items


@router.post("/7shifts", response_model=Export7ShiftsResult)
async def export_to_7shifts(
    body: Export7ShiftsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> Export7ShiftsResult:
    """Push selected shifts to 7shifts via their Create Shift API."""
    headers = {
        "Authorization": f"Bearer {body.access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Load the requested shifts
    result = await db.execute(
        select(Shift).where(
            Shift.id.in_(body.shift_ids),
            Shift.company_id == current_user.company_id,
        )
    )
    shifts = list(result.scalars().all())

    if not shifts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No matching shifts found"
        )

    # Look up employee external IDs for 7shifts user_id mapping
    emp_ids = {s.employee_id for s in shifts}
    from backend.models import Employee as EmpModel

    emp_result = await db.execute(
        select(EmpModel).where(EmpModel.id.in_(emp_ids))
    )
    emp_ext_map: dict[uuid.UUID, str | None] = {}
    for emp in emp_result.scalars().all():
        emp_ext_map[emp.id] = emp.external_id

    # Look up location external IDs
    loc_ids = {s.location_id for s in shifts}
    loc_result = await db.execute(
        select(Location).where(Location.id.in_(loc_ids))
    )
    loc_ext_map: dict[uuid.UUID, str | None] = {}
    for loc in loc_result.scalars().all():
        loc_ext_map[loc.id] = loc.external_id

    # Look up role external IDs
    role_ids = {s.role_id for s in shifts}
    role_result = await db.execute(
        select(Role).where(Role.id.in_(role_ids))
    )
    role_ext_map: dict[uuid.UUID, str | None] = {}
    for role in role_result.scalars().all():
        role_ext_map[role.id] = role.external_id

    # Look up department external IDs by location
    loc_ids = {s.location_id for s in shifts}
    dept_result = await db.execute(
        select(Department).where(
            Department.company_id == current_user.company_id,
            Department.location_id.in_(loc_ids),
            Department.external_id.isnot(None),
        )
    )
    # Map location_id -> first department external_id (use first available)
    loc_dept_ext_map: dict[uuid.UUID, str] = {}
    for dept in dept_result.scalars().all():
        if dept.location_id not in loc_dept_ext_map:
            loc_dept_ext_map[dept.location_id] = dept.external_id  # type: ignore[assignment]

    # Get the company external_id for the API path
    from backend.models import Company

    company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = company_result.scalar_one()
    company_ext_id = company.external_id
    if not company_ext_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company has no 7shifts external ID. Import from 7shifts first.",
        )

    exported = 0
    failed = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for shift in shifts:
            emp_ext = emp_ext_map.get(shift.employee_id)
            loc_ext = loc_ext_map.get(shift.location_id)

            if not emp_ext:
                errors.append(
                    f"Shift {shift.id}: employee has no 7shifts external ID, skipped"
                )
                failed += 1
                continue

            if not loc_ext:
                errors.append(
                    f"Shift {shift.id}: location has no 7shifts external ID, skipped"
                )
                failed += 1
                continue

            payload: dict[str, object] = {
                "user_id": int(emp_ext),
                "location_id": int(loc_ext),
                "start": shift.start_time.isoformat(),
                "end": shift.end_time.isoformat(),
            }

            try:
                url = f"{SEVEN_SHIFTS_BASE}/company/{company_ext_id}/shifts"
                logger.warning("7shifts export: POST %s payload=%s", url, json.dumps(payload))
                resp = await client.post(url, headers=headers, json=payload)
                logger.warning("7shifts export: response %s — %s", resp.status_code, resp.text[:500])
                if resp.status_code in (200, 201):
                    shift.exported_at = datetime.now(timezone.utc)
                    exported += 1
                else:
                    errors.append(
                        f"Shift {shift.id}: 7shifts returned {resp.status_code} — {resp.text[:200]}"
                    )
                    failed += 1
            except httpx.RequestError as e:
                errors.append(f"Shift {shift.id}: request error — {e}")
                failed += 1

    await db.commit()
    return Export7ShiftsResult(exported=exported, failed=failed, errors=errors)


@router.get("/jsonl/{schedule_id}")
async def export_jsonl(
    schedule_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    token: str | None = None,
) -> Response:
    """Export an approved schedule's shifts as JSONL."""
    sched_result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
            ShiftSchedule.status == "approved",
        )
    )
    schedule = sched_result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approved schedule not found",
        )

    shift_result = await db.execute(
        select(Shift).where(Shift.shift_schedule_id == schedule.id)
    )
    shifts = shift_result.scalars().all()

    # Lookup employee names
    emp_ids = {s.employee_id for s in shifts}
    emp_result = await db.execute(
        select(Employee).where(Employee.id.in_(emp_ids))
    )
    emp_map: dict[uuid.UUID, str] = {}
    for emp in emp_result.scalars().all():
        emp_map[emp.id] = emp.full_name

    # Lookup location name
    loc_result = await db.execute(
        select(Location).where(Location.id == schedule.location_id)
    )
    loc = loc_result.scalar_one_or_none()
    loc_name = loc.name if loc else str(schedule.location_id)

    lines: list[str] = []
    for s in shifts:
        row = {
            "shift_id": str(s.id),
            "schedule_id": str(s.shift_schedule_id),
            "location_id": str(s.location_id),
            "location_name": loc_name,
            "employee_id": str(s.employee_id),
            "employee_name": emp_map.get(s.employee_id, ""),
            "role_id": str(s.role_id),
            "role_name": s.role_name,
            "date": s.date.isoformat(),
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
            "exported_at": s.exported_at.isoformat() if s.exported_at else None,
        }
        lines.append(json.dumps(row))

    content = "\n".join(lines) + "\n" if lines else ""
    filename = f"schedule_{schedule_id}_{loc_name.replace(' ', '_')}.jsonl"

    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
