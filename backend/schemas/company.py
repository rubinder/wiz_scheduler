from datetime import datetime

from pydantic import BaseModel


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    ownership_group_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CompanyUpdate(BaseModel):
    name: str | None = None
