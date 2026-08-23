"""Employee check-in: the rotating QR, the scan, and the punctuality report.

Paid-only in all three directions, including the employee-facing POST — what
gates the feature is the tenant's plan, not the caller's role.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_current_user, get_db, require_manager
from backend.models import Company, Employee, EmployeeCheckIn, Location, User
from backend.schemas.check_in import (
    CheckInQrResponse,
    CheckInReportResponse,
    CheckInReportRow,
    CheckInRequest,
    CheckInResponse,
)
from backend.services.check_in import (
    CheckInRejected,
    issue_token,
    record_check_in,
)
from backend.services.check_in_token import check_in_deep_link
from backend.services.plan import assert_paid_plan

router = APIRouter(prefix="/check-ins", tags=["check-ins"])


async def _company_slug(db: AsyncSession, company_id: str) -> str:
    """The slug is one of the four inputs to the signed QR payload."""
    return (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one()


async def _load_location(
    db: AsyncSession, company_id: str, location_id: str
) -> Location:
    location = (await db.execute(
        select(Location).where(
            Location.id == location_id, Location.company_id == company_id
        )
    )).scalar_one_or_none()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


@router.get("/qr", response_model=CheckInQrResponse)
async def get_check_in_qr(
    location_id: str = Query(...),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CheckInQrResponse:
    """The code to display right now.

    Rendered server-side so the secret and the derivation never reach a
    client. The manager page polls this and swaps the image when `counter`
    moves.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")
    location = await _load_location(db, company_id, location_id)

    slug = await _company_slug(db, company_id)
    token, counter = await issue_token(db, slug, location)

    buf = BytesIO()
    segno.make(check_in_deep_link(token, location.id), error="m").save(
        buf, kind="svg", scale=8, border=2
    )

    return CheckInQrResponse(
        counter=counter,
        svg=buf.getvalue().decode("utf-8"),
        checked_in_today=counter,
    )


@router.post("", response_model=CheckInResponse,
             status_code=status.HTTP_201_CREATED)
async def create_check_in(
    body: CheckInRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckInResponse:
    """Record a scan. Identity comes from the JWT, never from the code."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")
    location = await _load_location(db, company_id, body.location_id)

    employee = (await db.execute(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No employee record is linked to this account",
        )

    slug = await _company_slug(db, company_id)

    try:
        row = await record_check_in(
            db, company_id, str(employee.id), location, slug, body.token
        )
    except CheckInRejected as rejected:
        # 409, not 400: the request was well-formed and the caller is
        # authorised — the code just belongs to a moment that has passed.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": rejected.code, "message": rejected.message},
        )

    return CheckInResponse.model_validate(row)


@router.get("/report", response_model=CheckInReportResponse)
async def get_check_in_report(
    employee_id: str | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CheckInReportResponse:
    """Punctuality over the retained window."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "check_in")

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_CHECKINS_DAYS
    )
    query = (
        select(EmployeeCheckIn, Employee.full_name)
        .join(Employee, Employee.id == EmployeeCheckIn.employee_id)
        .where(
            EmployeeCheckIn.company_id == company_id,
            EmployeeCheckIn.checked_in_at >= cutoff,
        )
        .order_by(EmployeeCheckIn.checked_in_at)
    )
    if employee_id:
        query = query.where(EmployeeCheckIn.employee_id == employee_id)

    rows = [
        CheckInReportRow(
            id=row.id,
            employee_id=row.employee_id,
            employee_name=name,
            location_id=row.location_id,
            checked_in_at=row.checked_in_at,
            local_date=row.local_date,
            status=row.status,
            minutes_from_start=row.minutes_from_start,
        )
        for row, name in (await db.execute(query)).all()
    ]
    return CheckInReportResponse(
        rows=rows, retention_days=settings.RETENTION_CHECKINS_DAYS
    )
