import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import (
    get_current_user, get_db, get_ownership_group_company_ids,
)
from backend.models import (
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeRole,
    Shift,
    User,
    UserConsent,
)
from backend.services.archive import erase_employee
from backend.services.manager_permissions import is_admin_manager, lock_company_admin_ids

router = APIRouter(prefix="/gdpr", tags=["gdpr"])


async def _linked_employees(db: AsyncSession, user: User) -> list[Employee]:
    """Every employee row linked to ``user`` within its ownership group,
    the user's own company first. Export and erasure cover all of them; a
    link from outside the group was never the user's to export or erase."""
    company_ids = await get_ownership_group_company_ids(user, db)
    rows = (await db.execute(
        select(Employee).where(
            Employee.user_id == user.id, Employee.company_id.in_(company_ids),
        )
    )).scalars().all()
    return sorted(rows, key=lambda e: (e.company_id != user.company_id, e.id))


# ---------------------------------------------------------------------------
# GET /gdpr/export  --  Data export (right to access + portability)
# ---------------------------------------------------------------------------
@router.get("/export")
async def export_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user.id

    # Per-user cooldown (#45). DB-backed for two reasons: doubles as the
    # permanent GDPR audit trail (who exported their data, when), and
    # survives uvicorn restart unlike the in-memory rate limiter.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from backend.config import settings as _settings
    from backend.models.gdpr_export_log import GdprExportLog

    now = _dt.now(_tz.utc)
    cooldown = _td(minutes=_settings.GDPR_EXPORT_COOLDOWN_MINUTES)
    recent = (await db.execute(
        select(GdprExportLog).where(
            GdprExportLog.user_id == user_id,
            GdprExportLog.exported_at > now - cooldown,
        ).order_by(GdprExportLog.exported_at.desc()).limit(1)
    )).scalar_one_or_none()
    if recent is not None:
        recent_at = recent.exported_at
        if recent_at.tzinfo is None:
            recent_at = recent_at.replace(tzinfo=_tz.utc)
        retry_after = int((recent_at + cooldown - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "export_cooldown",
                "message": (
                    f"Data export was run "
                    f"{int((now - recent_at).total_seconds() // 60)} "
                    f"minutes ago. Wait {max(retry_after // 60, 1)} more "
                    f"minute(s) and retry."
                ),
                "retry_after_seconds": max(retry_after, 1),
            },
        )

    db.add(GdprExportLog(user_id=user_id, exported_at=now))
    await db.commit()

    # User profile
    user_data = {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "user_role": current_user.user_role,
        "company_id": current_user.company_id,
    }

    # Linked employee profiles: one per company of the user's ownership
    # group they are linked in (an employee may be shared across it).
    employees = await _linked_employees(db, current_user)

    employee_data = None
    employee_profiles: list[dict] = []
    availability_data: list[dict] = []
    affinity_data: list[dict] = []
    shift_data: list[dict] = []

    for employee in employees:
        # Roles
        role_result = await db.execute(
            select(EmployeeRole).where(EmployeeRole.employee_id == employee.id)
        )
        roles = [
            {"id": r.id, "role_id": r.role_id, "skill_level": r.skill_level}
            for r in role_result.scalars().all()
        ]

        profile = {
            "id": employee.id,
            "full_name": employee.full_name,
            "email": employee.email,
            "location_ids": employee.location_ids,
            "roles": roles,
        }
        employee_profiles.append(profile)
        if employee_data is None:
            employee_data = profile

        # Availability
        avail_result = await db.execute(
            select(EmployeeAvailability).where(
                EmployeeAvailability.employee_id == employee.id
            )
        )
        availability_data += [
            {
                "id": a.id,
                "year": a.year,
                "month": a.month,
                "day": a.day,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "end_time": a.end_time.isoformat() if a.end_time else None,
            }
            for a in avail_result.scalars().all()
        ]

        # Affinities (as source or target)
        aff_result = await db.execute(
            select(EmployeeAffinity).where(
                (EmployeeAffinity.employee_id == employee.id)
                | (EmployeeAffinity.target_employee_id == employee.id)
            )
        )
        affinity_data += [
            {
                "id": a.id,
                "employee_id": a.employee_id,
                "target_employee_id": a.target_employee_id,
                "level": float(a.level),
                "entry_date": a.entry_date.isoformat(),
                "expiration_date": a.expiration_date.isoformat() if a.expiration_date else None,
            }
            for a in aff_result.scalars().all()
        ]

        # Shifts
        shift_result = await db.execute(
            select(Shift).where(Shift.employee_id == employee.id)
        )
        shift_data += [
            {
                "id": s.id,
                "shift_schedule_id": s.shift_schedule_id,
                "location_id": s.location_id,
                "role_id": s.role_id,
                "role_name": s.role_name,
                "date": s.date.isoformat(),
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
            }
            for s in shift_result.scalars().all()
        ]

    # Consents
    consent_result = await db.execute(
        select(UserConsent).where(UserConsent.user_id == user_id)
    )
    consent_data = [
        {
            "id": c.id,
            "consent_type": c.consent_type,
            "version": c.version,
            "granted_at": c.granted_at.isoformat() if c.granted_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "ip_address": c.ip_address,
        }
        for c in consent_result.scalars().all()
    ]

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": user_data,
        "employee": employee_data,
        "employee_profiles": employee_profiles,
        "availability": availability_data,
        "affinities": affinity_data,
        "shifts": shift_data,
        "consents": consent_data,
    }


