"""Open shifts (#174): who fits one, and how many are coming up."""

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, Shift, ShiftSchedule, User
from backend.services.candidates import rank_candidates, slot_span

router = APIRouter(prefix="/schedules", tags=["schedules"])

# How far ahead the Sidebar badge looks for open shifts.
OPEN_SHIFT_BADGE_DAYS = 14

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class CandidatesRequest(BaseModel):
    location_id: str
    role_id: str
    date: date
    # Local wall-clock at the location. An end at or before the start is the
    # next day.
    start_time: str = Field(pattern=_HHMM)
    end_time: str = Field(pattern=_HHMM)
    # The shift being edited, so its own assignment is not a clash.
    exclude_shift_id: str | None = None


class CandidateResponse(BaseModel):
    employee_id: str
    full_name: str
    tier: int
    available: bool
    has_role: bool
    at_location: bool


class OpenShiftCount(BaseModel):
    count: int


@router.post("/candidates", response_model=list[CandidateResponse])
async def list_candidates(
    body: CandidatesRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[CandidateResponse]:
    location = (await db.execute(
        select(Location).where(
            Location.id == body.location_id,
            Location.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    span_start, span_end = slot_span(body.date, body.start_time, body.end_time)
    ranked = await rank_candidates(
        db, str(current_user.company_id), location, body.role_id,
        span_start, span_end, body.exclude_shift_id,
    )
    return [CandidateResponse(**asdict(c)) for c in ranked]


@router.get("/open-shifts/count", response_model=OpenShiftCount)
async def open_shift_count(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> OpenShiftCount:
    now = datetime.now(timezone.utc)
    count = (await db.execute(
        select(func.count())
        .select_from(Shift)
        .join(ShiftSchedule, Shift.shift_schedule_id == ShiftSchedule.id)
        .where(
            Shift.company_id == current_user.company_id,
            ShiftSchedule.status == "approved",
            Shift.employee_id.is_(None),
            Shift.start_time >= now,
            Shift.start_time < now + timedelta(days=OPEN_SHIFT_BADGE_DAYS),
        )
    )).scalar_one()
    return OpenShiftCount(count=count)
