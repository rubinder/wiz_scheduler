import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, User
from backend.schemas.location import LocationCreate, LocationResponse, LocationUpdate

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/", response_model=list[LocationResponse])
async def list_locations(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    result = await db.execute(
        select(Location).where(Location.company_id == current_user.company_id)
    )
    locations = result.scalars().all()
    return [LocationResponse.model_validate(loc) for loc in locations]


@router.post("/", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    location = Location(
        company_id=current_user.company_id,
        region_id=body.region_id,
        name=body.name,
        address=body.address,
        geo_coord=body.geo_coord,
        timezone=body.timezone,
    )
    db.add(location)
    await db.commit()
    await db.refresh(location)
    return LocationResponse.model_validate(location)


@router.put("/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: uuid.UUID,
    body: LocationUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == current_user.company_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    if body.region_id is not None:
        location.region_id = body.region_id
    if body.name is not None:
        location.name = body.name
    if body.address is not None:
        location.address = body.address
    if body.geo_coord is not None:
        location.geo_coord = body.geo_coord
    if body.timezone is not None:
        location.timezone = body.timezone

    await db.commit()
    await db.refresh(location)
    return LocationResponse.model_validate(location)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == current_user.company_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    await db.delete(location)
    await db.commit()
