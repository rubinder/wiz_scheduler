"""Which locations a manager may act on (#149, #150).

An admin manager is unrestricted. A regular manager is limited to the
locations assigned in `manager_locations`, archived ones included: history
of an archived location stays theirs, and restoring the location restores
access (planning surfaces already hide archived rows through `active()`).

Routers take `LocationScope` from `dependencies.get_location_scope` and ask
it, never `manager_type`, so "unrestricted" has one definition. A location
outside the scope answers 404, exactly like another company's location, so
a restricted manager cannot probe which ids exist.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, false, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ManagerLocation, User


def is_restricted_manager(user: User) -> bool:
    """Only an explicit 'regular' manager is restricted. NULL was the
    pre-0049 value for every manager and meant admin."""
    return user.user_role == "manager" and user.manager_type == "regular"


@dataclass(frozen=True)
class LocationScope:
    # None = unrestricted (admin). A frozenset, possibly empty, otherwise.
    ids: frozenset[str] | None

    @property
    def restricted(self) -> bool:
        return self.ids is not None

    def allows(self, location_id: str | None) -> bool:
        if self.ids is None:
            return True
        return location_id is not None and location_id in self.ids

    def allows_any(self, location_ids: Iterable[str] | None) -> bool:
        """See rule (spec Q2): any one location in scope. Employees'
        location_ids is a JSON list, so callers filter employees in Python
        with this rather than in SQL."""
        if self.ids is None:
            return True
        return any(lid in self.ids for lid in (location_ids or ()))

    def allows_all(self, location_ids: Iterable[str] | None) -> bool:
        """Edit rule (spec Q2): every location in scope, and at least one,
        so an employee with no locations stays admin-only."""
        if self.ids is None:
            return True
        ids = list(location_ids or ())
        return bool(ids) and all(lid in self.ids for lid in ids)

    def require(self, location_id: str | None, what: str = "Location") -> None:
        if not self.allows(location_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")

    def require_all(self, location_ids: Iterable[str] | None, what: str = "Location") -> None:
        if not self.allows_all(location_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")

    def clause(self, column: ColumnElement) -> ColumnElement[bool]:
        """WHERE clause limiting a location_id column to the scope."""
        if self.ids is None:
            return true()
        if not self.ids:
            return false()
        return column.in_(sorted(self.ids))


UNRESTRICTED = LocationScope(ids=None)


async def location_scope_for(db: AsyncSession, user: User) -> LocationScope:
    if not is_restricted_manager(user):
        return UNRESTRICTED
    rows = await db.execute(
        select(ManagerLocation.location_id).where(ManagerLocation.manager_id == user.id)
    )
    return LocationScope(ids=frozenset(rows.scalars().all()))
