from pydantic import BaseModel


class RegionCreate(BaseModel):
    name: str
    geo_bounds: dict | None = None


class RegionUpdate(BaseModel):
    name: str | None = None
    geo_bounds: dict | None = None


class RegionResponse(BaseModel):
    id: str
    company_id: str
    name: str
    geo_bounds: dict | None

    model_config = {"from_attributes": True}
