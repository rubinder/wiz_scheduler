from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_current_user, get_db, get_ownership_group_company_ids, require_manager
from backend.models import Company, User
from backend.schemas.company import CompanyResponse, CompanyUpdate

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

    await db.commit()
    await db.refresh(company)
    return CompanyResponse.model_validate(company)
