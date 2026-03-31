from pydantic import BaseModel


class LocationCreate(BaseModel):
    region_id: str
    name: str
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str


class LocationUpdate(BaseModel):
    region_id: str | None = None
    name: str | None = None
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str | None = None


class LocationResponse(BaseModel):
    id: str
    company_id: str
    region_id: str
    name: str
    address: str | None
    geo_coord: dict | None
    timezone: str

    model_config = {"from_attributes": True}


class LocationBulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]
