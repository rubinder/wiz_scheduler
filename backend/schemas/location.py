from pydantic import BaseModel


class LocationCreate(BaseModel):
    region_id: str
    name: str
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str
    # Minimum rest hours between shifts on different days (NYC Fair Workweek
    # clopening rule = 11). NULL/omitted = no constraint.
    min_rest_hours: float | None = None


class LocationUpdate(BaseModel):
    region_id: str | None = None
    name: str | None = None
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str | None = None
    min_rest_hours: float | None = None


class LocationResponse(BaseModel):
    id: str
    company_id: str
    region_id: str
    name: str
    address: str | None
    geo_coord: dict | None
    timezone: str
    min_rest_hours: float | None = None

    model_config = {"from_attributes": True}


class LocationBulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]
