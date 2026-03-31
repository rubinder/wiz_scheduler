from pydantic import BaseModel


class RoleCreate(BaseModel):
    name: str
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class RoleResponse(BaseModel):
    id: str
    company_id: str
    name: str
    description: str | None
    external_id: str | None = None

    model_config = {"from_attributes": True}


class RoleBulkUploadResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]
