"""Email-ownership proof: minting, redeeming, and the generation gate.

Separate from services.plan on purpose. plan.py answers "what has this
ownership group paid for", derived entirely from ownership_groups columns;
whether a *person* proved their address is an identity question about one
User row. Folding it in would have made get_plan_state need a user, and
PlanState is consumed by the billing UI where verification means nothing.

The gate itself is called from the generate route, next to check_can_generate.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, EmailVerificationToken, User

logger = logging.getLogger(__name__)


def is_verified(user: User) -> bool:
    return user.email_verified_at is not None


def assert_email_verified(user: User) -> None:
    """Raise 403 if *user* has not proven their email address.

    403 and not 402: 402 means "pay us", and the whole point of this gate is
    that it is cleared for free by clicking a link. The frontend keys off the
    `email_not_verified` code to offer a resend button rather than the
    upgrade dialog.
    """
    if not settings.EMAIL_VERIFICATION_REQUIRED_FOR_GENERATE:
        return
    if is_verified(user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "email_not_verified",
            "message": (
                "Confirm your email address to generate schedules. "
                "We sent a link when you signed up — check your inbox, "
                "or request a new one."
            ),
            "email": user.email,
        },
    )


async def mint_token(db: AsyncSession, user: User) -> str:
    """Create and return an unused verification token for *user*.

    Does not commit — the caller owns the transaction so a signup stays one
    unit of work.
    """
    token_value = secrets.token_urlsafe(32)
    db.add(EmailVerificationToken(
        user_id=user.id,
        token=token_value,
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS),
    ))
    return token_value


async def has_fresh_token(db: AsyncSession, user: User) -> bool:
    """True if an unused, uncooled token was minted for *user* recently.

    Mirrors the forgot-password cooldown: suppresses a duplicate email
    instead of minting a second live token per click.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.EMAIL_VERIFICATION_COOLDOWN_MINUTES
    )
    row = (await db.execute(
        select(EmailVerificationToken.id).where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.created_at > cutoff,
            EmailVerificationToken.used_at.is_(None),
        ).limit(1)
    )).first()
    return row is not None


async def redeem(db: AsyncSession, token_value: str) -> User | None:
    """Consume *token_value* and mark its owner verified. Does not commit.

    Returns the User whose address was proven, or None when the token is
    unknown, already used, or expired. Verification is stamped on EVERY User
    row sharing that address, matching /auth/reset-password: the same mailbox
    across several companies proved itself once.
    """
    row = (await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token == token_value
        )
    )).scalar_one_or_none()
    if row is None or row.used_at is not None:
        return None

    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite hands back naive datetimes; Postgres does not.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        return None

    user = await db.get(User, row.user_id)
    if user is None:
        return None

    row.used_at = now
    same_email = (await db.execute(
        select(User).where(User.email == user.email)
    )).scalars().all()
    for u in same_email:
        if u.email_verified_at is None:
            u.email_verified_at = now
    return user


async def send_verification(
    db: AsyncSession, user: User, base_url: str
) -> bool:
    """Mint a token and mail it. Returns True only if Resend accepted it.

    Respects the per-OG daily email cap and the per-user cooldown; both
    return False without sending, which callers treat as a non-event — a
    verification email is never worth failing a request over.
    """
    from backend.services.email_quota import check_and_log_email
    from backend.services.email_verification_email import (
        send_email_verification_email,
    )

    if await has_fresh_token(db, user):
        logger.info("email_verification.cooldown_hit user=%s", user.id)
        return False

    og_id: str | None = None
    company_row = (await db.execute(
        select(Company.ownership_group_id).where(Company.id == user.company_id)
    )).first()
    if company_row is not None:
        og_id = company_row[0]

    if not await check_and_log_email(db, og_id, "email_verification"):
        return False

    token_value = await mint_token(db, user)
    verify_url = f"{base_url.rstrip('/')}/verify-email?token={token_value}"
    return await send_email_verification_email(user.email, verify_url)
