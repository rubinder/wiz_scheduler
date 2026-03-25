import json
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Shift, ShiftSchedule, User
from backend.schemas.schedule import GenerateRequest, ShiftScheduleResponse, UpdateShiftsRequest

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.post("/generate")
async def generate_schedule(
    body: GenerateRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    from backend.scheduling.graph import run_scheduling_pipeline

    template_ids = (
        [str(tid) for tid in body.template_ids] if body.template_ids else None
    )

    async def event_stream():
        try:
            async for chunk in run_scheduling_pipeline(
                company_id=str(current_user.company_id),
                week_start_date=str(body.week_start_date),
                db=db,
                template_ids=template_ids,
            ):
                # Persist a ShiftSchedule row so approve/reject have a record to find
                loc_id = chunk.get("location_id", "")
                if loc_id:
                    sched = ShiftSchedule(
                        company_id=current_user.company_id,
                        location_id=loc_id,
                        week_start_date=body.week_start_date,
                        status="draft",
                        raw_llm_output=json.dumps(chunk.get("shifts", [])),
                    )
                    db.add(sched)
                    await db.flush()
                    chunk["schedule_id"] = str(sched.id)
                    await db.commit()

                yield json.dumps(chunk) + "\n"
        except Exception as exc:
            # Emit the error as a single NDJSON line so the frontend can display it
            error_result = {
                "location_id": "",
                "location_name": "Pipeline Error",
                "shifts": [],
                "errors": [str(exc)],
                "status": "PIPELINE_ERROR",
            }
            yield json.dumps(error_result) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.put("/{schedule_id}/shifts")
async def update_shifts(
    schedule_id: uuid.UUID,
    body: UpdateShiftsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status == "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit an approved schedule")

    schedule.raw_llm_output = json.dumps([s.model_dump() for s in body.shifts])
    await db.commit()
    return {"ok": True}


@router.post("/{schedule_id}/approve", response_model=ShiftScheduleResponse)
async def approve_schedule(
    schedule_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftScheduleResponse:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status == "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Schedule already approved")

    schedule.status = "approved"

    # Create Shift records from the stored raw_llm_output
    if schedule.raw_llm_output:
        try:
            shifts_data = json.loads(schedule.raw_llm_output)
            for s in shifts_data:
                shift = Shift(
                    company_id=current_user.company_id,
                    shift_schedule_id=schedule.id,
                    location_id=uuid.UUID(s["location_id"]) if isinstance(s["location_id"], str) else s["location_id"],
                    employee_id=uuid.UUID(s["employee_id"]) if isinstance(s["employee_id"], str) else s["employee_id"],
                    role_id=uuid.UUID(s["role_id"]) if isinstance(s["role_id"], str) else s["role_id"],
                    role_name=s["role_name"],
                    date=date.fromisoformat(s["date"]) if isinstance(s["date"], str) else s["date"],
                    start_time=datetime.fromisoformat(s["start_time"]) if isinstance(s["start_time"], str) else s["start_time"],
                    end_time=datetime.fromisoformat(s["end_time"]) if isinstance(s["end_time"], str) else s["end_time"],
                )
                db.add(shift)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to parse schedule data: {exc}",
            )

    await db.commit()
    await db.refresh(schedule)

    # Fetch shifts for response
    shift_result = await db.execute(
        select(Shift).where(Shift.shift_schedule_id == schedule.id)
    )
    shifts = shift_result.scalars().all()

    resp = ShiftScheduleResponse.model_validate(schedule)
    resp.shifts = [_shift_to_response(s) for s in shifts]
    return resp


@router.post("/{schedule_id}/reject", response_model=ShiftScheduleResponse)
async def reject_schedule(
    schedule_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftScheduleResponse:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    schedule.status = "rejected"
    await db.commit()
    await db.refresh(schedule)
    return ShiftScheduleResponse.model_validate(schedule)


@router.get("/week/{week_start_date}", response_model=list[ShiftScheduleResponse])
async def get_week_schedules(
    week_start_date: date,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ShiftScheduleResponse]:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.company_id == current_user.company_id,
            ShiftSchedule.week_start_date == week_start_date,
        )
    )
    schedules = result.scalars().all()

    responses: list[ShiftScheduleResponse] = []
    for sched in schedules:
        shift_result = await db.execute(
            select(Shift).where(Shift.shift_schedule_id == sched.id)
        )
        shifts = shift_result.scalars().all()
        resp = ShiftScheduleResponse.model_validate(sched)
        resp.shifts = [_shift_to_response(s) for s in shifts]
        responses.append(resp)

    return responses


def _shift_to_response(shift: Shift) -> dict:
    """Convert a Shift ORM object to a dict matching ShiftResponse."""
    from backend.schemas.schedule import ShiftResponse

    return ShiftResponse.model_validate(shift)
