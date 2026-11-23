"""Call capacity (decision 103): the AI host billing page's "Call capacity"
card. Admins only, behind RESERVATIONS_ENABLED. The rules live in
reservations.voice_capacity; operator approval is the script
backend/scripts/approve_capacity_request.py, never an endpoint."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_admin
from backend.models import User
from backend.reservations import voice_capacity, voice_transfers
from backend.schemas.voice_capacity import CapacityOut, CapacityRequestIn, TransferNumberIn


def require_reservations_enabled() -> None:
    if not settings.RESERVATIONS_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/reservations/voice-capacity", tags=["reservations"],
                   dependencies=[Depends(require_reservations_enabled)])


async def _summary(db: AsyncSession, user: User) -> dict:
    return await voice_capacity.capacity_summary(db, str(user.company_id),
                                                 datetime.now(timezone.utc))


@router.get("", response_model=CapacityOut)
async def read_capacity(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _summary(db, user)


@router.post("/requests", response_model=CapacityOut, status_code=201)
async def request_capacity(
    body: CapacityRequestIn,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await voice_capacity.request_capacity(
            db, str(user.company_id), str(user.id), body.location_ids,
            body.expected_peak, body.needed_by, body.note.strip(),
            datetime.now(timezone.utc))
    except voice_capacity.CapacityRequestPending:
        await db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "capacity_request_pending",
            "message": "A capacity request is already waiting for review"})
    except voice_capacity.UnknownLocation:
        await db.rollback()
        raise HTTPException(status_code=422, detail={
            "code": "unknown_location",
            "message": "Choose locations of this company"})
    return await _summary(db, user)


@router.put("/transfer-number", response_model=CapacityOut)
async def write_transfer_number(
    body: TransferNumberIn,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    number = body.default_staff_transfer_number
    if number:
        problem = await voice_transfers.number_problem(db, number)
        if problem:
            raise HTTPException(status_code=422, detail={
                "code": "transfer_number_not_allowed", "message": problem})
    await voice_capacity.set_default_transfer_number(db, str(user.company_id), number)
    await db.commit()
    return await _summary(db, user)
