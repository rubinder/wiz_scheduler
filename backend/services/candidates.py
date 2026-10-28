"""Who fits an open shift, in the order a manager should see them (#174).

Tiers:
  1. available, has the role, at the location
  2. has the role, at the location, not available
  3. available, at the location, no role
Alphabetical within a tier. Everyone else (tier 4) is not offered: the
picker shows only people who fit the location. "Available" is services/availability_check.py --
the same definition as the scheduler and the no_availability edit warning.

"Has the role" follows the scheduler's condensed-role equivalence
(scheduling/graph.py role_equivalents + role_to_condensed): a slot carrying a
condensed role id is filled by any member role, and a slot carrying a member
role is filled by that role or any group-mate.

Bulk-loaded in six queries regardless of company size: condensed-role
mappings, employees, employee roles, availability windows, blackouts, and
nearby shifts. Scoring is Python.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CondensedRole,
    CondensedRoleMapping,
    Employee,
    EmployeeAvailability,
    EmployeeDayBlackout,
    EmployeeRole,
    Location,
    Role,
    Shift,
)
from backend.scheduling.graph import _shift_local_face
from backend.services.archive import active
from backend.services.availability_check import blackout_dicts, is_available, window_dict


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


def role_equivalents(role_id: str, mappings: list[tuple[str, str]]) -> set[str]:
    """Employee role ids that fill a slot of ``role_id``, given the company's
    (condensed_role_id, member_role_id) pairs. Mirrors the scheduler: an
    employee holding member role r is offered r, every group-mate of r, and
    every condensed role containing r."""
    groups: dict[str, set[str]] = {}
    for cid, rid in mappings:
        groups.setdefault(cid, set()).add(rid)
    equivalents = {role_id}
    for cid, members in groups.items():
        if role_id == cid or role_id in members:
            equivalents |= members
    return equivalents


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
    # A day either side: a shift instant can sit on the neighbouring UTC date
    # while landing on this one locally, and an overnight span reaches into
    # the next day.
    lo = datetime.combine(day - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(day + timedelta(days=2), time.min, tzinfo=timezone.utc)

    mappings = [
        (str(cid), str(rid)) for cid, rid in (await db.execute(
            select(CondensedRoleMapping.condensed_role_id, CondensedRoleMapping.role_id)
            .join(CondensedRole, CondensedRoleMapping.condensed_role_id == CondensedRole.id)
            .where(CondensedRole.company_id == company_id)
        )).all()
    ]
    equivalent_roles = role_equivalents(role_id, mappings)

    employees = (await db.execute(
        select(Employee).where(Employee.company_id == company_id, active(Employee))
    )).scalars().all()

    with_role = {
        str(eid) for eid in (await db.execute(
            select(EmployeeRole.employee_id).where(
                EmployeeRole.company_id == company_id,
                EmployeeRole.role_id.in_(equivalent_roles),
                # An archived role (#126) qualifies no one, even when it is
                # a group-mate of the slot's role.
                EmployeeRole.role_id.in_(select(Role.id).where(active(Role))),
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
        # Same faces as edit_warnings: each shift read in its own location's
        # zone. Availability is location-local and never converted per
        # employee (#171).
        other_spans = [
            face for face in (
                _shift_local_face(s, loc) for s, loc in shifts.get(eid, [])
            ) if face is not None
        ]
        available = is_available(
            windows=windows.get(eid, []),
            other_spans=other_spans,
            blackouts=blackouts.get(eid, []),
            span_start=span_start,
            span_end=span_end,
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
