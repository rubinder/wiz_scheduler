"""Shared helpers for employee preference submissions (#189).

Used by the employee routes and the manager review routes. Nothing here
commits; callers own the transaction.
"""
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    EmployeeAffinity,
    EmployeeDayBlackout,
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
)
from backend.models.preference_submission import EmployeePreferenceSubmission
from backend.services.archive import active

DISABLED_CODE = "employee_preference_submissions_disabled"


def relationship_from_level(level: float) -> str:
    """-1 -> never_together, >0 -> prefer_with, otherwise prefer_apart."""
    if level == -1:
        return "never_together"
    if level > 0:
        return "prefer_with"
    return "prefer_apart"


async def load_my_employee(db: AsyncSession, user_id: str, company_id: str) -> Employee:
    """The caller's active Employee profile, else 404."""
    emp = (await db.execute(
        select(Employee).where(
            Employee.user_id == user_id,
            Employee.company_id == company_id,
            active(Employee),
        )
    )).scalars().first()
    if emp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No employee profile found")
    return emp


async def is_enabled(db: AsyncSession, company_id: str) -> bool:
    value = (await db.execute(
        select(Company.employee_preference_submissions_enabled).where(Company.id == company_id)
    )).scalar_one_or_none()
    return bool(value)


async def share_location_coworkers(db: AsyncSession, employee: Employee) -> list[Employee]:
    """Active employees (not self) in the same company sharing >=1 location."""
    mine = set(employee.location_ids or [])
    if not mine:
        return []
    rows = (await db.execute(
        select(Employee).where(
            Employee.company_id == employee.company_id,
            Employee.id != employee.id,
            active(Employee),
        ).order_by(Employee.full_name)
    )).scalars().all()
    return [r for r in rows if mine & set(r.location_ids or [])]


async def validate_affinity_targets(
    db: AsyncSession, employee: Employee, target_ids: Iterable[str]
) -> None:
    """422 unless every target is an active, same-company coworker sharing a location.

    Rejects self and duplicates too. Relative to `employee`, so the manager
    approve path can reuse it.
    """
    ids = list(target_ids)
    if len(set(ids)) != len(ids):
        raise HTTPException(422, detail="Duplicate affinity target")
    if employee.id in ids:
        raise HTTPException(422, detail="An employee cannot name themselves")
    if not ids:
        return
    allowed = {e.id for e in await share_location_coworkers(db, employee)}
    bad = [i for i in ids if i not in allowed]
    if bad:
        raise HTTPException(
            422, detail="Affinity target must be an active coworker at a shared location"
        )


async def build_current_view(
    db: AsyncSession, employee: Employee, *, include_values: bool = False
) -> dict[str, Any]:
    """Live values for the six sections.

    include_values=False (employee view): no weight, level or importance;
    affinities are outgoing only, shown as a relationship. include_values=True
    (manager view) adds `weight` on day/hour-range/cap rows and `level` on
    affinities. Archived affinity targets are omitted.
    """
    cid, eid = employee.company_id, employee.id

    async def rows(model: Any, *order: Any) -> list[Any]:
        stmt = select(model).where(model.company_id == cid, model.employee_id == eid)
        return list((await db.execute(stmt.order_by(*order))).scalars().all())

    def w(row: Any) -> dict[str, Any]:
        return {"weight": float(row.weight)} if include_values else {}

    blackouts = await rows(EmployeeDayBlackout, EmployeeDayBlackout.day_of_week,
                           EmployeeDayBlackout.start_time)
    days = await rows(EmployeeDayPreference, EmployeeDayPreference.day_of_week)
    ranges = await rows(EmployeeHourRangePreference, EmployeeHourRangePreference.start_time,
                        EmployeeHourRangePreference.end_time)
    caps = await rows(EmployeeHourRangeCap, EmployeeHourRangeCap.start_time,
                      EmployeeHourRangeCap.end_time)
    affs = await rows(EmployeeAffinity, EmployeeAffinity.entry_date)
    names = {
        e.id: e.full_name
        for e in (await db.execute(
            select(Employee).where(
                Employee.company_id == cid,
                Employee.id.in_([a.target_employee_id for a in affs] or [""]),
                active(Employee),
            )
        )).scalars().all()
    }
    affinities: list[dict[str, Any]] = []
    for a in affs:
        if a.target_employee_id not in names:
            continue
        item: dict[str, Any] = {
            "target_employee_id": a.target_employee_id,
            "target_name": names[a.target_employee_id],
            "relationship": relationship_from_level(float(a.level)),
            "entry_date": a.entry_date,
            "expiration_date": a.expiration_date,
        }
        if include_values:
            item["level"] = float(a.level)
        affinities.append(item)
    return {
        "max_hours_per_week": employee.max_hours_per_week,
        "affinities": affinities,
        "blackouts": [
            {"day_of_week": b.day_of_week, "start_time": b.start_time, "end_time": b.end_time}
            for b in blackouts
        ],
        "day_preferences": [{"day_of_week": d.day_of_week, **w(d)} for d in days],
        "hour_range_preferences": [
            {"start_time": r.start_time, "end_time": r.end_time, **w(r)} for r in ranges
        ],
        "caps": [
            {"start_time": c.start_time, "end_time": c.end_time,
             "max_per_week": c.max_per_week, **w(c)}
            for c in caps
        ],
    }


