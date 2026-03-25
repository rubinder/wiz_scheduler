import uuid

from pydantic import BaseModel


class ShiftTemplateCreate(BaseModel):
    location_id: uuid.UUID
    name: str
    weekly_schedule: list[dict]


class ShiftTemplateUpdate(BaseModel):
    name: str | None = None
    weekly_schedule: list[dict] | None = None


class ShiftTemplateResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    location_id: uuid.UUID
    name: str
    weekly_schedule: list[dict]

    model_config = {"from_attributes": True}
