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
    Employee, EmployeeAffinity, EmployeeAvailability, EmployeeCheckIn, EmployeeCompany,
    EmployeeDayBlackout, EmployeeDayPreference, EmployeeHourRangeCap, EmployeeHourRangePreference,
    EmployeeInvite, EmployeeRole, EmployeeRoleMinutes, Location, Role, Shift, ShiftSchedule,
    TimeEntry,
)
from backend.services.check_in import local_date_for


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


async def erase_employee(db: AsyncSession, company_id: str, employee: Employee,
                         *, now: datetime | None = None) -> None:
    """GDPR erasure: personal data goes, the row and its pay history stay (#126).

    `full_name` is kept so past schedules and time entries stay readable.
    The caller deletes the User row and consents.
    """
    now = _now(now)
    await archive_employee(db, company_id, employee, now=now)
    emp_id = employee.id
    # Attendance logs are not pay records, so earlier days' check-ins go; time
    # entries keep their own checked_in_at snapshot. Today's rows at each
    # location are kept: the QR rotation counter is the count of that day's
    # check-ins and (location_id, local_date, counter) is unique, so deleting
    # one would make the next scan reuse a counter and be rejected as
    # code_already_used until local midnight. They hold no contact data once
    # the employee is erased, and the retention sweep removes them.
    rows = (await db.execute(
        select(EmployeeCheckIn.id, EmployeeCheckIn.local_date, Location)
        .join(Location, Location.id == EmployeeCheckIn.location_id)
        .where(EmployeeCheckIn.employee_id == emp_id)
    )).all()
    stale = [cid for cid, local_date, loc in rows if local_date < local_date_for(loc, now)]
    if stale:
        await db.execute(
            update(TimeEntry).where(TimeEntry.check_in_id.in_(stale))
            .values(check_in_id=None).execution_options(synchronize_session=False)
        )
        await db.execute(delete(EmployeeCheckIn).where(EmployeeCheckIn.id.in_(stale))
                         .execution_options(synchronize_session=False))
    # The row stays, so the ON DELETE CASCADE tables do not fire: delete explicitly.
    for model in (EmployeeAvailability, EmployeeDayBlackout, EmployeeDayPreference,
                  EmployeeHourRangeCap, EmployeeHourRangePreference, EmployeeRole,
                  EmployeeRoleMinutes, EmployeeCompany, EmployeeInvite):
        await db.execute(delete(model).where(model.employee_id == emp_id)
                         .execution_options(synchronize_session=False))
    await db.execute(
        delete(EmployeeAffinity).where(
            (EmployeeAffinity.employee_id == emp_id) | (EmployeeAffinity.target_employee_id == emp_id)
        ).execution_options(synchronize_session=False)
    )
    # The bulk delete above bypassed the session; drop the stale selectin-loaded roles.
    db.expire(employee, ["roles"])
    employee.erased_at = now
    employee.email = None
    employee.user_id = None
    employee.external_id = None
    employee.timezone = None
    employee.pay_rate = None
    employee.hire_date = None
    employee.seniority_rank = None
    employee.location_ids = None


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
