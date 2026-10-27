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
from backend.models.preference_submission import EmployeePreferenceSubmission
from backend.services.check_in import local_date_for


class EmployeeErased(Exception):
    """GDPR-erased employees are archived for good."""


# Terminal status for a draft whose location was archived (#126). Counted
# by the free quota exactly like a draft; never listed, edited or approved.
DISCARDED = "discarded"


@dataclass
class ArchiveCounts:
    opened: int = 0    # future approved shifts converted to open shifts
    deleted: int = 0   # future approved shifts removed (archived location)
    discarded: int = 0  # draft schedules moved to DISCARDED (archived location)


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


async def lock_employees(db: AsyncSession, company_id: str,
                         employee_ids) -> dict[str, datetime | None]:
    """Row-lock these employees and return each one's committed archived_at.

    Every path that archives an employee, or gives one a future shift, takes
    this lock first, so an assignment cannot commit a shift for someone
    archived concurrently: whichever transaction locks second sees the
    other's result. Rows are locked in id order, the one lock order used
    everywhere, so two such transactions cannot deadlock on each other.
    Columns, not entities, are selected so a stale identity-map copy cannot
    hide a concurrent archive.
    """
    ids = sorted({str(i) for i in employee_ids if i})
    if not ids:
        return {}
    rows = await db.execute(
        select(Employee.id, Employee.archived_at)
        .where(Employee.company_id == company_id, Employee.id.in_(ids))
        .order_by(Employee.id).with_for_update()
    )
    return {str(eid): archived_at for eid, archived_at in rows.all()}


async def archive_employee(db: AsyncSession, company_id: str, employee: Employee,
                           *, now: datetime | None = None) -> ArchiveCounts:
    archived_at = (await lock_employees(db, company_id, [employee.id])).get(str(employee.id))
    if employee.archived_at is None and archived_at is not None:
        employee.archived_at = archived_at  # archived concurrently; we lost the race
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


async def delete_past_check_ins(db: AsyncSession, where: ColumnElement[bool],
                                *, now: datetime) -> int:
    """Delete matching check-ins dated before their location's current local date.

    Today's rows at each location are kept: the QR rotation counter is the
    count of that day's check-ins and (location_id, local_date, counter) is
    unique, so deleting one would make the next scan reuse a counter and be
    rejected as code_already_used until local midnight. TimeEntry.check_in_id
    is nulled first; time entries keep their own checked_in_at snapshot.
    """
    rows = (await db.execute(
        select(EmployeeCheckIn.id, EmployeeCheckIn.local_date, Location)
        .join(Location, Location.id == EmployeeCheckIn.location_id)
        .where(where)
    )).all()
    stale = [cid for cid, local_date, loc in rows if local_date < local_date_for(loc, now)]
    if not stale:
        return 0
    await db.execute(
        update(TimeEntry).where(TimeEntry.check_in_id.in_(stale))
        .values(check_in_id=None).execution_options(synchronize_session=False)
    )
    result = await db.execute(delete(EmployeeCheckIn).where(EmployeeCheckIn.id.in_(stale))
                              .execution_options(synchronize_session=False))
    return result.rowcount or 0


async def erase_employee(db: AsyncSession, company_id: str, employee: Employee,
                         *, now: datetime | None = None) -> None:
    """GDPR erasure: personal data goes, the row and its pay history stay (#126).

    `full_name` is kept so past schedules and time entries stay readable.
    The caller deletes the User row and consents.
    """
    now = _now(now)
    await archive_employee(db, company_id, employee, now=now)
    emp_id = employee.id
    # Attendance logs are not pay records, so earlier days' check-ins go now.
    # Today's rows are kept (see delete_past_check_ins); the daily retention
    # sweep removes them once the day ends. They hold no contact data once
    # the employee is erased.
    await delete_past_check_ins(db, EmployeeCheckIn.employee_id == emp_id, now=now)
    # The row stays, so the ON DELETE CASCADE tables do not fire: delete explicitly.
    for model in (EmployeeAvailability, EmployeeDayBlackout, EmployeeDayPreference,
                  EmployeeHourRangeCap, EmployeeHourRangePreference, EmployeeRole,
                  EmployeeRoleMinutes, EmployeeCompany, EmployeeInvite,
                  EmployeePreferenceSubmission):
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
    """Archive a location: its future approved shifts go, its drafts are discarded.

    Unapproved schedules are NOT deleted. The free plan's per-location
    allowance and the paid overage meter both count this month's
    ShiftSchedule rows (services/location_quota, billing.count_schedules_this_month),
    so deleting them would let archive -> restore -> generate reset both.
    Drafts move to the terminal status DISCARDED instead: kept and counted
    like a draft, but never listed, edited, rejected or approved.
    """
    # Row lock first: approve_schedule takes the same lock before reading
    # archived_at, so an approval cannot slip future shifts in mid-archive.
    archived_at = (await db.execute(
        select(Location.archived_at).where(Location.id == location.id).with_for_update()
    )).scalar_one_or_none()
    if location.archived_at is None and archived_at is not None:
        location.archived_at = archived_at  # archived concurrently; we lost the race
    if location.archived_at is not None:
        return ArchiveCounts()
    now = _now(now)
    # The location is closing: its future work is cancelled, not refilled.
    future = await db.execute(
        delete(Shift).where(*_future_approved(company_id, now), Shift.location_id == location.id)
        .execution_options(synchronize_session=False)
    )
    discarded = await db.execute(
        update(ShiftSchedule).where(
            ShiftSchedule.company_id == company_id, ShiftSchedule.location_id == location.id,
            ShiftSchedule.status == "draft",
        ).values(status=DISCARDED).execution_options(synchronize_session=False)
    )
    location.archived_at = now
    return ArchiveCounts(deleted=future.rowcount or 0, discarded=discarded.rowcount or 0)


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
