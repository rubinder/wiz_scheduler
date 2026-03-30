import uuid

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    company_name: str


class LoginRequest(BaseModel):
    email: str
    password: str
    ownership_group_id: uuid.UUID | None = None


class SwitchCompanyRequest(BaseModel):
    company_id: uuid.UUID


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    email: str
    full_name: str | None
    user_role: str
    ownership_group_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}
