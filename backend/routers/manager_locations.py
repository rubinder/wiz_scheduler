"""API endpoints for managers: the team list, a manager's type, and their
location assignments (#149, #150)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from backend.dependencies import get_db, require_admin, require_manager
from backend.models import User, Location, ManagerLocation
from backend.services.archive import active
from backend.services.location_scope import is_restricted_manager
from backend.services.manager_permissions import (
    assign_location_to_manager,
    is_admin_manager,
    lock_company_admin_ids,
    replace_manager_locations,
    revoke_location_from_manager,
    get_manager_locations,
    validate_assignable_locations,
)

router = APIRouter(prefix="/managers", tags=["managers"])


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    timezone: str


class ManagerLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    location: LocationResponse


class AssignedLocation(BaseModel):
    id: str
    name: str
    timezone: str
    # An archived location stays assigned (spec Q7): restoring it restores
    # access, so the team page shows it, flagged.
    archived: bool


class ManagerResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    # NULL (pre-0049) is reported as "admin", which is what it means.
    manager_type: Literal["admin", "regular"]
    assigned_locations: list[AssignedLocation]


class UpdateManagerRequest(BaseModel):
    manager_type: Literal["admin", "regular"] | None = None
    location_ids: list[str] | None = None


async def _manager_responses(db: AsyncSession, managers: list[User]) -> list[ManagerResponse]:
    ids = [m.id for m in managers]
    by_manager: dict[str, list[AssignedLocation]] = {mid: [] for mid in ids}
    if ids:
        rows = await db.execute(
            select(ManagerLocation.manager_id, Location)
            .join(Location, Location.id == ManagerLocation.location_id)
            .where(ManagerLocation.manager_id.in_(ids))
            .order_by(Location.name, Location.id)
        )
        for manager_id, loc in rows.all():
            by_manager[manager_id].append(AssignedLocation(
                id=loc.id, name=loc.name, timezone=loc.timezone,
                archived=loc.archived_at is not None,
            ))
    return [
        ManagerResponse(
            id=m.id,
            email=m.email,
            full_name=m.full_name,
            manager_type="regular" if is_restricted_manager(m) else "admin",
            assigned_locations=by_manager[m.id],
        )
        for m in managers
    ]


async def _company_manager(db: AsyncSession, company_id: str, manager_id: str) -> User:
    manager = (await db.execute(
        select(User).where(
            User.id == manager_id,
            User.company_id == company_id,
            User.user_role == "manager",
        )
    )).scalar_one_or_none()
    if manager is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")
    return manager


@router.get("/", response_model=list[ManagerResponse])
async def list_managers(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ManagerResponse]:
    """The caller's company's managers, with type and assigned locations.
    Admin only. Declared before the `/{manager_id}/...` routes."""
    managers = list((await db.execute(
        select(User)
        .where(User.company_id == current_user.company_id, User.user_role == "manager")
        .order_by(User.email, User.id)
    )).scalars().all())
    return await _manager_responses(db, managers)


@router.put("/{manager_id}", response_model=ManagerResponse)
async def update_manager(
    manager_id: str,
    body: UpdateManagerRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ManagerResponse:
    """Change a manager's type and/or replace a regular manager's locations,
    in one transaction. Admin only.

    - regular needs a non-empty set of active locations in the company;
      `location_ids` replaces the set.
    - admin has no assignments: becoming admin deletes them.
    - the company always keeps at least one admin (spec Q6), checked with
      the company's admin rows locked, so two concurrent demotions cannot
      both pass.
    """
    company_id = current_user.company_id
    target = await _company_manager(db, company_id, manager_id)
    was_admin = is_admin_manager(target)
    new_type = body.manager_type or ("admin" if was_admin else "regular")

    if new_type == "admin":
        if body.location_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin managers have every location; location_ids must be empty",
            )
        target.manager_type = "admin"
        await replace_manager_locations(db, target.id, [])
    else:
        if body.location_ids is None and was_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A regular manager needs at least one location",
            )
        if body.location_ids is not None:
            if not body.location_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A regular manager needs at least one location",
                )
            try:
                location_ids = await validate_assignable_locations(
                    db, company_id, body.location_ids
                )
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if was_admin:
            admin_ids = await lock_company_admin_ids(db, company_id)
            if admin_ids - {target.id} == set():
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The company must keep at least one admin manager",
                )
        target.manager_type = "regular"
        if body.location_ids is not None:
            await replace_manager_locations(db, target.id, location_ids)

    await db.commit()
    await db.refresh(target)
    return (await _manager_responses(db, [target]))[0]


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
        select(Location).where(Location.id.in_(location_ids), active(Location))
    )
    locations = result.scalars().all()

    return [LocationResponse.model_validate(loc) for loc in locations]


@router.get("/{manager_id}/locations", response_model=list[LocationResponse])
async def get_manager_location_assignments(
    manager_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    """Get all locations assigned to a manager. Admins may ask about anyone
    in their company; a regular manager only about themselves."""
    if is_restricted_manager(current_user) and current_user.id != manager_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin manager can view another manager's locations",
        )
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
    if not is_restricted_manager(manager):
        loc_result = await db.execute(
            select(Location).where(Location.company_id == current_user.company_id, active(Location))
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
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    """Assign a location to a regular manager. Admin only."""

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
        select(Location).where(Location.id == location_id, active(Location))
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
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a location from a regular manager. Admin only."""

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
