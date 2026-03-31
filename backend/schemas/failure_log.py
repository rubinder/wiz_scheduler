from datetime import datetime
from typing import Any

from pydantic import BaseModel


class FailureLogResponse(BaseModel):
    id: str
    company_id: str | None
    category: str
    severity: str
    source: str
    message: str
    detail: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
