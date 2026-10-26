"""Manager permission checks for location-scoped access control."""

from collections.abc import Iterable

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User, ManagerLocation, Location
from backend.services.archive import active


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
            select(Location.id).where(Location.company_id == user.company_id, active(Location))
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
        .where(ManagerLocation.manager_id == manager_id, active(Location))
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
            Location.company_id == manager.company_id,
            active(Location),
        )
    )
    location = location_result.scalar_one_or_none()
    if not location:
        raise ValueError(f"Location {location_id} not found in manager's company")

    existing = (await db.execute(
        select(ManagerLocation).where(
            ManagerLocation.manager_id == manager_id,
            ManagerLocation.location_id == location_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing

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


class LastAdminError(Exception):
    """Demoting this manager would leave the company without an admin (Q6)."""


def is_admin_manager(user: User) -> bool:
    """NULL was the pre-0049 value for every manager and meant admin."""
    return user.user_role == "manager" and user.manager_type != "regular"


async def lock_company_admin_ids(db: AsyncSession, company_id: str) -> set[str]:
    """Row-lock and return the company's admin managers.

    Every demotion takes these locks first, so two admins demoting each
    other serialize: the second re-reads the first's row, no longer an
    admin, and sees itself as the last one.
    """
    rows = await db.execute(
        select(User.id)
        .where(
            User.company_id == company_id,
            User.user_role == "manager",
            or_(User.manager_type.is_(None), User.manager_type != "regular"),
        )
        .order_by(User.id)
        .with_for_update()
    )
    return set(rows.scalars().all())


async def validate_assignable_locations(
    db: AsyncSession,
    company_id: str,
    location_ids: Iterable[str],
    keep: Iterable[str] = (),
) -> list[str]:
    """Deduplicated ids, each an active location of `company_id`, or an
    archived one listed in `keep` (the manager's current assignments).

    New assignments need an active location, as `assign_location_to_manager`
    does; an archived location already assigned may stay assigned when the
    set is replaced (spec Q7). Raises ValueError naming the first unknown id.
    """
    ids = list(dict.fromkeys(location_ids))
    if not ids:
        return []
    kept = set(keep)
    rows = (await db.execute(
        select(Location.id, Location.archived_at).where(
            Location.id.in_(ids),
            Location.company_id == company_id,
        )
    )).all()
    found = {lid for lid, archived_at in rows if archived_at is None or lid in kept}
    for lid in ids:
        if lid not in found:
            raise ValueError(f"Location {lid} not found in this company")
    return ids


async def replace_manager_locations(
    db: AsyncSession, manager_id: str, location_ids: Iterable[str]
) -> None:
    """Make `location_ids` the manager's whole assignment set. No commit:
    the caller's transaction owns it."""
    await db.execute(delete(ManagerLocation).where(ManagerLocation.manager_id == manager_id))
    for lid in dict.fromkeys(location_ids):
        db.add(ManagerLocation(manager_id=manager_id, location_id=lid))
    await db.flush()
