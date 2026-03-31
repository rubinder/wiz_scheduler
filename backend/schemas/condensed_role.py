from pydantic import BaseModel


class CondensedRoleCreate(BaseModel):
    name: str
    role_ids: list[str]


class CondensedRoleUpdate(BaseModel):
    name: str | None = None
    role_ids: list[str] | None = None


class CondensedRoleMappingResponse(BaseModel):
    role_id: str
    role_name: str

    model_config = {"from_attributes": True}


class CondensedRoleResponse(BaseModel):
    id: str
    company_id: str
    name: str
    roles: list[CondensedRoleMappingResponse]

    model_config = {"from_attributes": True}
