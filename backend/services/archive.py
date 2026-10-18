"""Archive instead of delete (#126).

Employees, locations and roles are never hard-deleted outside the
ownership-group wipe: the row stays so every approved shift, check-in and
pay record pointing at it keeps its history. Archiving only changes work
that has not happened yet, and never a shift someone checked in to or was
paid for. Nothing here commits; callers own the transaction.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee, EmployeeCheckIn, EmployeeInvite, Location, Role, Shift, ShiftSchedule, TimeEntry,
)


class EmployeeErased(Exception):
    """GDPR-erased employees are archived for good."""


@dataclass
class ArchiveCounts:
    opened: int = 0
    deleted: int = 0


def active(model) -> ColumnElement[bool]:
    """The one active-row filter: `select(Employee).where(active(Employee))`."""
    return model.archived_at.is_(None)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _untouchable() -> ColumnElement[bool]:
    """A shift someone checked in to or was paid for is history, not a plan."""
    return (
        exists().where(EmployeeCheckIn.shift_id == Shift.id)
        | exists().where(TimeEntry.shift_id == Shift.id)
    )


def _future_approved(company_id: str, now: datetime) -> tuple[ColumnElement[bool], ...]:
    approved = select(ShiftSchedule.id).where(
        ShiftSchedule.company_id == company_id, ShiftSchedule.status == "approved",
    )
    return (
        Shift.company_id == company_id,
        Shift.shift_schedule_id.in_(approved),
        Shift.start_time > now,
        ~_untouchable(),
    )


async def _open(db: AsyncSession, *where: ColumnElement[bool]) -> int:
    result = await db.execute(
        update(Shift).where(*where).values(employee_id=None, preference_violations=[])
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def archive_employee(db: AsyncSession, company_id: str, employee: Employee,
                           *, now: datetime | None = None) -> ArchiveCounts:
    if employee.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    opened = await _open(db, *_future_approved(company_id, now), Shift.employee_id == employee.id)
    await db.execute(
        update(EmployeeInvite)
        .where(EmployeeInvite.company_id == company_id, EmployeeInvite.employee_id == employee.id,
               EmployeeInvite.status == "pending")
        .values(status="revoked").execution_options(synchronize_session=False)
    )
    employee.archived_at = now
    return ArchiveCounts(opened=opened)


async def archive_location(db: AsyncSession, company_id: str, location: Location,
                           *, now: datetime | None = None) -> ArchiveCounts:
    if location.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    # The location is closing: its future work is cancelled, not refilled.
    future = await db.execute(
        delete(Shift).where(*_future_approved(company_id, now), Shift.location_id == location.id)
        .execution_options(synchronize_session=False)
    )
    drafts = select(ShiftSchedule.id).where(
        ShiftSchedule.company_id == company_id, ShiftSchedule.location_id == location.id,
        ShiftSchedule.status != "approved",
    )
    draft_shifts = await db.execute(
        delete(Shift).where(Shift.shift_schedule_id.in_(drafts), ~_untouchable())
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(ShiftSchedule).where(
            ShiftSchedule.id.in_(drafts),
            ~exists().where(Shift.shift_schedule_id == ShiftSchedule.id),
        ).execution_options(synchronize_session=False)
    )
    location.archived_at = now
    return ArchiveCounts(deleted=(future.rowcount or 0) + (draft_shifts.rowcount or 0))


async def archive_role(db: AsyncSession, company_id: str, role: Role,
                       *, now: datetime | None = None) -> ArchiveCounts:
    if role.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    opened = await _open(db, *_future_approved(company_id, now), Shift.role_id == role.id)
    role.archived_at = now
    return ArchiveCounts(opened=opened)


async def restore_employee(db: AsyncSession, company_id: str, employee: Employee) -> None:
    if employee.erased_at is not None:
        raise EmployeeErased(employee.id)
    employee.archived_at = None


async def restore_location(db: AsyncSession, company_id: str, location: Location) -> None:
    location.archived_at = None


async def restore_role(db: AsyncSession, company_id: str, role: Role) -> None:
    role.archived_at = None
