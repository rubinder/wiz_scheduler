"""Employee preference submissions (#189): employee-side routes.

Managers review these in the same router (added separately); nothing here
touches the six live preference tables.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db
from backend.models import User
from backend.models.preference_submission import EmployeePreferenceSubmission
from backend.schemas.preference_submission import (
    Coworker,
    MySubmissionState,
    PreferenceSubmissionPayload,
)
from backend.services import preference_submissions as svc

router = APIRouter(prefix="/preference-submissions", tags=["preference-submissions"])


async def _employee(db: AsyncSession, user: User):
    if user.user_role != "employee":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No employee profile found")
    return await svc.load_my_employee(db, user.id, user.company_id)


@router.get("/me", response_model=MySubmissionState)
async def get_my_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    emp = await _employee(db, current_user)
    return await svc.build_my_state(db, emp)


@router.put("/me", response_model=MySubmissionState)
async def submit_my_preferences(
    body: PreferenceSubmissionPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    emp = await _employee(db, current_user)
    if not await svc.is_enabled(db, emp.company_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=svc.DISABLED_CODE)
    await svc.validate_affinity_targets(
        db, emp, [a.target_employee_id for a in body.affinities]
    )
    await db.execute(
        update(EmployeePreferenceSubmission)
        .where(
            EmployeePreferenceSubmission.employee_id == emp.id,
            EmployeePreferenceSubmission.status == "pending",
        )
        .values(status="superseded")
    )
    db.add(EmployeePreferenceSubmission(
        company_id=emp.company_id,
        employee_id=emp.id,
        status="pending",
        submitted_payload=body.model_dump(mode="json"),
    ))
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent submit won the one-pending-per-employee index.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="A submission is already being saved; try again."
        )
    return await svc.build_my_state(db, emp)


@router.get("/me/coworkers", response_model=list[Coworker])
async def my_coworkers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Coworker]:
    emp = await _employee(db, current_user)
    return [Coworker(id=e.id, full_name=e.full_name)
            for e in await svc.share_location_coworkers(db, emp)]
