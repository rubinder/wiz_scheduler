from datetime import date, datetime

from pydantic import BaseModel


from typing import Literal


class GenerateRequest(BaseModel):
    week_start_date: date
    location_ids: list[str] | None = None
    template_ids: list[str] | None = None
    use_local: bool = False
    strategy: Literal["random", "rotation", "rotation_history", "max_hours"] = "random"
    strategy_param: float | None = None
    strategy_param2: float | None = None
    num_days: int = 7


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
