import uuid

from pydantic import BaseModel


class RegionCreate(BaseModel):
    name: str
    geo_bounds: dict | None = None


class RegionUpdate(BaseModel):
    name: str | None = None
    geo_bounds: dict | None = None


class RegionResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    geo_bounds: dict | None

    model_config = {"from_attributes": True}
