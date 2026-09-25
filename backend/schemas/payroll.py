"""Request and response shapes for the payroll router.

Shift timestamps (start_time/end_time) and checked_in_at are true instants,
which Postgres hands back normalised to UTC. They are serialised in the
LOCATION's zone with the offset intact — the same instant, wearing its local
wall-clock face — exactly as `_shift_to_response`
(backend/routers/schedules.py) does for the schedule endpoints. That is what
the frontend needs, because utils/shiftTime.ts reads the face off the string
rather than converting it (per #92); handing it the raw UTC instant would
show a 09:00 shift as 13:00.

This is not the ".astimezone() on availability" mistake #61/#85 forbid:
availability is a wall-clock value falsely tagged UTC, so converting it moves
the face. These columns are genuine instants, so converting recovers it.
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
