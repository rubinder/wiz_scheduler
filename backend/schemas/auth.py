from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    company_name: str


class LoginRequest(BaseModel):
    email: str
    password: str
    ownership_group_id: str | None = None


class SwitchCompanyRequest(BaseModel):
    company_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    company_id: str
    email: str
    full_name: str | None
    user_role: str
    ownership_group_id: str | None = None

    model_config = {"from_attributes": True}
