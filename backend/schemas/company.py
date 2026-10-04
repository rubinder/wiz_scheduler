from datetime import datetime

from pydantic import BaseModel, Field


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    ownership_group_id: str | None = None
    created_at: datetime
    overtime_threshold_hours: float | None = None
    overtime_premium_multiplier: float | None = None
    signal_config: dict | None = None  # Default signal weights for this company

    model_config = {"from_attributes": True}


class CompanyUpdate(BaseModel):
    name: str | None = None
    overtime_threshold_hours: float | None = Field(default=None, gt=0)
    overtime_premium_multiplier: float | None = Field(default=None, ge=1)
    signal_config: dict | None = None  # Default signal weights {seniority, pay, overtime, affinity}
