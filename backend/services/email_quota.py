"""Per-OG daily email cap (#42).

Senders call ``check_and_log_email(db, og_id, kind)`` immediately before
``resend.Emails.send``. The function:

  * checks whether the OG is at or past OG_EMAIL_DAILY_CAP for the
    rolling 24h window;
  * if under, inserts an audit row and returns True (sender proceeds);
  * if at/over, emits a structured log and returns False (sender skips
    the network call). The audit row is NOT written for blocked sends
    so a rate-limited OG doesn't keep extending its own deny window.

Callers without an OG (e.g., the registration welcome email — the OG
was just created in the same request) should pass ``og_id=None``; the
check returns True and skips the audit row.

The 24h count uses a rolling window via sent_at >= now() - 24h, NOT a
calendar day. The cap is a cost ceiling, not a billing period.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.og_email_send_log import OgEmailSendLog

logger = logging.getLogger(__name__)


async def check_and_log_email(
    db: AsyncSession,
    ownership_group_id: str | None,
    kind: str,
) -> bool:
    """Return True if the sender may proceed; False if blocked by the cap.

    Caller passes ``ownership_group_id=None`` when the email is sent
    before any OG exists (registration). In that case the cap doesn't
    apply and nothing is logged.
    """
    if ownership_group_id is None:
        return True

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    count = (await db.execute(
        select(func.count(OgEmailSendLog.id)).where(
            OgEmailSendLog.ownership_group_id == ownership_group_id,
            OgEmailSendLog.sent_at >= cutoff,
        )
    )).scalar_one()

    if count >= settings.OG_EMAIL_DAILY_CAP:
        logger.info(
            "resend.skipped reason=og_daily_cap og=%s kind=%s count_24h=%d cap=%d",
            ownership_group_id, kind, count, settings.OG_EMAIL_DAILY_CAP,
        )
        return False

    db.add(OgEmailSendLog(
        ownership_group_id=ownership_group_id,
        kind=kind,
        sent_at=now,
    ))
    await db.flush()
    return True
