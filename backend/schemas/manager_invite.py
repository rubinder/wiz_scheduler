from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ManagerType = Literal["admin", "regular"]


class CreateManagerInviteRequest(BaseModel):
    # Note: plan specifies EmailStr, but email-validator is not a project
    # dep — mirrors existing schemas in backend/schemas/employee.py.
    email: str
    # 'regular' requires a non-empty location_ids in the inviter's company
    # (#149); an admin invite ignores location_ids.
    manager_type: ManagerType = "admin"
    location_ids: list[str] | None = None


class ManagerInviteResponse(BaseModel):
    id: str
    email: str
    token: str
    invite_url: str
    status: str
    created_at: datetime
    expires_at: datetime
    manager_type: str = "admin"
    location_ids: list[str] | None = None


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
    manager_type: str = "admin"
    location_ids: list[str] | None = None
