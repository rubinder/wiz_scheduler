from pydantic import BaseModel, Field, field_validator

from backend.utils.tz import is_valid_timezone


def _check_timezone(value: str | None) -> str | None:
    if value is not None and not is_valid_timezone(value):
        raise ValueError(
            f"'{value}' is not a valid IANA timezone (for example America/New_York)"
        )
    return value


class LocationCreate(BaseModel):
    region_id: str
    name: str
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str
    # Minimum rest hours between shifts on different days (NYC Fair Workweek
    # clopening rule = 11). NULL/omitted = no constraint.
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)
    signal_config: dict | None = None  # Default signal weights for this location

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        return _check_timezone(v)


class LocationUpdate(BaseModel):
    region_id: str | None = None
    name: str | None = None
    address: str | None = None
    geo_coord: dict | None = None
    timezone: str | None = None
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)
    signal_config: dict | None = None  # Default signal weights {seniority, pay, overtime, affinity}

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str | None) -> str | None:
        return _check_timezone(v)


class LocationResponse(BaseModel):
    id: str
    company_id: str
    region_id: str
    name: str
    address: str | None
    geo_coord: dict | None
    timezone: str
    min_rest_hours: float | None = None
    overtime_threshold_hours: float | None = None
    overtime_premium_multiplier: float | None = None
    signal_config: dict | None = None  # Default signal weights for this location

    model_config = {"from_attributes": True}


class LocationBulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]
