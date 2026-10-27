from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, get_ownership_group_company_ids, require_admin, require_manager
from backend.models import Company, User
from backend.schemas.company import CompanyResponse, CompanyUpdate, PreferenceSubmissionsToggle
from backend.services.plan import assert_paid_plan

router = APIRouter(prefix="/company", tags=["company"])


@router.get("/", response_model=CompanyResponse)
async def get_company(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return CompanyResponse.model_validate(company)


@router.get("/all", response_model=list[CompanyResponse])
async def list_group_companies(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    group_company_ids: list = Depends(get_ownership_group_company_ids),
) -> list[CompanyResponse]:
    """List all companies in the current user's ownership group."""
    result = await db.execute(
        select(Company).where(Company.id.in_(group_company_ids))
    )
    companies = result.scalars().all()
    return [CompanyResponse.model_validate(c) for c in companies]


@router.put("/", response_model=CompanyResponse)
async def update_company(
    body: CompanyUpdate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    if body.name is not None:
        company.name = body.name
    # Gate on the VALUE being set, not just the field's presence, so a
    # free-plan company can clear stale overtime settings (set to null)
    # without needing to re-upgrade -- clearing never grants paid-tier behavior.
    if body.overtime_threshold_hours is not None or body.overtime_premium_multiplier is not None:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")
    if "overtime_threshold_hours" in body.model_fields_set:
        company.overtime_threshold_hours = body.overtime_threshold_hours
    if "overtime_premium_multiplier" in body.model_fields_set:
        company.overtime_premium_multiplier = body.overtime_premium_multiplier

    # Update signal_config if provided (gated on paid plan for signal weight features)
    if "signal_config" in body.model_fields_set:
        if body.signal_config is not None:
            await assert_paid_plan(db, str(current_user.company_id), "signal_weight_scheduling")
        company.signal_config = body.signal_config

    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)


@router.put("/settings/employee-preference-submissions", response_model=CompanyResponse)
async def set_employee_preference_submissions(
    body: PreferenceSubmissionsToggle,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> CompanyResponse:
    """Admin-only on/off switch for employee preference submissions (#189).

    Turning it off hides and freezes pending submissions; it never deletes
    or decides them.
    """
    company = (await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )).scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    company.employee_preference_submissions_enabled = body.enabled
    company.employee_preference_submissions_toggled_at = datetime.now(timezone.utc)
    company.employee_preference_submissions_toggled_by = current_user.id
    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)
