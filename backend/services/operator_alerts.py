"""Emails to the site operator, not to customers.

Nothing here counts against a tenant's email cap (services/email_quota):
the operator is the recipient, and the mail exists so they can act on
their own Anthropic Console, which has no API for buying credits.
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup

logger = logging.getLogger(__name__)


async def _total_prepaid_usd(db: AsyncSession) -> float:
    total = (await db.execute(
        select(func.coalesce(func.sum(OwnershipGroup.ai_credits_usd), 0.0))
    )).scalar_one()
    return float(total)


async def send_credit_purchase_alert(
    db: AsyncSession,
    og: OwnershipGroup,
    amount_usd: float,
    kind: str,
) -> bool:
    """Tell the operator a customer paid for credits and what to buy upstream.

    Customers pay LLM_OVERAGE_MARKUP × token cost, so amount / markup is the
    Anthropic-side spend this charge funds. The total across all groups is
    the outstanding liability to compare against the Console balance.

    Always writes one [OPERATOR] log line. Sends email only when both
    OPERATOR_ALERT_EMAIL and RESEND_API_KEY are set. Never raises: the
    charge has already happened and must not be undone by a mail failure.

    Returns True only when Resend accepted the send.
    """
    markup = settings.LLM_OVERAGE_MARKUP
    anthropic_equiv = round(amount_usd / markup, 2)
    try:
        total_prepaid = await _total_prepaid_usd(db)
    except Exception:
        logger.exception("[OPERATOR] could not sum prepaid balances")
        total_prepaid = 0.0
    total_equiv = round(total_prepaid / markup, 2)

    logger.info(
        "[OPERATOR] credit_charge og=%s kind=%s paid=%.2f anthropic_equiv=%.2f "
        "total_prepaid=%.2f total_anthropic_equiv=%.2f",
        og.id, kind, amount_usd, anthropic_equiv, total_prepaid, total_equiv,
    )

    if not settings.OPERATOR_ALERT_EMAIL or not settings.RESEND_API_KEY:
        return False

    label = "purchase" if kind == "purchase" else "auto-reload"
    subject = (
        f"[WizScheduler] AI credit {label} ${amount_usd:.2f} — "
        f"top up Anthropic by ${anthropic_equiv:.2f}"
    )
    body = (
        '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
        f"<p><strong>{html.escape(og.name)}</strong> (og={html.escape(og.id)}) "
        f"paid <strong>${amount_usd:.2f}</strong> for AI credits ({label}).</p>"
        f"<p>At {markup:.0%} markup that funds <strong>${anthropic_equiv:.2f}</strong> "
        f"of Anthropic usage. Top up the Console by at least that.</p>"
        f"<p>Outstanding prepaid across all accounts: ${total_prepaid:.2f}, "
        f"which is ${total_equiv:.2f} at Anthropic. Keep the Console balance "
        f"above that figure.</p>"
        "</div>"
    )

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [settings.OPERATOR_ALERT_EMAIL],
            "subject": subject,
            "html": body,
        })
        return True
    except Exception:
        logger.exception("[OPERATOR] credit_charge email failed og=%s kind=%s", og.id, kind)
        return False
