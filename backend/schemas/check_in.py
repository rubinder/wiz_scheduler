from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CheckInQrResponse(BaseModel):
    """What the manager's screen renders. Deliberately no token field — the
    payload is inside the SVG, and there is no reason for it to be readable
    as text in the page."""

    counter: int
    svg: str
    checked_in_today: int


class CheckInRequest(BaseModel):
    token: str
    location_id: str


class CheckInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    checked_in_at: datetime
    local_date: date
    minutes_from_start: int | None
    shift_id: str | None


class CheckInReportRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    employee_name: str
    location_id: str
    checked_in_at: datetime
    local_date: date
    status: str
    minutes_from_start: int | None


class CheckInReportResponse(BaseModel):
    rows: list[CheckInReportRow]
    retention_days: int
