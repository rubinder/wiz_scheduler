"""Request and response shapes for the payroll router.

Timestamps are serialised exactly as stored, offset intact. Nothing on this
path calls .astimezone() on a shift timestamp — the frontend reads the
wall-clock face off the string (utils/shiftTime.ts), per #92.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

# Two monthly pay periods. Bounds every query on the page and is far beyond
# any real payroll cadence.
MAX_RANGE_DAYS = 62


class PayrollRangeRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None


class PayrollDeriveResponse(BaseModel):
    created: int
    existing: int
    exception_count: int


class TimeEntryRowSchema(BaseModel):
    id: str
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None
    attested_by_name: str | None
    attested_at: datetime | None
    attestation_reason: str | None
    approved_at: datetime | None
    exported_at: datetime | None


class PayrollEntriesResponse(BaseModel):
    rows: list[TimeEntryRowSchema]
    total_entries: int
    total_paid_minutes: int
    approved_entries: int


class PayrollExceptionRowSchema(BaseModel):
    shift_id: str
    employee_id: str
    employee_name: str
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    pay_date: date
    start_time: datetime
    end_time: datetime
    paid_minutes: int


class PayrollExceptionsResponse(BaseModel):
    rows: list[PayrollExceptionRowSchema]
    total: int


class PayrollAttestRequest(BaseModel):
    shift_id: str
    reason: str | None = Field(default=None, max_length=500)


class PayrollApproveRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None
    entry_ids: list[str] | None = None


class PayrollApproveResponse(BaseModel):
    approved: int
    already_approved: int


class PayrollExportRequest(BaseModel):
    range_start: date
    range_end: date
    location_id: str | None = None
    include_exported: bool = False
