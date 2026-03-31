from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Company, OwnershipGroup, User
from backend.schemas.ownership_group import (
    CompanySummary,
    OwnershipGroupCompaniesResponse,
    OwnershipGroupCreate,
    OwnershipGroupResponse,
)

router = APIRouter(prefix="/ownership-group", tags=["ownership-group"])


@router.get("/", response_model=OwnershipGroupCompaniesResponse)
async def get_ownership_group(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> OwnershipGroupCompaniesResponse:
    """Get the current user's ownership group and all companies in it."""
    # Get the user's company
    company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = company_result.scalar_one_or_none()
    if company is None or company.ownership_group_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ownership group found for this company",
        )

    # Get the ownership group
    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == company.ownership_group_id)
    )
    ownership_group = og_result.scalar_one()

    # Get all companies in the group
    companies_result = await db.execute(
        select(Company).where(Company.ownership_group_id == company.ownership_group_id)
    )
    companies = companies_result.scalars().all()

    return OwnershipGroupCompaniesResponse(
        ownership_group=OwnershipGroupResponse.model_validate(ownership_group),
        companies=[CompanySummary.model_validate(c) for c in companies],
    )


@router.put("/", response_model=OwnershipGroupResponse)
async def update_ownership_group(
    body: OwnershipGroupCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> OwnershipGroupResponse:
    """Update the ownership group name."""
    company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = company_result.scalar_one_or_none()
    if company is None or company.ownership_group_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ownership group found for this company",
        )

    og_result = await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == company.ownership_group_id)
    )
    ownership_group = og_result.scalar_one()
    ownership_group.name = body.name

    await db.commit()
    await db.refresh(ownership_group)
    return OwnershipGroupResponse.model_validate(ownership_group)


@router.post("/companies", response_model=CompanySummary, status_code=status.HTTP_201_CREATED)
async def add_company_to_group(
    body: OwnershipGroupCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CompanySummary:
    """Create a new company and add it to the current ownership group."""
    company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    current_company = company_result.scalar_one_or_none()
    if current_company is None or current_company.ownership_group_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No ownership group found for this company",
        )

    import secrets

    slug = secrets.token_hex(3)
    new_company = Company(
        name=body.name,
        slug=slug,
        ownership_group_id=current_company.ownership_group_id,
    )
    db.add(new_company)
    await db.commit()
    await db.refresh(new_company)
    return CompanySummary.model_validate(new_company)
