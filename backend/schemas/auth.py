from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    # Exactly one of `password` or `google_id_token` is required.
    # Password path → traditional email+password registration.
    # Google path → caller passes a Google id_token; the verified Google
    # email must match `email`; an internal random password is generated
    # to satisfy the NOT NULL hashed_password column.
    password: str | None = None
    google_id_token: str | None = None
    full_name: str
    company_name: str
    privacy_accepted: bool = False
    terms_accepted: bool = False
    stripe_session_id: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str
    ownership_group_id: str | None = None


class SwitchCompanyRequest(BaseModel):
    company_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleAuthRequest(BaseModel):
    id_token: str


class GoogleLinkRequest(BaseModel):
    id_token: str
    email: str
    password: str
    ownership_group_id: str | None = None


class GoogleAuthResponse(BaseModel):
    """Response for Google auth - either a token (success) or a link_required hint."""
    access_token: str | None = None
    token_type: str = "bearer"
    link_required: bool = False
    email: str | None = None
    google_name: str | None = None


class UserResponse(BaseModel):
    id: str
    company_id: str
    email: str
    full_name: str | None
    user_role: str
    ownership_group_id: str | None = None
    is_demo: bool = False
    has_google: bool = False

    model_config = {"from_attributes": True}


class GoogleLinkCurrentRequest(BaseModel):
    """Body for POST /auth/google/link-current.

    Unlike POST /auth/google/link (which re-verifies ownership via password),
    this endpoint trusts the bearer JWT — used to add Google sign-in from
    inside a logged-in session, e.g. a Dashboard 'Link Google' card.
    """
    id_token: str


class ForgotPasswordRequest(BaseModel):
    """Body for POST /auth/forgot-password. Always returns 204 regardless of
    whether the email matches a real account, to avoid leaking which addresses
    have WizScheduler accounts."""
    email: str


class ResetPasswordRequest(BaseModel):
    """Body for POST /auth/reset-password."""
    token: str
    new_password: str
