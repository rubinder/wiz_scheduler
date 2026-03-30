import uuid

from pydantic import BaseModel


class CondensedRoleCreate(BaseModel):
    name: str
    role_ids: list[uuid.UUID]


class CondensedRoleUpdate(BaseModel):
    name: str | None = None
    role_ids: list[uuid.UUID] | None = None


class CondensedRoleMappingResponse(BaseModel):
    role_id: uuid.UUID
    role_name: str

    model_config = {"from_attributes": True}


class CondensedRoleResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    roles: list[CondensedRoleMappingResponse]

    model_config = {"from_attributes": True}
