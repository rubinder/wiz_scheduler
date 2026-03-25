import uuid
from datetime import datetime

from pydantic import BaseModel


class OwnershipGroupCreate(BaseModel):
    name: str


class OwnershipGroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OwnershipGroupCompaniesResponse(BaseModel):
    ownership_group: OwnershipGroupResponse
    companies: list["CompanySummary"]


class CompanySummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str

    model_config = {"from_attributes": True}
