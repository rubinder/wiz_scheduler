import datetime as _datetime_module
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


from typing import Literal


class GenerateRequest(BaseModel):
    week_start_date: date
    location_ids: list[str] | None = None
    template_ids: list[str] | None = None
    use_local: bool = False
    strategy: Literal["random", "rotation", "rotation_history", "max_hours"] = "random"
    strategy_param: float | None = None
    strategy_param2: float | None = None
    # Capped at 7: the per-day template fusion in
    # backend.scheduling.graph._load_initial_state keys the fused
    # weekly_schedule by day name. A window >7 days would contain duplicate
    # day-names, causing later dates to silently overwrite override slots from
    # earlier ones. Keep windows to one calendar week.
    num_days: int = Field(default=7, ge=1, le=7)


class ShiftUpdate(BaseModel):
    employee_id: str
    employee_name: str
    role_id: str
    role_name: str
    location_id: str
    date: str
    start_time: str
    end_time: str
    status: str = "ok"
    # Round-trips the asterisk data (#99). The server re-annotates on save,
    # so a stale client value is overwritten, never trusted.
    preference_violations: list[dict] = []


class UpdateShiftsRequest(BaseModel):
    shifts: list[ShiftUpdate]


class UpdateShiftsResponse(BaseModel):
    ok: bool = True
    shifts: list[ShiftUpdate]


class ShiftResponse(BaseModel):
    id: str
    shift_schedule_id: str
    location_id: str
    employee_id: str
    role_id: str
    role_name: str
    date: date
    start_time: datetime
    end_time: datetime
    # Resolved by the caller, not stored on Shift. The approved-schedule
    # viewer renders names, and export_schedules.py already builds the same
    # employee_id -> full_name map for the same reason.
    employee_name: str = ""
    # NULL on rows older than #99 -> [] so the client has one shape to read.
    preference_violations: list[dict] = []

    @field_validator("preference_violations", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or []

    model_config = {"from_attributes": True}


class ApprovedShiftEdit(BaseModel):
    """One edit to an approved schedule.

    shift_id is None for a new shift. deleted=True removes an existing one,
    in which case the other fields are ignored.
    """

    shift_id: str | None = None
    deleted: bool = False
    employee_id: str | None = None
    role_id: str | None = None
    # Qualified reference, not the bare `date` imported above: an annotated
    # assignment evaluates its RHS default before its annotation, so a field
    # literally named `date` with a default would bind the name `date` to
    # None in this class's namespace before `date | None` is evaluated,
    # raising "unsupported operand type(s) for |: 'NoneType' and 'NoneType'".
    date: _datetime_module.date | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class EditApprovedShiftsRequest(BaseModel):
    edits: list[ApprovedShiftEdit]


class EditWarning(BaseModel):
    code: str
    shift_id: str | None
    employee_id: str
    detail: str


class EditApprovedResponse(BaseModel):
    applied: int
    warnings: list[EditWarning] = []


class ShiftScheduleResponse(BaseModel):
    id: str
    company_id: str
    location_id: str
    week_start_date: date
    status: str
    strategy: str | None = None
    strategy_param: float | None = None
    strategy_param2: float | None = None
    created_at: datetime
    preference_summary: dict | None = None
    shifts: list[ShiftResponse] = []

    model_config = {"from_attributes": True}
