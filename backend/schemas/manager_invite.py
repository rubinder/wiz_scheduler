from datetime import datetime

from pydantic import BaseModel


class CreateManagerInviteRequest(BaseModel):
    # Note: plan specifies EmailStr, but email-validator is not a project
    # dep — mirrors existing schemas in backend/schemas/employee.py.
    email: str


class ManagerInviteResponse(BaseModel):
    id: str
    email: str
    token: str
    invite_url: str
    status: str
    created_at: datetime
    expires_at: datetime


class CompanyChoice(BaseModel):
    id: str
    name: str


class ManagerInviteInfoResponse(BaseModel):
    email: str
    group_name: str
    expired: bool
    companies: list[CompanyChoice]


class AcceptManagerInviteRequest(BaseModel):
    token: str
    company_id: str
    full_name: str
    password: str


class AcceptManagerInviteResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ListManagerInviteRow(BaseModel):
    id: str
    email: str
    status: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    accepted_company_id: str | None
