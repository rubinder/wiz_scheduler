import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Region, User
from backend.schemas.region import RegionCreate, RegionResponse, RegionUpdate

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("/", response_model=list[RegionResponse])
async def list_regions(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[RegionResponse]:
    result = await db.execute(
        select(Region).where(Region.company_id == current_user.company_id)
    )
    regions = result.scalars().all()
    return [RegionResponse.model_validate(r) for r in regions]


@router.post("/", response_model=RegionResponse, status_code=status.HTTP_201_CREATED)
async def create_region(
    body: RegionCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> RegionResponse:
    region = Region(
        company_id=current_user.company_id,
        name=body.name,
        geo_bounds=body.geo_bounds,
    )
    db.add(region)
    await db.commit()
    await db.refresh(region)
    return RegionResponse.model_validate(region)


@router.put("/{region_id}", response_model=RegionResponse)
async def update_region(
    region_id: uuid.UUID,
    body: RegionUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> RegionResponse:
    result = await db.execute(
        select(Region).where(Region.id == region_id, Region.company_id == current_user.company_id)
    )
    region = result.scalar_one_or_none()
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found")

    if body.name is not None:
        region.name = body.name
    if body.geo_bounds is not None:
        region.geo_bounds = body.geo_bounds

    await db.commit()
    await db.refresh(region)
    return RegionResponse.model_validate(region)


@router.delete("/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_region(
    region_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Region).where(Region.id == region_id, Region.company_id == current_user.company_id)
    )
    region = result.scalar_one_or_none()
    if region is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not found")

    await db.delete(region)
    await db.commit()
