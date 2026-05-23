"""Special Hours Days endpoints."""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, ShiftTemplate, SpecialHoursDay, User
from backend.schemas.special_hours import (
    CreateSpecialHoursDayRequest,
    SpecialHoursDayResponse,
    UpdateSpecialHoursDayRequest,
)
from backend.services.special_hours import clone_template_for_date

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/special-hours", tags=["special-hours"])


async def _verify_location_in_company(
    db: AsyncSession, location_id: str, company_id: str
) -> Location:
    loc = (await db.execute(
        select(Location).where(
            Location.id == location_id, Location.company_id == company_id
        )
    )).scalar_one_or_none()
    if loc is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return loc


async def _pick_recurring_template(
    db: AsyncSession, location_id: str
) -> ShiftTemplate:
    rows = (await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.location_id == location_id,
            ShiftTemplate.specific_date.is_(None),
        )
    )).scalars().all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "no_recurring_template",
                    "message": "Location has no recurring template"},
        )
    return rows[0]


@router.post("/", response_model=SpecialHoursDayResponse)
async def create_special_hours_day(
    body: CreateSpecialHoursDayRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> SpecialHoursDayResponse:
    if body.close_time <= body.open_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "close_before_open",
                    "message": "close_time must be after open_time"},
        )

    await _verify_location_in_company(
        db, body.location_id, str(current_user.company_id)
    )

    existing = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.location_id == body.location_id,
            SpecialHoursDay.date == body.date,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "duplicate",
                    "message": "Special hours already exist for this date"},
        )

    if body.draft_template_id:
        draft = (await db.execute(
            select(ShiftTemplate).where(
                ShiftTemplate.id == body.draft_template_id,
                ShiftTemplate.company_id == current_user.company_id,
                ShiftTemplate.location_id == body.location_id,
                ShiftTemplate.specific_date.is_(None),
            )
        )).scalar_one_or_none()
        if draft is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_draft_template",
                        "message": "Draft template not found or not a recurring template for this location"},
            )
    else:
        draft = await _pick_recurring_template(db, body.location_id)

    clone = await clone_template_for_date(
        db,
        source=draft,
        target_date=body.date,
        open_time=body.open_time,
        close_time=body.close_time,
        label=body.label,
    )

    row = SpecialHoursDay(
        company_id=current_user.company_id,
        location_id=body.location_id,
        date=body.date,
        open_time=body.open_time,
        close_time=body.close_time,
        label=body.label,
        shift_template_id=clone.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SpecialHoursDayResponse.model_validate(row)


@router.get("/", response_model=list[SpecialHoursDayResponse])
async def list_special_hours_days(
    location_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[SpecialHoursDayResponse]:
    q = select(SpecialHoursDay).where(
        SpecialHoursDay.company_id == current_user.company_id
    )
    if location_id is not None:
        q = q.where(SpecialHoursDay.location_id == location_id)
    if from_date is not None:
        q = q.where(SpecialHoursDay.date >= from_date)
    if to_date is not None:
        q = q.where(SpecialHoursDay.date <= to_date)
    q = q.order_by(SpecialHoursDay.date.asc(), SpecialHoursDay.location_id.asc())
    rows = (await db.execute(q)).scalars().all()
    return [SpecialHoursDayResponse.model_validate(r) for r in rows]


@router.put("/{special_hours_id}", response_model=SpecialHoursDayResponse)
async def update_special_hours_day(
    special_hours_id: str,
    body: UpdateSpecialHoursDayRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> SpecialHoursDayResponse:
    row = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.id == special_hours_id,
            SpecialHoursDay.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    new_open = body.open_time if body.open_time is not None else row.open_time
    new_close = body.close_time if body.close_time is not None else row.close_time
    if new_close <= new_open:
        raise HTTPException(
            status_code=400,
            detail={"code": "close_before_open",
                    "message": "close_time must be after open_time"},
        )

    times_changed = (
        body.open_time is not None or body.close_time is not None
    )
    date_changed = body.date is not None and body.date != row.date

    if body.date is not None:
        row.date = body.date
    if body.open_time is not None:
        row.open_time = body.open_time
    if body.close_time is not None:
        row.close_time = body.close_time
    if body.label is not None:
        row.label = body.label

    if row.shift_template_id and (times_changed or date_changed):
        tmpl = (await db.execute(
            select(ShiftTemplate).where(ShiftTemplate.id == row.shift_template_id)
        )).scalar_one_or_none()
        if tmpl is not None:
            if date_changed:
                tmpl.specific_date = row.date
            if times_changed and tmpl.weekly_schedule:
                day0 = tmpl.weekly_schedule[0]
                open_str = row.open_time.strftime("%H:%M:%S")
                close_str = row.close_time.strftime("%H:%M:%S")
                for r in day0.get("roles", []):
                    r["start_time"] = open_str
                    r["end_time"] = close_str
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tmpl, "weekly_schedule")

    await db.commit()
    await db.refresh(row)
    return SpecialHoursDayResponse.model_validate(row)


@router.delete("/{special_hours_id}", status_code=204)
async def delete_special_hours_day(
    special_hours_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(SpecialHoursDay).where(
            SpecialHoursDay.id == special_hours_id,
            SpecialHoursDay.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    tmpl_id = row.shift_template_id
    await db.delete(row)
    if tmpl_id:
        tmpl = (await db.execute(
            select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
        )).scalar_one_or_none()
        if tmpl is not None:
            await db.delete(tmpl)
    await db.commit()
