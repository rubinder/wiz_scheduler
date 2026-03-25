import uuid
from datetime import date, datetime

from pydantic import BaseModel


class GenerateRequest(BaseModel):
    week_start_date: date
    location_ids: list[uuid.UUID] | None = None
    template_ids: list[uuid.UUID] | None = None


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
    id: uuid.UUID
    shift_schedule_id: uuid.UUID
    location_id: uuid.UUID
    employee_id: uuid.UUID
    role_id: uuid.UUID
    role_name: str
    date: date
    start_time: datetime
    end_time: datetime

    model_config = {"from_attributes": True}


class ShiftScheduleResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    location_id: uuid.UUID
    week_start_date: date
    status: str
    created_at: datetime
    shifts: list[ShiftResponse] = []

    model_config = {"from_attributes": True}
