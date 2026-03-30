import uuid
from datetime import date, datetime

from pydantic import BaseModel


class EmployeeRoleSchema(BaseModel):
    role_id: uuid.UUID
    skill_level: int


class EmployeeCreate(BaseModel):
    full_name: str
    email: str | None = None
    user_id: uuid.UUID | None = None
    location_ids: list[uuid.UUID] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[uuid.UUID] | None = None  # additional companies to assign to


class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    user_id: uuid.UUID | None = None
    location_ids: list[uuid.UUID] | None = None
    roles: list[EmployeeRoleSchema] | None = None
    company_ids: list[uuid.UUID] | None = None  # update company assignments


class EmployeeRoleResponse(BaseModel):
    id: uuid.UUID
    role_id: uuid.UUID
    skill_level: int

    model_config = {"from_attributes": True}


class EmployeeAffinityCreate(BaseModel):
    employee_id: uuid.UUID
    target_employee_id: uuid.UUID
    level: float
    entry_date: date
    expiration_date: date | None = None


class EmployeeAffinityUpdate(BaseModel):
    target_employee_id: uuid.UUID | None = None
    level: float | None = None
    entry_date: date | None = None
    expiration_date: date | None = None


class EmployeeAffinityResponse(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
    target_employee_id: uuid.UUID
    level: float
    entry_date: date
    expiration_date: date | None

    model_config = {"from_attributes": True}


class EmployeeResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    user_id: uuid.UUID | None
    full_name: str
    email: str | None
    location_ids: list[uuid.UUID] | None
    roles: list[EmployeeRoleResponse] = []
    company_ids: list[uuid.UUID] = []

    model_config = {"from_attributes": True}


class EmployeeMeRoleResponse(BaseModel):
    role_id: uuid.UUID
    role_name: str

    model_config = {"from_attributes": True}


class EmployeeMeLocationResponse(BaseModel):
    location_id: uuid.UUID
    location_name: str

    model_config = {"from_attributes": True}


class EmployeeMeResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    roles: list[EmployeeMeRoleResponse] = []
    locations: list[EmployeeMeLocationResponse] = []

    model_config = {"from_attributes": True}


class AvailabilityCreate(BaseModel):
    employee_id: uuid.UUID
    year: int
    month: int
    day: int
    start_time: datetime
    end_time: datetime


class AvailabilityResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    employee_id: uuid.UUID
    year: int
    month: int
    day: int
    start_time: datetime
    end_time: datetime

    model_config = {"from_attributes": True}


class BulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]


# ── Invite schemas ──


class InviteResponse(BaseModel):
    id: uuid.UUID
    employee_id: uuid.UUID
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
    id: uuid.UUID
    employee_id: uuid.UUID
    employee_name: str
    email: str
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None

    model_config = {"from_attributes": True}
