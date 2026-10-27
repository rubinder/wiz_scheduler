"""Employee preference submissions (#189).

Employee routes (`/me`) submit and read; they never touch the six live
preference tables. Manager routes list, review, approve and deny; only
approve writes the live tables. The employee is emailed after a decision
commits, and a failed email never fails the decision.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, get_location_scope, require_manager
from backend.models import Company, Employee, User
from backend.models.preference_submission import EmployeePreferenceSubmission
from backend.schemas.preference_submission import (
    ApprovedPayload,
    Coworker,
    DenyBody,
    MySubmissionState,
    PreferenceSubmissionPayload,
    ReviewListItem,
    SubmissionDetail,
)
from backend.services import preference_submissions as svc
from backend.services.email_quota import check_and_log_email
from backend.services.location_scope import LocationScope
from backend.services.preference_decision_email import (
    render_decision_email,
    send_preference_decision_email,
)

logger = logging.getLogger(__name__)

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


# ── Manager review ──

@router.get("/", response_model=list[ReviewListItem])
async def list_pending(
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await svc.list_pending_for_review(db, current_user.company_id, scope)


@router.get("/{submission_id}", response_model=SubmissionDetail)
async def get_submission(
    submission_id: str,
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sub, emp = await svc.load_for_review(db, submission_id, current_user.company_id, scope)
    return await svc.build_review_detail(db, sub, emp)


@router.post("/{submission_id}/approve", response_model=SubmissionDetail)
async def approve_submission(
    submission_id: str,
    body: ApprovedPayload,
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sub, emp = await svc.load_for_review(
        db, submission_id, current_user.company_id, scope, lock=True
    )
    svc.require_pending(sub)
    sub.approved_payload = await svc.apply_approved(db, emp, body, scope)
    sub.status = "approved"
    sub.reviewed_by = current_user.id
    sub.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    detail = await svc.build_review_detail(db, sub, emp)
    await _notify_decision(db, sub, emp)
    return detail


@router.post("/{submission_id}/deny", response_model=SubmissionDetail)
async def deny_submission(
    submission_id: str,
    body: DenyBody,
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sub, emp = await svc.load_for_review(
        db, submission_id, current_user.company_id, scope, lock=True
    )
    svc.require_pending(sub)
    sub.status = "denied"
    sub.denial_reason = body.reason
    sub.reviewed_by = current_user.id
    sub.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    detail = await svc.build_review_detail(db, sub, emp)
    await _notify_decision(db, sub, emp)
    return detail


async def _notify_decision(
    db: AsyncSession, sub: EmployeePreferenceSubmission, emp: Employee
) -> None:
    """Email the submitting employee. Runs after the decision commit and
    swallows every failure: the decision stands whatever happens here."""
    try:
        to = emp.email
        if not to and emp.user_id:
            to = (await db.execute(
                select(User.email).where(User.id == emp.user_id)
            )).scalar_one_or_none()
        if not to:
            return
        company = (await db.execute(
            select(Company.name, Company.ownership_group_id).where(Company.id == emp.company_id)
        )).one()
        approved = sub.approved_payload if sub.status == "approved" else None
        names = await svc.coworker_names(
            db, emp.company_id,
            [a["target_employee_id"] for a in (approved or {}).get("affinities", [])],
        )
        subject, html = render_decision_email(
            employee_name=emp.full_name,
            decision="approved" if sub.status == "approved" else "denied",
            approved=approved,
            denial_reason=sub.denial_reason,
            coworker_names=names,
            group_name=company.name,
        )
        if await check_and_log_email(db, company.ownership_group_id, "preference_decision"):
            await db.commit()
            await send_preference_decision_email(to, subject, html)
    except Exception:
        logger.exception("preference decision notification failed for submission %s", sub.id)
        try:
            await db.rollback()
        except Exception:
            logger.exception("rollback after notification failure failed")
