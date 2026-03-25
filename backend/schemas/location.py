import uuid

from pydantic import BaseModel


class LocationCreate(BaseModel):
    region_id: uuid.UUID
    name: str
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str


class LocationUpdate(BaseModel):
    region_id: uuid.UUID | None = None
    name: str | None = None
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str | None = None


class LocationResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    region_id: uuid.UUID
    name: str
    address: str | None
    geo_coord: dict | None
    timezone: str

    model_config = {"from_attributes": True}
