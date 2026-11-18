"""Call screening (#259 step 7): whether the AI host answers a call.

`decide` is the one evaluator. The first match wins:

1. a withheld or invalid caller ID (anything that is not E.164; Connect
   sends "anonymous", an empty string or a carrier word) -> answer
2. an active allow entry, at the location or company-wide -> answer
3. an active block entry, at the location or company-wide -> block
4. the FTC complaint list (`is_ftc_blocked`) -> block
5. otherwise answer

An allow beats every block, so a company-wide block with a location allow is
answered. An entry is active while expires_at is NULL or after `now`.

`screen` runs `decide` and records one ScreenedCall (the caller as the
HMAC voice conversations use, never the number). Screening makes no model
call and creates no conversation: a blocked call is not usage.
"""

import re
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations.screening_models import NumberRule, ScreenedCall, SpamNumber
from backend.services.rate_limit import source_key_for

ANSWER = "answer"
BLOCK = "block"

_E164 = re.compile(r"^\+[1-9][0-9]{1,14}$")


def valid_e164(number: str | None) -> bool:
    return bool(number) and _E164.fullmatch(number) is not None


async def is_ftc_blocked(db: AsyncSession, e164: str, now: datetime) -> bool:
    """At least SPAM_MIN_REPORTS complaints over the last SPAM_WINDOW_DAYS
    report dates (today included, UTC)."""
    since = now.date() - timedelta(days=settings.SPAM_WINDOW_DAYS)
    total = (await db.execute(
        select(func.coalesce(func.sum(SpamNumber.reports), 0))
        .where(SpamNumber.e164 == e164, SpamNumber.report_date > since)
    )).scalar_one()
    return int(total) >= settings.SPAM_MIN_REPORTS


async def decide(db: AsyncSession, company_id: str, location_id: str,
                 caller: str | None, now: datetime) -> tuple[str, str]:
    """(decision, reason): decision "answer" or "block"; reason one of
    allow_list, block_list, ftc, default, withheld."""
    if not valid_e164(caller):
        return ANSWER, "withheld"
    actions = set((await db.execute(
        select(NumberRule.action).where(
            NumberRule.company_id == company_id,
            NumberRule.e164 == caller,
            or_(NumberRule.location_id.is_(None), NumberRule.location_id == location_id),
            or_(NumberRule.expires_at.is_(None), NumberRule.expires_at > now),
        )
    )).scalars().all())
    if "allow" in actions:
        return ANSWER, "allow_list"
    if "block" in actions:
        return BLOCK, "block_list"
    if await is_ftc_blocked(db, caller, now):
        return BLOCK, "ftc"
    return ANSWER, "default"


async def screen(db: AsyncSession, company_id: str, location_id: str,
                 caller: str | None, now: datetime) -> tuple[str, str]:
    """`decide`, recorded as a ScreenedCall (flushed; the caller commits)."""
    decision, reason = await decide(db, company_id, location_id, caller, now)
    db.add(ScreenedCall(
        company_id=company_id, location_id=location_id,
        caller_key=source_key_for(caller) if valid_e164(caller) else None,
        decision=decision, reason=reason, created_at=now))
    await db.flush()
    return decision, reason
