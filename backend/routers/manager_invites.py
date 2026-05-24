"""Manager-invite endpoints. Invites are scoped to an OwnershipGroup; the
accepting user picks which Company in the OG they want to join.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import Company, ManagerInvite, OwnershipGroup, User
from backend.schemas.manager_invite import (
    AcceptManagerInviteRequest,
    AcceptManagerInviteResponse,
    CompanyChoice,
    CreateManagerInviteRequest,
    ListManagerInviteRow,
    ManagerInviteInfoResponse,
    ManagerInviteResponse,
)
from backend.services.manager_invite_email import send_manager_invite_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manager-invites", tags=["manager-invites"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MANAGER_INVITE_EXPIRE_DAYS = 7


def _create_access_token(user_id: str, company_id: str, user_role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "user_role": user_role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _invite_url_for(request: Request, token: str) -> str:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        parsed = urlparse(origin)
        base = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base = str(request.base_url).rstrip("/")
    return f"{base}/accept-manager-invite?token={token}"


@router.post("/", response_model=ManagerInviteResponse)
async def create_manager_invite(
    body: CreateManagerInviteRequest,
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ManagerInviteResponse:
    company = (await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )).scalar_one()
    if not company.ownership_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your Company is not part of an Ownership Group",
        )

    og = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == company.ownership_group_id)
    )).scalar_one()

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=current_user.id,
        email=body.email,
        token=token,
        status="pending",
        expires_at=now + timedelta(days=MANAGER_INVITE_EXPIRE_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    invite_url = _invite_url_for(request, token)
    from backend.services.email_quota import check_and_log_email
    if await check_and_log_email(db, og.id, "manager_invite"):
        await db.commit()
        await send_manager_invite_email(
            email=body.email, group_name=og.name, invite_url=invite_url
        )
    else:
        await db.commit()

    return ManagerInviteResponse(
        id=invite.id,
        email=invite.email,
        token=invite.token,
        invite_url=invite_url,
        status=invite.status,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.get("/info", response_model=ManagerInviteInfoResponse)
async def get_manager_invite_info(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> ManagerInviteInfoResponse:
    invite = (await db.execute(
        select(ManagerInvite).where(ManagerInvite.token == token)
    )).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    now = datetime.now(timezone.utc)
    expired = invite.expires_at < now

    og = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.id == invite.ownership_group_id)
    )).scalar_one()

    companies = (await db.execute(
        select(Company)
        .where(Company.ownership_group_id == og.id)
        .order_by(Company.name)
    )).scalars().all()

    return ManagerInviteInfoResponse(
        email=invite.email,
        group_name=og.name,
        expired=expired,
        companies=[CompanyChoice(id=c.id, name=c.name) for c in companies],
    )


@router.post("/accept", response_model=AcceptManagerInviteResponse)
async def accept_manager_invite(
    body: AcceptManagerInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> AcceptManagerInviteResponse:
    invite = (await db.execute(
        select(ManagerInvite).where(ManagerInvite.token == body.token)
    )).scalar_one_or_none()
    if invite is None or invite.status != "pending":
        raise HTTPException(status_code=404, detail="Invite not found or already used")

    now = datetime.now(timezone.utc)
    if invite.expires_at < now:
        invite.status = "expired"
        await db.commit()
        raise HTTPException(status_code=410, detail="Invite expired")

    company = await db.get(Company, body.company_id)
    if company is None or company.ownership_group_id != invite.ownership_group_id:
        raise HTTPException(
            status_code=400,
            detail="Chosen company is not part of the invite's ownership group",
        )

    dup = (await db.execute(
        select(User).where(
            User.email == invite.email, User.company_id == company.id
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail="A user with that email already exists in the chosen company",
        )

    new_user = User(
        company_id=company.id,
        email=invite.email,
        hashed_password=pwd_context.hash(body.password),
        full_name=body.full_name,
        user_role="manager",
    )
    db.add(new_user)
    await db.flush()

    invite.status = "accepted"
    invite.accepted_at = now
    invite.accepted_company_id = company.id
    await db.commit()

    return AcceptManagerInviteResponse(
        access_token=_create_access_token(new_user.id, company.id, "manager"),
    )


@router.get("/", response_model=list[ListManagerInviteRow])
async def list_manager_invites(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ListManagerInviteRow]:
    company = (await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )).scalar_one()
    if not company.ownership_group_id:
        return []

    rows = (await db.execute(
        select(ManagerInvite)
        .where(ManagerInvite.ownership_group_id == company.ownership_group_id)
        .order_by(ManagerInvite.created_at.desc())
    )).scalars().all()
    return [
        ListManagerInviteRow(
            id=r.id, email=r.email, status=r.status,
            created_at=r.created_at, expires_at=r.expires_at,
            accepted_at=r.accepted_at, accepted_company_id=r.accepted_company_id,
        )
        for r in rows
    ]
