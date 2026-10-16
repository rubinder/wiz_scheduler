"""Who fits an open shift, in the order a manager should see them (#174).

Tiers:
  1. available, has the role, at the location
  2. has the role, at the location, not available
  3. available, at the location, no role
Alphabetical within a tier. Everyone else (tier 4) is not offered: the
picker shows only people who fit the location. "Available" is services/availability_check.py --
the same definition as the scheduler and the no_availability edit warning.

Bulk-loaded in five queries regardless of company size: employees, employee
roles, availability windows, blackouts, and nearby shifts. Scoring is Python.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Employee,
    EmployeeAvailability,
    EmployeeDayBlackout,
    EmployeeRole,
    Location,
    Shift,
)
from backend.scheduling.employee_time import needs_conversion
from backend.scheduling.graph import _shift_local_face
from backend.services.availability_check import blackout_dicts, is_available, window_dict
from backend.utils.tz import is_valid_timezone


@dataclass(frozen=True)
class Candidate:
    employee_id: str
    full_name: str
    tier: int
    available: bool
    has_role: bool
    at_location: bool


def tier_for(*, available: bool, has_role: bool, at_location: bool) -> int:
    if at_location and has_role and available:
        return 1
    if at_location and has_role:
        return 2
    if at_location and available:
        return 3
    return 4


def slot_span(day: date, start_hm: str, end_hm: str) -> tuple[datetime, datetime]:
    """A slot's naive wall-clock span at its location. An end at or before
    the start is the next day (an overnight shift)."""
    start = datetime.combine(day, time.fromisoformat(start_hm))
    end = datetime.combine(day, time.fromisoformat(end_hm))
    if end <= start:
        end += timedelta(days=1)
    return start, end


async def rank_candidates(
    db: AsyncSession,
    company_id: str,
    location: Location,
    role_id: str,
    span_start: datetime,
    span_end: datetime,
    exclude_shift_id: str | None = None,
) -> list[Candidate]:
    day = span_start.date()
    # A day either side: an employee-zone window or a shift instant can sit
    # on the neighbouring UTC date while landing on this one locally.
    lo = datetime.combine(day - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(day + timedelta(days=2), time.min, tzinfo=timezone.utc)

    employees = (await db.execute(
        select(Employee).where(Employee.company_id == company_id)
    )).scalars().all()

    with_role = {
        str(eid) for eid in (await db.execute(
            select(EmployeeRole.employee_id).where(
                EmployeeRole.company_id == company_id,
                EmployeeRole.role_id == role_id,
            )
        )).scalars().all()
    }

    windows: dict[str, list[dict[str, str]]] = {}
    for row in (await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.company_id == company_id,
            EmployeeAvailability.start_time >= lo,
            EmployeeAvailability.start_time < hi,
        )
    )).scalars().all():
        windows.setdefault(str(row.employee_id), []).append(window_dict(row))

    blackouts: dict[str, list[dict[str, str]]] = {}
    for row in (await db.execute(
        select(EmployeeDayBlackout).where(EmployeeDayBlackout.company_id == company_id)
    )).scalars().all():
        blackouts.setdefault(str(row.employee_id), []).extend(blackout_dicts([row]))

    shift_query = (
        select(Shift, Location)
        .join(Location, Shift.location_id == Location.id)
        .where(
            Shift.company_id == company_id,
            Location.company_id == company_id,
            Shift.employee_id.isnot(None),
            Shift.start_time >= lo,
            Shift.start_time < hi,
        )
    )
    if exclude_shift_id:
        shift_query = shift_query.where(Shift.id != exclude_shift_id)
    shifts: dict[str, list[tuple[Shift, Location]]] = {}
    for shift, loc in (await db.execute(shift_query)).all():
        shifts.setdefault(str(shift.employee_id), []).append((shift, loc))

    ranked: list[Candidate] = []
    for emp in employees:
        eid = str(emp.id)
        emp_tz = emp.timezone if is_valid_timezone(emp.timezone) else None
        convert = needs_conversion(emp_tz, location.timezone)
        # Same faces as edit_warnings: converted into this location's zone
        # for an employee with a timezone, else each shift's own location.
        other_spans = [
            face for face in (
                _shift_local_face(s, location if convert else loc)
                for s, loc in shifts.get(eid, [])
            ) if face is not None
        ]
        available = is_available(
            windows=windows.get(eid, []),
            other_spans=other_spans,
            blackouts=blackouts.get(eid, []),
            span_start=span_start,
            span_end=span_end,
            emp_tz=emp_tz if convert else None,
            loc_tz=location.timezone if convert else None,
        )
        has_role = eid in with_role
        at_location = str(location.id) in {str(x) for x in (emp.location_ids or [])}
        ranked.append(Candidate(
            employee_id=eid,
            full_name=emp.full_name,
            tier=tier_for(available=available, has_role=has_role, at_location=at_location),
            available=available,
            has_role=has_role,
            at_location=at_location,
        ))

    # Tier 4 -- not at the location, or at it with neither the role nor
    # availability -- is not offered for an open shift.
    ranked = [c for c in ranked if c.tier < 4]
    ranked.sort(key=lambda c: (c.tier, c.full_name.lower()))
    return ranked
