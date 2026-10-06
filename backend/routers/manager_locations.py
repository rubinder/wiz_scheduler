"""API endpoints for managing manager location assignments."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.dependencies import get_db, require_manager
from backend.models import User, Location, ManagerLocation
from backend.services.manager_permissions import (
    assign_location_to_manager,
    revoke_location_from_manager,
    get_manager_locations,
)

router = APIRouter(prefix="/managers", tags=["managers"])


class LocationResponse(BaseModel):
    id: str
    name: str
    timezone: str

    class Config:
        from_attributes = True


class ManagerLocationResponse(BaseModel):
    id: str
    location_id: str
    location: LocationResponse

    class Config:
        from_attributes = True


class ManagerResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    manager_type: str
    assigned_locations: list[LocationResponse]

    class Config:
        from_attributes = True


@router.get("/me/locations", response_model=list[LocationResponse])
async def get_current_manager_locations(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    """Get all locations accessible to the current manager."""
    from backend.services.manager_permissions import get_accessible_location_ids

    # Get accessible location IDs
    location_ids = await get_accessible_location_ids(db, current_user)

    if not location_ids:
        return []

    # Fetch the locations
    result = await db.execute(
        select(Location).where(Location.id.in_(location_ids))
    )
    locations = result.scalars().all()

    return [LocationResponse.model_validate(loc) for loc in locations]


@router.get("/{manager_id}/locations", response_model=list[LocationResponse])
async def get_manager_location_assignments(
    manager_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    """Get all locations assigned to a manager."""
    # Verify the manager exists and belongs to the same company
    result = await db.execute(
        select(User).where(
            User.id == manager_id,
            User.company_id == current_user.company_id,
        )
    )
    manager = result.scalar_one_or_none()
    if not manager:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manager not found",
        )

    # For admin managers, return all company locations
    if manager.manager_type == "admin":
        loc_result = await db.execute(
            select(Location).where(Location.company_id == current_user.company_id)
        )
        locations = loc_result.scalars().all()
    else:
        # For regular managers, return only assigned locations
        locations = await get_manager_locations(db, manager_id)

    return [LocationResponse.model_validate(loc) for loc in locations]


@router.post("/{manager_id}/locations/{location_id}", response_model=LocationResponse)
async def assign_location(
    manager_id: str,
    location_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    """Assign a location to a regular manager. Admin only."""
    # Only admins can assign locations
    if current_user.manager_type != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can assign locations",
        )

    # Verify manager is in the same company
    manager_result = await db.execute(
        select(User).where(
            User.id == manager_id,
            User.company_id == current_user.company_id,
        )
    )
    manager = manager_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manager not found",
        )

    try:
        await assign_location_to_manager(db, manager_id, location_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Fetch and return the location
    loc_result = await db.execute(
        select(Location).where(Location.id == location_id)
    )
    location = loc_result.scalar_one_or_none()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )

    return LocationResponse.model_validate(location)


@router.delete("/{manager_id}/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_location(
    manager_id: str,
    location_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a location from a regular manager. Admin only."""
    # Only admins can revoke locations
    if current_user.manager_type != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can revoke locations",
        )

    # Verify manager is in the same company
    manager_result = await db.execute(
        select(User).where(
            User.id == manager_id,
            User.company_id == current_user.company_id,
        )
    )
    manager = manager_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Manager not found",
        )

    try:
        await revoke_location_from_manager(db, manager_id, location_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
