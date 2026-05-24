import logging
import secrets
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_current_user, get_db
from backend.models import Company, OwnershipGroup, User
from backend.models.consent import UserConsent
from backend.utils.privacy import mask_ip
from backend.schemas.auth import (
    ForgotPasswordRequest,
    GoogleAuthRequest,
    GoogleAuthResponse,
    GoogleLinkCurrentRequest,
    GoogleLinkRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SwitchCompanyRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Mask an email for logs: keep the first 3 chars of the local part."""
    if not email or "@" not in email:
        return (email[:3] + "***") if email else "?"
    local, _, domain = email.partition("@")
    return f"{local[:3]}***@{domain}"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(
    user_id: str,
    company_id: str,
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
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    if not body.privacy_accepted or not body.terms_accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must accept the privacy policy and terms of service to register.",
        )

    # Exactly one of password / google_id_token must be provided.
    if bool(body.password) == bool(body.google_id_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of password or google_id_token.",
        )

    # Resolve auth credentials. Google path: verify id_token, ensure the
    # verified Google email matches the registration email, and generate
    # a random internal password (hashed_password is NOT NULL in the DB
    # but never used for Google-authenticated users).
    google_sub: str | None = None
    if body.google_id_token:
        idinfo = await _verify_google_token(body.google_id_token)
        if not idinfo:
            raise HTTPException(status_code=401, detail="Invalid Google token")
        if not idinfo.get("email_verified"):
            raise HTTPException(status_code=400, detail="Google email is not verified")
        if idinfo.get("email", "").lower() != body.email.lower():
            raise HTTPException(status_code=400, detail="Google email does not match registration email")
        google_sub = idinfo["sub"]
        # Generate an unguessable random password the user never sees. They
        # authenticate via Google; if they ever want a real password they
        # can set one later via a separate flow.
        hashed_pw = _hash_password(secrets.token_urlsafe(48))
    else:
        # Hash password outside the DB transaction to reduce connection hold time
        hashed_pw = _hash_password(body.password)
    client_ip = mask_ip(request.client.host) if request.client else None

    # Verify Stripe checkout session if billing is configured
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    if settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_ID:
        if not body.stripe_session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Billing setup is required before registration.",
            )
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            session = stripe.checkout.Session.retrieve(body.stripe_session_id)
        except stripe.StripeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid billing session.",
            )
        if session.payment_status != "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Billing payment has not been completed.",
            )
        stripe_customer_id = session.customer
        stripe_subscription_id = session.subscription

    # Create ownership group (done first so we can check email uniqueness per company)
    ownership_group = OwnershipGroup(
        name=body.company_name,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
    )
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
        hashed_password=hashed_pw,
        full_name=body.full_name,
        user_role="manager",
        google_id=google_sub,
    )
    db.add(user)
    await db.flush()

    # Record GDPR consent
    for consent_type in ("privacy_policy", "terms_of_service"):
        db.add(UserConsent(
            user_id=user.id,
            company_id=company.id,
            consent_type=consent_type,
            version="1.0",
            ip_address=client_ip,
        ))

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
        logger.info(
            "login.fail email=%s reason=%s",
            _mask_email(body.email),
            "no_user_with_email" if not users else "wrong_password",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Look up ownership groups for each matched user's company
    company_ids = list({u.company_id for u in matched_users})
    comp_result = await db.execute(
        select(Company).where(Company.id.in_(company_ids))
    )
    company_map: dict[str, Company] = {c.id: c for c in comp_result.scalars().all()}

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
            logger.info(
                "login.fail email=%s reason=og_mismatch requested_og=%s",
                _mask_email(body.email),
                body.ownership_group_id,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = _create_access_token(target_user.id, target_user.company_id, target_user.user_role)
        return TokenResponse(access_token=token)

    # If multiple ownership groups, ask the user to choose
    if len(og_ids) > 1:
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id.in_(og_ids))
        )
        groups = og_result.scalars().all()
        logger.info(
            "login.multiple_ogs email=%s og_count=%d",
            _mask_email(body.email),
            len(groups),
        )
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


async def _verify_google_token(id_token: str) -> dict | None:
    """Verify a Google ID token and return the payload (email, sub, name, etc).

    Returns None if verification fails for any reason — invalid signature,
    expired token, missing google-auth dependency, network failure, etc.
    """
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        idinfo = google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )
        if idinfo["iss"] not in ("accounts.google.com", "https://accounts.google.com"):
            return None
        return idinfo
    except Exception as e:
        # Don't swallow silently — surface in CloudWatch so misconfigurations
        # (missing dep, missing GOOGLE_CLIENT_ID, clock skew, etc) are visible
        # without bringing the route down.
        import logging
        logging.getLogger("wizscheduler.google_auth").warning(
            "Google ID token verification failed: %s", e
        )
        return None


@router.post("/google", response_model=GoogleAuthResponse)
async def google_auth(
    body: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
) -> GoogleAuthResponse:
    """Authenticate via Google SSO.

    Cases:
    1. User with this google_id exists -> log in, return token
    2. User with matching email exists but no google_id -> return link_required=True
    3. No user with this email -> return error (must register first or be invited)
    """
    idinfo = await _verify_google_token(body.id_token)
    if not idinfo:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = idinfo["sub"]
    email = idinfo.get("email", "")
    name = idinfo.get("name", "")

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")

    # Case 1: User already linked with this google_id
    result = await db.execute(
        select(User).where(User.google_id == google_sub)
    )
    user = result.scalars().first()
    if user:
        token = _create_access_token(user.id, user.company_id, user.user_role)
        return GoogleAuthResponse(access_token=token)

    # Case 2: User with matching email exists but no google_id
    result = await db.execute(
        select(User).where(User.email == email)
    )
    users = result.scalars().all()
    if users:
        return GoogleAuthResponse(
            link_required=True,
            email=email,
            google_name=name,
        )

    # Case 3: No user found
    raise HTTPException(
        status_code=404,
        detail="No account found with this email. Please register first or accept an invite.",
    )


@router.post("/google/link", response_model=TokenResponse)
async def google_link(
    body: GoogleLinkRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Link a Google account to an existing password-based account.

    User must provide their password to verify ownership, then their
    google_id is saved to their user record.
    """
    idinfo = await _verify_google_token(body.id_token)
    if not idinfo:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = idinfo["sub"]
    email = idinfo.get("email", "")

    if email != body.email:
        raise HTTPException(status_code=400, detail="Email mismatch")

    # Find user by email and verify password
    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    users = result.scalars().all()
    matched = [u for u in users if _verify_password(body.password, u.hashed_password)]

    if not matched:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Handle multiple ownership groups (same as regular login)
    company_ids = list({u.company_id for u in matched})
    comp_result = await db.execute(
        select(Company).where(Company.id.in_(company_ids))
    )
    company_map = {c.id: c for c in comp_result.scalars().all()}

    og_ids = {company_map[u.company_id].ownership_group_id for u in matched if u.company_id in company_map}
    og_ids.discard(None)

    if body.ownership_group_id:
        target_user = None
        for u in matched:
            comp = company_map.get(u.company_id)
            if comp and comp.ownership_group_id == body.ownership_group_id:
                target_user = u
                break
        if not target_user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    elif len(og_ids) > 1:
        # Multiple ownership groups - need disambiguation
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id.in_(og_ids))
        )
        groups = og_result.scalars().all()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "multiple_ownership_groups",
                "groups": [{"id": str(g.id), "name": g.name} for g in groups],
            },
        )
    else:
        target_user = matched[0]

    # Link google_id to all user records with this email
    for u in matched:
        u.google_id = google_sub
    await db.commit()

    token = _create_access_token(target_user.id, target_user.company_id, target_user.user_role)
    return TokenResponse(access_token=token)


