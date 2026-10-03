from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, get_ownership_group_company_ids, require_manager
from backend.models import Company, User
from backend.schemas.company import CompanyResponse, CompanyUpdate
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
    current_user: User = Depends(require_manager),
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

    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)
