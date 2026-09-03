import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.utils.base_url import trusted_base_url
from backend.utils.email_normalize import normalize_email
from backend.models import Company, Employee, EmployeeInvite, OwnershipGroup, User
from backend.models.consent import UserConsent
from backend.utils.privacy import mask_ip
from backend.schemas.employee import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InviteInfoResponse,
    InviteResponse,
    InviteStatusResponse,
)

router = APIRouter(tags=["invites"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

INVITE_EXPIRE_DAYS = 7


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


async def _send_invite_email(email: str, employee_name: str, invite_url: str, group_name: str) -> None:
    """Send invite email via Resend if API key is configured."""
    if not settings.RESEND_API_KEY:
        logger.info("RESEND_API_KEY not set — skipping invite email to %s", email[:3] + "***")
        return
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        logger.info("Sending invite email to %s (from %s)", email[:3] + "***", settings.FROM_EMAIL)
        result = resend.Emails.send(
            {
                "from": settings.FROM_EMAIL,
                "to": [email],
                "subject": f"You're invited to {group_name} on WizScheduler!",
                "html": (
                    f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                    f"<p>Hi {employee_name},</p>"
                    f"<p>You've been invited to join <strong>{group_name}</strong> on WizScheduler. "
                    f"Click the link below to set up your account and manage your availability:</p>"
                    f'<p><a href="{invite_url}" style="display:inline-block;padding:10px 24px;'
                    f'background-color:#4f46e5;color:#ffffff;text-decoration:none;border-radius:6px;'
                    f'font-weight:600;">Set Up Your Account</a></p>'
                    f"<p>This link expires in {INVITE_EXPIRE_DAYS} days.</p>"
                    f'<p style="color:#6b7280;font-size:12px;">If the button doesn\'t work, '
                    f"copy and paste this URL: {invite_url}</p>"
                    f"</div>"
                ),
            }
        )
        logger.info("Invite email sent to %s — result: %s", email[:3] + "***", result)
    except Exception:
        logger.exception("Failed to send invite email to %s", email[:3] + "***")


@router.post("/employees/{employee_id}/invite", response_model=InviteResponse)
async def create_invite(
    employee_id: str,
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    """Generate an invite link for an employee who doesn't have a login yet."""
    # Verify employee exists and belongs to this company
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.company_id == current_user.company_id,
        )
    )
    employee = result.scalar_one_or_none()
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if employee.user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Employee already has a login account",
        )

    if not employee.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Employee must have an email address before sending an invite",
        )

    # Check for existing pending invite — revoke it
    existing = await db.execute(
        select(EmployeeInvite).where(
            EmployeeInvite.employee_id == employee_id,
            EmployeeInvite.status == "pending",
        )
    )
    for old_invite in existing.scalars().all():
        old_invite.status = "revoked"

    # Create new invite
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    invite = EmployeeInvite(
        company_id=current_user.company_id,
        employee_id=employee_id,
        token=token,
        email=employee.email,
        status="pending",
        expires_at=now + timedelta(days=INVITE_EXPIRE_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    # The token in this URL creates an employee account. See utils.base_url
    # for why the host is not taken from request headers.
    invite_url = f"{trusted_base_url(request)}/accept-invite?token={token}"

    # Fetch ownership group name for the email
    comp_result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = comp_result.scalar_one()
    group_name = company.name  # fallback to company name
    if company.ownership_group_id:
        og_result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.id == company.ownership_group_id)
        )
        og = og_result.scalar_one_or_none()
        if og:
            group_name = og.name

    from backend.services.email_quota import check_and_log_email
    if await check_and_log_email(
        db, company.ownership_group_id, "employee_invite",
    ):
        await db.commit()
        await _send_invite_email(employee.email, employee.full_name, invite_url, group_name)
    else:
        await db.commit()

    return InviteResponse(
        id=invite.id,
        employee_id=invite.employee_id,
        email=invite.email,
        token=invite.token,
        invite_url=invite_url,
        status=invite.status,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/invites/{token}", response_model=InviteInfoResponse)
async def get_invite_info(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> InviteInfoResponse:
    """Public endpoint — returns employee name and email for the accept-invite page."""
    result = await db.execute(
        select(EmployeeInvite).where(EmployeeInvite.token == token)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite link")

    if invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already been used or expired")

    if invite.expires_at < datetime.now(timezone.utc):
        invite.status = "expired"
        await db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired")

    # Get employee and company names
    emp_result = await db.execute(select(Employee).where(Employee.id == invite.employee_id))
    employee = emp_result.scalar_one()

    comp_result = await db.execute(select(Company).where(Company.id == invite.company_id))
    company = comp_result.scalar_one()

    return InviteInfoResponse(
        employee_name=employee.full_name,
        email=invite.email,
        company_name=company.name,
    )


@router.post("/invites/{token}/accept", response_model=AcceptInviteResponse)
async def accept_invite(
    token: str,
    body: AcceptInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AcceptInviteResponse:
    """Public endpoint — employee sets password, User account is created."""
    result = await db.execute(
        select(EmployeeInvite).where(EmployeeInvite.token == token)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite link")

    if invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already been used or expired")

    if invite.expires_at < datetime.now(timezone.utc):
        invite.status = "expired"
        await db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired")

    # Check email not already registered for this company
    existing_user = await db.execute(
        select(User).where(User.email == invite.email, User.company_id == invite.company_id)
    )
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists for this company. Please log in instead.",
        )

    # Get the employee
    emp_result = await db.execute(select(Employee).where(Employee.id == invite.employee_id))
    employee = emp_result.scalar_one()

    # Create User account
    user = User(
        company_id=invite.company_id,
        email=invite.email,
        hashed_password=pwd_context.hash(body.password),
        full_name=employee.full_name,
        user_role="employee",
        email_normalized=normalize_email(invite.email),
        # Proven by opening the emailed invite link (see manager_invites).
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()

    # Record GDPR consent for the new employee user
    client_ip = mask_ip(request.client.host) if request.client else None
    for consent_type in ("privacy_policy", "terms_of_service"):
        db.add(UserConsent(
            user_id=user.id,
            company_id=invite.company_id,
            consent_type=consent_type,
            version="1.0",
            ip_address=client_ip,
        ))

    # Link employee to user
    employee.user_id = user.id

    # Mark invite as accepted
    invite.status = "accepted"
    invite.accepted_at = datetime.now(timezone.utc)

    await db.commit()

    # Return JWT so the employee is logged in immediately
    access_token = _create_access_token(user.id, invite.company_id, "employee")
    return AcceptInviteResponse(access_token=access_token)


@router.get("/employees/invites", response_model=list[InviteStatusResponse])
async def list_invites(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[InviteStatusResponse]:
    """List all invites for the manager's company."""
    result = await db.execute(
        select(EmployeeInvite, Employee.full_name)
        .join(Employee, EmployeeInvite.employee_id == Employee.id)
        .where(EmployeeInvite.company_id == current_user.company_id)
        .order_by(EmployeeInvite.created_at.desc())
    )
    rows = result.all()
    return [
        InviteStatusResponse(
            id=invite.id,
            employee_id=invite.employee_id,
            employee_name=emp_name,
            email=invite.email,
            status=invite.status,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            accepted_at=invite.accepted_at,
        )
        for invite, emp_name in rows
    ]
