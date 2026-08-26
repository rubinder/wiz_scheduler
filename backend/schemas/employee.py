import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class EmployeeRoleSchema(BaseModel):
    role_id: str
    skill_level: int


class EmployeeCreate(BaseModel):
    full_name: str
    email: str | None = None
    user_id: str | None = None
    location_ids: list[str] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[str] | None = None  # additional companies to assign to
    max_hours_per_week: float | None = None


class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    user_id: str | None = None
    location_ids: list[str] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[str] | None = None  # update company assignments
    # Use a sentinel to distinguish "unset" from "explicitly clear to null".
    # Pydantic v2: a field not present in the payload stays as default None;
    # routers must send this field to mutate it (nullable means "no cap").
    max_hours_per_week: float | None = None


class EmployeeRoleResponse(BaseModel):
    id: str
    role_id: str
    skill_level: int

    model_config = {"from_attributes": True}


class EmployeeAffinityCreate(BaseModel):
    employee_id: str
    target_employee_id: str
    level: float
    entry_date: date
    expiration_date: date | None = None


class EmployeeAffinityUpdate(BaseModel):
    target_employee_id: str | None = None
    level: float | None = None
    entry_date: date | None = None
    expiration_date: date | None = None


class EmployeeAffinityResponse(BaseModel):
    id: str
    employee_id: str
    target_employee_id: str
    level: float
    entry_date: date
    expiration_date: date | None

    model_config = {"from_attributes": True}


class EmployeeResponse(BaseModel):
    id: str
    company_id: str
    user_id: str | None
    full_name: str
    email: str | None
    location_ids: list[str] | None
    max_hours_per_week: float | None = None
    roles: list[EmployeeRoleResponse] = []
    company_ids: list[str] = []

    model_config = {"from_attributes": True}


class EmployeeMeRoleResponse(BaseModel):
    role_id: str
    role_name: str

    model_config = {"from_attributes": True}


class EmployeeMeLocationResponse(BaseModel):
    location_id: str
    location_name: str

    model_config = {"from_attributes": True}


class EmployeeMeResponse(BaseModel):
    id: str
    full_name: str
    email: str | None
    roles: list[EmployeeMeRoleResponse] = []
    locations: list[EmployeeMeLocationResponse] = []

    model_config = {"from_attributes": True}


class AvailabilityCreate(BaseModel):
    employee_id: str
    year: int
    month: int
    day: int
    start_time: datetime
    end_time: datetime


class AvailabilityResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    year: int
    month: int
    day: int
    start_time: datetime
    end_time: datetime

    model_config = {"from_attributes": True}


class DayBlackoutCreate(BaseModel):
    employee_id: str
    day_of_week: int          # 0 = Monday ... 6 = Sunday
    start_time: str           # "HH:MM"
    end_time: str             # "HH:MM"


class DayBlackoutResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    day_of_week: int
    start_time: str
    end_time: str

    model_config = {"from_attributes": True}


class BulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]


# ── Invite schemas ──


class InviteResponse(BaseModel):
    id: str
    employee_id: str
    email: str
    token: str
    invite_url: str
    status: str
    created_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class AcceptInviteRequest(BaseModel):
    password: str


class AcceptInviteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class InviteInfoResponse(BaseModel):
    employee_name: str
    email: str
    company_name: str


class InviteStatusResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    email: str
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None

    model_config = {"from_attributes": True}


# ── Weighted scheduling preferences ──
#
# Shared weight rule across all three preference types: 0.0-1.0 in
# increments of 0.1. 1.0 is a hard rule (see EmployeeDayPreference's
# docstring in backend/models/employee.py); anything below is a soft,
# scored preference. Defaults to 0.7 so a manager who omits it gets a
# reasonable soft preference rather than an inert 0.0 row.

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(label: str, value: str) -> str:
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise ValueError(f"{label} must be in HH:MM format")
    return value


def _validate_weight_step(v: float | None) -> float | None:
    if v is not None and round(v, 1) != v:
        raise ValueError("weight must be in increments of 0.1")
    return v


class EmployeeDayPreferenceCreate(BaseModel):
    employee_id: str
    day_of_week: int = Field(ge=0, le=6)
    weight: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float) -> float:
        if round(v, 1) != v:
            raise ValueError("weight must be in increments of 0.1")
        return v


class EmployeeDayPreferenceUpdate(BaseModel):
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float | None) -> float | None:
        return _validate_weight_step(v)


class EmployeeDayPreferenceResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    day_of_week: int
    weight: float

    model_config = {"from_attributes": True}


class EmployeeHourRangePreferenceCreate(BaseModel):
    employee_id: str
    start_time: str
    end_time: str
    weight: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("start_time", "end_time")
    @classmethod
    def valid_hhmm(cls, v: str) -> str:
        return _validate_hhmm("start_time/end_time", v)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float) -> float:
        if round(v, 1) != v:
            raise ValueError("weight must be in increments of 0.1")
        return v

    @model_validator(mode="after")
    def reject_zero_length_range(self) -> "EmployeeHourRangePreferenceCreate":
        # start_time == end_time normalises to a full 24h window in
        # overlap_fraction (the same convention overnight ranges rely on),
        # so it would match every shift at 1.0 instead of meaning "no range".
        if self.start_time == self.end_time:
            raise ValueError("start_time and end_time must not be equal")
        return self


class EmployeeHourRangePreferenceUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("start_time", "end_time")
    @classmethod
    def valid_hhmm(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_hhmm("start_time/end_time", v)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float | None) -> float | None:
        return _validate_weight_step(v)

    @model_validator(mode="after")
    def reject_zero_length_range(self) -> "EmployeeHourRangePreferenceUpdate":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time == self.end_time
        ):
            raise ValueError("start_time and end_time must not be equal")
        return self


class EmployeeHourRangePreferenceResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    start_time: str
    end_time: str
    weight: float

    model_config = {"from_attributes": True}


class EmployeeHourRangeCapCreate(BaseModel):
    employee_id: str
    start_time: str
    end_time: str
    max_per_week: int = Field(ge=0)
    weight: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("start_time", "end_time")
    @classmethod
    def valid_hhmm(cls, v: str) -> str:
        return _validate_hhmm("start_time/end_time", v)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float) -> float:
        if round(v, 1) != v:
            raise ValueError("weight must be in increments of 0.1")
        return v

    @model_validator(mode="after")
    def reject_zero_length_range(self) -> "EmployeeHourRangeCapCreate":
        if self.start_time == self.end_time:
            raise ValueError("start_time and end_time must not be equal")
        return self


class EmployeeHourRangeCapUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    max_per_week: int | None = Field(default=None, ge=0)
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("start_time", "end_time")
    @classmethod
    def valid_hhmm(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_hhmm("start_time/end_time", v)

    @field_validator("weight")
    @classmethod
    def one_decimal_place(cls, v: float | None) -> float | None:
        return _validate_weight_step(v)

    @model_validator(mode="after")
    def reject_zero_length_range(self) -> "EmployeeHourRangeCapUpdate":
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time == self.end_time
        ):
            raise ValueError("start_time and end_time must not be equal")
        return self


class EmployeeHourRangeCapResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    start_time: str
    end_time: str
    max_per_week: int
    weight: float

    model_config = {"from_attributes": True}