async def redact_approved(
    db: AsyncSession, company_id: str, approved: dict[str, Any]
) -> dict[str, Any]:
    """Employee-safe summary of an approved payload: coworker names only,
    no relationship, level, weight or importance."""
    target_ids = [a["target_employee_id"] for a in approved.get("affinities", [])]
    names = {
        e.id: e.full_name
        for e in (await db.execute(
            select(Employee).where(Employee.company_id == company_id,
                                   Employee.id.in_(target_ids or [""]))
        )).scalars().all()
    }
    return {
        "max_hours_per_week": approved.get("max_hours_per_week"),
        "coworkers": [{"target_name": names[t]} for t in target_ids if t in names],
        "blackouts": [
            {k: b[k] for k in ("day_of_week", "start_time", "end_time")}
            for b in approved.get("blackouts", [])
        ],
        "day_preferences": [{"day_of_week": d["day_of_week"]}
                            for d in approved.get("day_preferences", [])],
        "hour_range_preferences": [
            {"start_time": r["start_time"], "end_time": r["end_time"]}
            for r in approved.get("hour_range_preferences", [])
        ],
        "caps": [
            {k: c[k] for k in ("start_time", "end_time", "max_per_week")}
            for c in approved.get("caps", [])
        ],
    }


async def get_pending(db: AsyncSession, employee_id: str) -> EmployeePreferenceSubmission | None:
    return (await db.execute(
        select(EmployeePreferenceSubmission).where(
            EmployeePreferenceSubmission.employee_id == employee_id,
            EmployeePreferenceSubmission.status == "pending",
        )
    )).scalars().first()


async def get_last_decision(
    db: AsyncSession, employee: Employee
) -> dict[str, Any] | None:
    row = (await db.execute(
        select(EmployeePreferenceSubmission).where(
            EmployeePreferenceSubmission.employee_id == employee.id,
            EmployeePreferenceSubmission.company_id == employee.company_id,
            EmployeePreferenceSubmission.status.in_(("approved", "denied")),
        ).order_by(EmployeePreferenceSubmission.reviewed_at.desc().nulls_last(),
                   EmployeePreferenceSubmission.submitted_at.desc())
    )).scalars().first()
    if row is None:
        return None
    approved = None
    if row.status == "approved" and row.approved_payload is not None:
        approved = await redact_approved(db, employee.company_id, row.approved_payload)
    return {
        "status": row.status,
        "reviewed_at": row.reviewed_at,
        "denial_reason": row.denial_reason,
        "approved": approved,
    }


async def build_my_state(db: AsyncSession, employee: Employee) -> dict[str, Any]:
    pending = await get_pending(db, employee.id)
    return {
        "enabled": await is_enabled(db, employee.company_id),
        "current": await build_current_view(db, employee),
        "pending": None if pending is None else {
            "id": pending.id,
            "submitted_at": pending.submitted_at,
            "payload": pending.submitted_payload,
        },
        "last_decision": await get_last_decision(db, employee),
    }
