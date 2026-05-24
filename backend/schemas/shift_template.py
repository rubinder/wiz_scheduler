from datetime import date

from pydantic import BaseModel


class ShiftTemplateCreate(BaseModel):
    location_id: str
    name: str
    weekly_schedule: list[dict]


class ShiftTemplateUpdate(BaseModel):
    name: str | None = None
    weekly_schedule: list[dict] | None = None


class ShiftTemplateResponse(BaseModel):
    id: str
    company_id: str
    location_id: str
    name: str
    weekly_schedule: list[dict]
    specific_date: date | None = None

    model_config = {"from_attributes": True}
