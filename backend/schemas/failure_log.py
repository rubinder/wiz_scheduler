import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class FailureLogResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    category: str
    severity: str
    source: str
    message: str
    detail: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
