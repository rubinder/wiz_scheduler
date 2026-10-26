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

from backend.models import Employee, ManagerLocation, User


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


# ── Location scope for employees (#149, spec Q2) ──
# An employee is *visible* to a manager when any of their locations is in
# scope and *editable* only when all of them are (and they have at least
# one). Invisible answers 404 like another company's employee. All of these
# are no-ops for an unrestricted (admin) scope.


def require_employee_visible(
    scope: LocationScope, employee: Employee, what: str = "Employee",
) -> None:
    if not scope.allows_any(employee.location_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def require_employee_editable(
    scope: LocationScope, employee: Employee, what: str = "Employee",
) -> None:
    scope.require_all(employee.location_ids, what)


async def visible_employee_ids(
    db: AsyncSession, company_id: str, scope: LocationScope,
) -> set[str] | None:
    """Ids of the company's employees (archived included) this scope may
    see, or None when unrestricted. location_ids is a JSON column, so the
    filter runs in Python."""
    if not scope.restricted:
        return None
    rows = await db.execute(
        select(Employee.id, Employee.location_ids).where(Employee.company_id == company_id)
    )
    return {eid for eid, lids in rows.all() if scope.allows_any(lids)}


async def require_employee_id_editable(
    db: AsyncSession,
    scope: LocationScope,
    company_id: str,
    employee_id: str,
    what: str = "Employee",
) -> None:
    """Editable check by id, for rows that carry only an employee_id."""
    if not scope.restricted:
        return
    employee = (await db.execute(select(Employee).where(
        Employee.id == employee_id, Employee.company_id == company_id,
    ))).scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
    require_employee_editable(scope, employee, what)


async def require_employees_visible(
    db: AsyncSession, scope: LocationScope, employee_ids: Iterable[str | None],
) -> None:
    """Every named employee must be visible: a restricted manager may not
    put someone who works only elsewhere onto a shift. Callers check
    ownership first (services.ownership.assert_owned)."""
    if not scope.restricted:
        return
    ids = {str(e) for e in employee_ids if e}
    if not ids:
        return
    rows = await db.execute(
        select(Employee.id, Employee.location_ids).where(Employee.id.in_(sorted(ids)))
    )
    if any(not scope.allows_any(lids) for _eid, lids in rows.all()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
