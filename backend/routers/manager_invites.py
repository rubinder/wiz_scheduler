"""Manager-invite endpoints. Invites are scoped to an OwnershipGroup; the
accepting user picks which Company in the OG they want to join.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_admin
from backend.services.passwords import hash_password
from backend.utils.base_url import trusted_base_url
from backend.utils.email_normalize import normalize_email
from backend.models import Company, Location, ManagerInvite, ManagerLocation, OwnershipGroup, User
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
from backend.services.manager_permissions import validate_assignable_locations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manager-invites", tags=["manager-invites"])

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
    # The token in this URL creates a manager account. See
    # utils.base_url for why the host is not taken from request headers.
    return f"{trusted_base_url(request)}/accept-manager-invite?token={token}"


@router.post("/", response_model=ManagerInviteResponse)
async def create_manager_invite(
    body: CreateManagerInviteRequest,
    request: Request,
    current_user: User = Depends(require_admin),
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

    # A regular invite names its initial locations (spec Q5): active ones of
    # the inviter's company, at least one. An admin invite has none.
    location_ids: list[str] | None = None
    if body.manager_type == "regular":
        if not body.location_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A regular manager invite needs at least one location",
            )
        try:
            location_ids = await validate_assignable_locations(
                db, current_user.company_id, body.location_ids
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=current_user.id,
        email=body.email,
        token=token,
        status="pending",
        expires_at=now + timedelta(days=MANAGER_INVITE_EXPIRE_DAYS),
        manager_type=body.manager_type,
        location_ids=location_ids,
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
        manager_type=invite.manager_type,
        location_ids=invite.location_ids,
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

    if invite.manager_type == "regular" and invite.location_ids:
        # A location-limited invite belongs to the company its locations
        # are in; joining a sibling company would leave the manager with
        # none of them.
        invite_companies = set((await db.execute(
            select(Location.company_id).where(Location.id.in_(list(invite.location_ids)))
        )).scalars().all())
        if invite_companies and company.id not in invite_companies:
            raise HTTPException(
                status_code=400,
                detail="This invite is for locations in a different company",
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
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        user_role="manager",
        # The invite chose the type (spec Q5); invites made before 0051
        # are admin invites, like every manager 0042 backfilled.
        manager_type="regular" if invite.manager_type == "regular" else "admin",
        email_normalized=normalize_email(invite.email),
        # Accepting the invite required opening a link we mailed to this
        # address, which is the same proof /auth/verify-email collects.
        # Asking again would be a second confirmation email for one mailbox.
        email_verified_at=now,
    )
    db.add(new_user)
    await db.flush()

    if new_user.manager_type == "regular" and invite.location_ids:
        # The invite's locations that still exist in the chosen company
        # (archived ones included: they stay assigned, spec Q7). If none
        # do, the manager starts with no locations and sees nothing until
        # an admin assigns some.
        kept = (await db.execute(
            select(Location.id).where(
                Location.id.in_(list(invite.location_ids)),
                Location.company_id == company.id,
            )
        )).scalars().all()
        for lid in dict.fromkeys(i for i in invite.location_ids if i in set(kept)):
            db.add(ManagerLocation(manager_id=new_user.id, location_id=lid))
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
    current_user: User = Depends(require_admin),
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
            manager_type=r.manager_type, location_ids=r.location_ids,
        )
        for r in rows
    ]
