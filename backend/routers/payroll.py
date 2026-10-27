"""Payable hours: derivation, the exception queue, attestation, approval, CSV.

Paid-only via assert_paid_plan — except the retention info and full-history
export, which a canceled group inside its grace period can also reach so it
can take its records before deletion (assert_payroll_history_access, #172).
Admin-manager-only via
require_admin (a location-scoped regular manager is refused, #149), and
every query filtered by the caller's company_id.

The router is a thin translation layer: the decision table lives in
backend/services/time_entries.py and backend/services/payroll_export.py.
"""

from dataclasses import asdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_admin
from backend.models import Company, User
from backend.schemas.payroll import (
    MAX_RANGE_DAYS,
    PayrollApproveRequest,
    PayrollApproveResponse,
    PayrollAttestRequest,
    PayrollDeriveResponse,
    PayrollEntriesResponse,
    PayrollExceptionRowSchema,
    PayrollExceptionsResponse,
    PayrollExportRequest,
    PayrollRangeRequest,
    PayrollRetentionResponse,
    TimeEntryRowSchema,
)
from backend.services.data_retention import payroll_retention_info
from backend.services.payroll_export import export_approved, export_history
from backend.services.plan import (
    assert_paid_plan,
    assert_payroll_history_access,
)
from backend.services.time_entries import (
    approve_entries,
    attest_shift,
    derive_time_entries,
    list_exceptions,
    list_time_entries,
)

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _utc_now() -> datetime:
    """The clock for the retention info; a seam so tests can freeze it."""
    return datetime.now(timezone.utc)


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
    current_user: User = Depends(require_admin),
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
    current_user: User = Depends(require_admin),
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
    current_user: User = Depends(require_admin),
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


@router.post("/attest", response_model=TimeEntryRowSchema,
             status_code=status.HTTP_201_CREATED)
async def attest(
    body: PayrollAttestRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> TimeEntryRowSchema:
    """Confirm an unscanned shift was worked. Phones die; nobody misses a
    paycheque over a QR scan."""
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")

    entry = await attest_shift(
        db, company_id, body.shift_id, current_user, body.reason
    )
    rows = await list_time_entries(
        db, company_id, entry.pay_date, entry.pay_date
    )
    row = next(r for r in rows if r.id == entry.id)
    return TimeEntryRowSchema(**asdict(row))


@router.post("/approve", response_model=PayrollApproveResponse)
async def approve(
    body: PayrollApproveRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PayrollApproveResponse:
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    result = await approve_entries(
        db, company_id, body.range_start, body.range_end, current_user,
        body.location_id, body.entry_ids,
    )
    return PayrollApproveResponse(**asdict(result))


@router.post("/export", response_class=Response)
async def export_csv(
    body: PayrollExportRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download approved hours as CSV.

    A POST rather than a GET despite being a download: it mutates exported_at
    and writes an audit row, and a browser prefetch of a GET must not silently
    consume a pay period. It also keeps the download on the authenticated
    fetch path — get_current_user reads the Authorization header only, so a
    plain <a href> download would arrive unauthenticated.
    """
    company_id = str(current_user.company_id)
    await assert_paid_plan(db, company_id, "payroll")
    _validate_range(body.range_start, body.range_end)

    # Fetched before export_approved so nothing can fail (and 500) after the
    # rows are stamped and the audit row is committed.
    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one()

    content, _export = await export_approved(
        db, company_id, current_user, body.range_start, body.range_end,
        body.location_id, body.include_exported,
    )
    filename = (
        f"payroll_{slug}_{body.range_start.isoformat()}_"
        f"{body.range_end.isoformat()}.csv"
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/retention", response_model=PayrollRetentionResponse)
async def get_retention(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PayrollRetentionResponse:
    """When the seven-year sweep next runs and how many of this company's pay
    records it will delete. warn is true within 30 days of a sweep that
    deletes any (#172)."""
    company_id = str(current_user.company_id)
    await assert_payroll_history_access(db, company_id)
    info = await payroll_retention_info(db, company_id, now=_utc_now())
    return PayrollRetentionResponse(**info)


@router.post("/export/history", response_class=StreamingResponse)
async def export_history_csv(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Download every retained pay record as CSV (#172).

    All locations, approved or not, exported or not, with an `approved`
    column. Stamps nothing; writes one PayrollExport audit row. A POST for
    the same reasons as /export: it writes an audit row, and the credential
    stays in a header.
    """
    company_id = str(current_user.company_id)
    await assert_payroll_history_access(db, company_id)
    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one()

    chunks, export = await export_history(db, company_id, current_user)
    filename = (
        f"payroll_history_{slug}_{export.range_start.isoformat()}_"
        f"{export.range_end.isoformat()}.csv"
    )
    # The file is already rendered; the stream only drains in-memory chunks,
    # so nothing here touches the session after the handler returns.
    return StreamingResponse(
        iter(chunks),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