@router.post("/google/link-current", response_model=TokenResponse)
async def google_link_current(
    body: GoogleLinkCurrentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Link a Google identity to the currently-authenticated user.

    No password re-verification — JWT IS the proof of identity. Used by
    the in-app 'Link Google' card so logged-in users can add Google
    sign-in without logging out + back in via the Login flow.
    """
    idinfo = await _verify_google_token(body.id_token)
    if not idinfo:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    google_sub = idinfo["sub"]
    google_email = idinfo.get("email", "")

    if google_email.lower() != current_user.email.lower():
        raise HTTPException(
            status_code=400,
            detail="Google account email must match your account email",
        )

    # Reject if this google_id is already linked to a different person.
    existing = (await db.execute(
        select(User).where(
            User.google_id == google_sub,
            User.email != current_user.email,
        )
    )).first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This Google account is already linked to a different user",
        )

    # Mirror /auth/google/link: link to every User row with this email so the
    # multi-company-same-email case keeps working with Google sign-in.
    same_email = (await db.execute(
        select(User).where(User.email == current_user.email)
    )).scalars().all()
    for u in same_email:
        u.google_id = google_sub
    await db.commit()

    token = _create_access_token(
        current_user.id, current_user.company_id, current_user.user_role
    )
    return TokenResponse(access_token=token)


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mint a single-use password-reset token and email it to the user.

    Returns 204 regardless of whether the email matches an account, to avoid
    leaking which addresses are registered. If multiple User rows share the
    email (same-email-across-companies case), one token covers all of them —
    the reset endpoint stamps every matching row.

    Two rate-limit layers stop abuse:
      1. Per-source-IP sliding window (returns 429 above the threshold).
      2. Per-email cooldown (silently no-ops, still returns 204).
    """
    from backend.models import PasswordResetToken
    from backend.services.password_reset_email import send_password_reset_email
    from backend.services.rate_limit import forgot_password_limiter

    # Per-IP rate limit. We pull the IP straight from request.client; behind
    # the ALB this is the ALB's private IP unless we trust X-Forwarded-For.
    # Use the leftmost X-Forwarded-For entry when present (set by ALB).
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    source_ip = xff or (request.client.host if request.client else "unknown")
    if not forgot_password_limiter.check_and_record(source_ip):
        logger.info(
            "forgot_password.rate_limited ip=%s",
            source_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Too many password reset attempts. Try again in a few minutes.",
            },
        )

    users = (await db.execute(
        select(User).where(User.email == body.email)
    )).scalars().all()

    if not users:
        logger.info(
            "forgot_password.no_user email=%s",
            (body.email[:3] + "***") if body.email else "?",
        )
        return  # Still 204 — no leak.

    # Per-email cooldown: if a still-unused, still-unexpired token was
    # minted in the last RESET_COOLDOWN_MINUTES, return 204 WITHOUT minting
    # another or sending another email. Caller sees the same 204 either way
    # (preserves the no-leak property) but we don't email-bomb the user or
    # burn Resend quota on a rapid-fire attacker.
    now = datetime.now(timezone.utc)
    cooldown_start = now - timedelta(minutes=settings.FORGOT_PASSWORD_COOLDOWN_MINUTES)
    recent = (await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == users[0].id,
            PasswordResetToken.created_at > cooldown_start,
            PasswordResetToken.used_at.is_(None),
        ).limit(1)
    )).scalar_one_or_none()
    if recent is not None:
        logger.info(
            "forgot_password.cooldown_hit email=%s last_token_at=%s",
            (body.email[:3] + "***") if body.email else "?",
            recent.created_at.isoformat() if recent.created_at else "?",
        )
        return  # Still 204 — no email sent, no new token row.

    token_value = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=users[0].id,
        token=token_value,
        expires_at=now + timedelta(minutes=30),
    ))
    await db.commit()

    # Build reset URL from request origin (mirrors the invite-URL helper).
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        base = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base = str(request.base_url).rstrip("/")
    reset_url = f"{base}/reset-password?token={token_value}"

    await send_password_reset_email(body.email, reset_url)
    logger.info(
        "forgot_password.email_sent email=%s users_matched=%d",
        (body.email[:3] + "***") if body.email else "?",
        len(users),
    )


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Validate the token, update the password on every User row with the same
    email (so multi-company users get one reset), mark the token used.
    """
    from backend.models import PasswordResetToken

    if len(body.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters",
        )

    token_row = (await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == body.token)
    )).scalar_one_or_none()
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid reset link",
        )

    now = datetime.now(timezone.utc)
    expires_at = token_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(status_code=410, detail="Reset link expired")
    if token_row.used_at is not None:
        raise HTTPException(status_code=410, detail="Reset link already used")

    owner = (await db.execute(
        select(User).where(User.id == token_row.user_id)
    )).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="User no longer exists")

    new_hash = _hash_password(body.new_password)
    same_email = (await db.execute(
        select(User).where(User.email == owner.email)
    )).scalars().all()
    for u in same_email:
        u.hashed_password = new_hash

    token_row.used_at = now
    await db.commit()

    token = _create_access_token(owner.id, owner.company_id, owner.user_role)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    # Look up the ownership_group_id and slug from the user's company
    company_result = await db.execute(
        select(Company.ownership_group_id, Company.slug).where(Company.id == current_user.company_id)
    )
    row = company_result.one_or_none()
    ownership_group_id = row[0] if row else None
    company_slug = row[1] if row else None

    response = UserResponse.model_validate(current_user)
    response.ownership_group_id = ownership_group_id
    response.is_demo = company_slug == "acme-corp"
    response.has_google = current_user.google_id is not None
    return response
