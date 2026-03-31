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

    model_config = {"from_attributes": True}
