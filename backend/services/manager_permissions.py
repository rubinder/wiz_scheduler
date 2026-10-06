"""Manager permission checks for location-scoped access control."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User, ManagerLocation, Location


async def get_accessible_location_ids(db: AsyncSession, user: User) -> set[str]:
    """Get all location IDs accessible to a manager.

    - Admin managers: all locations in their company
    - Regular managers: only assigned locations
    - Non-managers: empty set

    Args:
        db: Database session
        user: The manager user

    Returns:
        Set of location IDs the manager can access
    """
    if user.user_role != "manager" or not user.manager_type:
        return set()

    if user.manager_type == "admin":
        # Admins can access all locations in their company
        result = await db.execute(
            select(Location.id).where(Location.company_id == user.company_id)
        )
        return set(result.scalars().all())

    # Regular managers can only access assigned locations
    result = await db.execute(
        select(ManagerLocation.location_id).where(
            ManagerLocation.manager_id == user.id
        )
    )
    return set(result.scalars().all())


async def can_access_location(
    db: AsyncSession, user: User, location_id: str
) -> bool:
    """Check if a manager can access a specific location.

    Args:
        db: Database session
        user: The manager user
        location_id: Location to check access for

    Returns:
        True if the user can access this location
    """
    accessible = await get_accessible_location_ids(db, user)
    return location_id in accessible


async def get_manager_locations(
    db: AsyncSession, manager_id: str
) -> list[Location]:
    """Get all locations assigned to a manager.

    Args:
        db: Database session
        manager_id: Manager user ID

    Returns:
        List of Location objects
    """
    result = await db.execute(
        select(Location)
        .join(ManagerLocation, Location.id == ManagerLocation.location_id)
        .where(ManagerLocation.manager_id == manager_id)
    )
    return result.scalars().all()


async def assign_location_to_manager(
    db: AsyncSession, manager_id: str, location_id: str
) -> ManagerLocation:
    """Assign a location to a regular manager.

    Args:
        db: Database session
        manager_id: Manager user ID
        location_id: Location to assign

    Returns:
        The created ManagerLocation record

    Raises:
        ValueError: If manager or location not found, or manager is admin
    """
    # Verify manager exists and is a regular manager
    manager_result = await db.execute(
        select(User).where(User.id == manager_id)
    )
    manager = manager_result.scalar_one_or_none()
    if not manager:
        raise ValueError(f"Manager {manager_id} not found")
    if manager.manager_type != "regular":
        raise ValueError("Can only assign locations to regular managers")

    # Verify location exists in the same company
    location_result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == manager.company_id
        )
    )
    location = location_result.scalar_one_or_none()
    if not location:
        raise ValueError(f"Location {location_id} not found in manager's company")

    # Create the assignment
    manager_location = ManagerLocation(
        manager_id=manager_id,
        location_id=location_id
    )
    db.add(manager_location)
    await db.commit()
    await db.refresh(manager_location)
    return manager_location


async def revoke_location_from_manager(
    db: AsyncSession, manager_id: str, location_id: str
) -> None:
    """Revoke a location from a regular manager.

    Args:
        db: Database session
        manager_id: Manager user ID
        location_id: Location to revoke

    Raises:
        ValueError: If this is the manager's last location
    """
    # Check if this is the last location
    remaining_result = await db.execute(
        select(ManagerLocation).where(ManagerLocation.manager_id == manager_id)
    )
    all_assignments = remaining_result.scalars().all()

    if len(all_assignments) == 1 and all_assignments[0].location_id == location_id:
        raise ValueError("Cannot revoke the manager's last location")

    # Remove the assignment
    result = await db.execute(
        select(ManagerLocation).where(
            ManagerLocation.manager_id == manager_id,
            ManagerLocation.location_id == location_id
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment:
        await db.delete(assignment)
        await db.commit()