# ---------------------------------------------------------------------------
# DELETE /gdpr/delete-account  --  Right to erasure
# ---------------------------------------------------------------------------
@router.delete("/delete-account")
async def delete_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user.id

    # The company keeps an admin (#149, spec Q6): while other managers
    # remain, the last admin promotes one of them first. A sole manager may
    # always erase their account, as before.
    if is_admin_manager(current_user):
        admin_ids = await lock_company_admin_ids(db, current_user.company_id)
        if admin_ids - {user_id} == set():
            other_manager = (await db.execute(
                select(User.id).where(
                    User.company_id == current_user.company_id,
                    User.user_role == "manager",
                    User.id != user_id,
                ).limit(1)
            )).scalar_one_or_none()
            if other_manager is not None:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Make another manager an admin before deleting the last admin account",
                )

    # 1. Delete consent records
    await db.execute(
        delete(UserConsent).where(UserConsent.user_id == user_id)
    )

    # 2. The employee row is archived and erased, not deleted: shifts and pay
    #    records the employer must keep still point at it (#126).
    for employee in await _linked_employees(db, current_user):
        await erase_employee(db, str(employee.company_id), employee)

    # 3. Delete the user record
    await db.execute(
        delete(User).where(User.id == user_id)
    )

    await db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# GET /gdpr/consents  --  List user consent records
# ---------------------------------------------------------------------------
@router.get("/consents")
async def list_consents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        select(UserConsent).where(UserConsent.user_id == current_user.id)
    )
    return [
        {
            "id": c.id,
            "consent_type": c.consent_type,
            "version": c.version,
            "granted_at": c.granted_at.isoformat() if c.granted_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "ip_address": c.ip_address,
        }
        for c in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# POST /gdpr/consents/revoke  --  Revoke a specific consent
# ---------------------------------------------------------------------------
@router.post("/consents/revoke")
async def revoke_consent(
    consent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke a previously granted consent."""
    result = await db.execute(
        select(UserConsent).where(
            UserConsent.id == consent_id,
            UserConsent.user_id == current_user.id,
            UserConsent.revoked_at.is_(None),
        )
    )
    consent = result.scalar_one_or_none()
    if not consent:
        raise HTTPException(status_code=404, detail="Consent not found or already revoked")
    consent.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return {"revoked": True, "consent_id": consent_id}


# ---------------------------------------------------------------------------
# Public legal documents (no auth). The text lives in backend/legal/ so a
# change is an ordinary, reviewable diff; the marketing site snapshots these
# endpoints (wiz_scheduler_marketing scripts/snapshot-legal.mjs), so they are
# the source of truth for what wizscheduler.com publishes.
# ---------------------------------------------------------------------------
_LEGAL_DIR = Path(__file__).resolve().parent.parent / "legal"


def _legal_text(name: str) -> str:
    return (_LEGAL_DIR / name).read_text(encoding="utf-8")


@router.get("/privacy-policy")
async def privacy_policy() -> dict:
    return {
        "version": "2.0",
        "effective_date": "2026-10-12",
        "content": _legal_text("privacy-policy.txt"),
    }


@router.get("/terms")
async def terms_of_service() -> dict:
    return {
        "version": "2.0",
        "effective_date": "2026-10-12",
        "content": _legal_text("terms.txt"),
    }


@router.get("/dpa")
async def data_processing_agreement() -> dict:
    return {
        "version": "2.0",
        "effective_date": "2026-10-12",
        "processors": json.loads(_legal_text("dpa-processors.json")),
        "content": _legal_text("dpa.txt"),
    }
