"""Payable hours: derivation, the exception queue, attestation, approval, CSV.

Paid-only in every direction via assert_paid_plan, manager-only via
require_manager, and every query filtered by the caller's company_id.

The router is a thin translation layer: the decision table lives in
backend/services/time_entries.py and backend/services/payroll_export.py.
"""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import User
from backend.schemas.payroll import (
    MAX_RANGE_DAYS,
    PayrollDeriveResponse,
    PayrollEntriesResponse,
    PayrollExceptionRowSchema,
    PayrollExceptionsResponse,
    PayrollRangeRequest,
    TimeEntryRowSchema,
)
from backend.services.plan import assert_paid_plan
from backend.services.time_entries import (
    derive_time_entries,
    list_exceptions,
    list_time_entries,
)

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _validate_range(range_start: date, range_end: date) -> None:
    """Inclusive, forwards, and at most MAX_RANGE_DAYS long.

    Checked in the router rather than in a pydantic validator so the GET query
    parameters and the POST bodies are held to exactly the same rule, and so
    the refusal is a 400 with a code rather than a 422 with a pydantic blob.
    """
    if (
        range_end < range_start
        or (range_end - range_start).days + 1 > MAX_RANGE_DAYS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_range",
                "message": (
                    f"Pick a range that runs forwards and covers at most "
                    f"{MAX_RANGE_DAYS} days."
                ),
            },
        )


@router.post("/entries/derive", response_model=PayrollDeriveResponse)
async def derive_entries(
    body: PayrollRangeRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollDeriveResponse:
    """Create the payable rows for a range. Idempotent — the page calls it on
    every load. A POST rather than a GET-with-side-effects because it writes.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    result = await derive_time_entries(
        db, company_id, body.range_start, body.range_end, body.location_id
    )
    return PayrollDeriveResponse(**asdict(result))


@router.get("/entries", response_model=PayrollEntriesResponse)
async def get_entries(
    range_start: date = Query(...),
    range_end: date = Query(...),
    location_id: str | None = Query(None),
    approved: bool | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollEntriesResponse:
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(range_start, range_end)

    rows = await list_time_entries(
        db, company_id, range_start, range_end, location_id, approved
    )
    return PayrollEntriesResponse(
        rows=[TimeEntryRowSchema(**asdict(r)) for r in rows],
        total_entries=len(rows),
        total_paid_minutes=sum(r.paid_minutes for r in rows),
        approved_entries=sum(1 for r in rows if r.approved_at is not None),
    )


@router.get("/exceptions", response_model=PayrollExceptionsResponse)
async def get_exceptions(
    range_start: date = Query(...),
    range_end: date = Query(...),
    location_id: str | None = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> PayrollExceptionsResponse:
    """Approved shifts nobody scanned for. Phones die; nobody misses a
    paycheque over a QR scan."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(range_start, range_end)

    rows = await list_exceptions(
        db, company_id, range_start, range_end, location_id
    )
    return PayrollExceptionsResponse(
        rows=[PayrollExceptionRowSchema(**asdict(r)) for r in rows],
        total=len(rows),
    )
