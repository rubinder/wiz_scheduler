import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_current_user, get_db
from backend.models import Company, OwnershipGroup, User
from backend.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    SwitchCompanyRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(
    user_id: uuid.UUID,
    company_id: uuid.UUID,
    user_role: str,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "user_role": user_role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def _send_welcome_email(email: str, full_name: str) -> None:
    """Send welcome email via Resend if API key is configured."""
    if not settings.RESEND_API_KEY:
        return
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send(
            {
                "from": settings.FROM_EMAIL,
                "to": [email],
                "subject": "Welcome to WizScheduler!",
                "html": (
                    f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                    f"<p>Hi {full_name}, welcome to WizScheduler!</p>"
                    f"<p>Your account has been created and you're ready to start scheduling.</p>"
                    f"</div>"
                ),
            }
        )
    except Exception:
        pass  # Non-critical — don't block registration


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Create ownership group (done first so we can check email uniqueness per company)
    ownership_group = OwnershipGroup(name=body.company_name)
    db.add(ownership_group)
    await db.flush()

    slug = secrets.token_hex(3)

    company = Company(
        name=body.company_name,
        slug=slug,
        ownership_group_id=ownership_group.id,
    )
    db.add(company)
    await db.flush()

    user = User(
        company_id=company.id,
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
        user_role="manager",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await _send_welcome_email(body.email, body.full_name)

    token = _create_access_token(user.id, company.id, user.user_role)
    return TokenResponse(access_token=token)


@router.post("/login")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email))
    users = result.scalars().all()

    # Find all users whose password matches
    matched_users: list[User] = [u for u in users if _verify_password(body.password, u.hashed_password)]
    if not matched_users:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Look up ownership groups for each matched user's company
    company_ids = list({u.company_id for u in matched_users})
    comp_result = await db.execute(
        select(Company).where(Company.id.in_(company_ids))
    )
    company_map: dict[uuid.UUID, Company] = {c.id: c for c in comp_result.scalars().all()}

    # Group matched users by ownership group
    og_ids = {company_map[u.company_id].ownership_group_id for u in matched_users if u.company_id in company_map}
    # Remove None (companies without an ownership group)
    og_ids.discard(None)

    # If caller specified an ownership group, filter to it
    if body.ownership_group_id is not None:
        target_user = None
        for u in matched_users:
            comp = company_map.get(u.company_id)
            if comp and comp.ownership_group_id == body.ownership_group_id:
                target_user = u
                break
        if target_user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = _create_access_token(target_user.id, target_user.company_id, target_user.user_role)
        return TokenResponse(access_token=token)

    # If multiple ownership groups, ask the user to choose
    if len(og_ids) > 1:
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id.in_(og_ids))
        )
        groups = og_result.scalars().all()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "multiple_ownership_groups",
                "groups": [{"id": str(g.id), "name": g.name} for g in groups],
            },
        )

    # Single ownership group (or none) — pick the first matched user
    token = _create_access_token(matched_users[0].id, matched_users[0].company_id, matched_users[0].user_role)
    return TokenResponse(access_token=token)


@router.post("/switch-company", response_model=TokenResponse)
async def switch_company(
    body: SwitchCompanyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Switch the active company. The target company must be in the same ownership group."""
    # Get the current user's company to find the ownership group
    current_company_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    current_company = current_company_result.scalar_one_or_none()
    if current_company is None or current_company.ownership_group_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your company is not part of an ownership group",
        )

    # Verify the target company is in the same ownership group
    target_company_result = await db.execute(
        select(Company).where(
            Company.id == body.company_id,
            Company.ownership_group_id == current_company.ownership_group_id,
        )
    )
    target_company = target_company_result.scalar_one_or_none()
    if target_company is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target company is not in your ownership group",
        )

    # Update the user's active company
    current_user.company_id = target_company.id
    await db.commit()

    # Issue a new token with the new company_id
    token = _create_access_token(current_user.id, target_company.id, current_user.user_role)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    # Look up the ownership_group_id from the user's company
    company_result = await db.execute(
        select(Company.ownership_group_id).where(Company.id == current_user.company_id)
    )
    ownership_group_id = company_result.scalar_one_or_none()

    response = UserResponse.model_validate(current_user)
    response.ownership_group_id = ownership_group_id
    return response
