from datetime import date, datetime

from pydantic import BaseModel, Field


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


class UpdateShiftsRequest(BaseModel):
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

    model_config = {"from_attributes": True}


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
    shifts: list[ShiftResponse] = []

    model_config = {"from_attributes": True}
