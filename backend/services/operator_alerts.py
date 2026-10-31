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


def _send_operator_email(subject: str, body_html: str, log_label: str) -> bool:
    """Mail the operator if both OPERATOR_ALERT_EMAIL and RESEND_API_KEY are
    set. Never raises. True only when Resend accepted the send."""
    if not settings.OPERATOR_ALERT_EMAIL or not settings.RESEND_API_KEY:
        return False
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [settings.OPERATOR_ALERT_EMAIL],
            "subject": subject,
            "html": (
                '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"{body_html}</div>"
            ),
        })
        return True
    except Exception:
        logger.exception("[OPERATOR] %s email failed", log_label)
        return False


def send_payroll_archive_write_failed_alert(
    og_id: str, archive_id: str, error: str
) -> bool:
    """A paid payroll archive (#218) could not be written at day 90.

    The group was NOT deleted; the daily cron retries. Someone should look
    before the next run (an unset PAYROLL_ARCHIVE_BUCKET, an IAM change, an
    S3 outage). Always logs one [OPERATOR] line. Never raises.
    """
    logger.error(
        "[OPERATOR] payroll_archive_write_failed og=%s archive=%s error=%s",
        og_id, archive_id, error,
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive write failed for og={og_id}",
        f"<p>The paid payroll archive <strong>{html.escape(archive_id)}</strong> "
        f"for og={html.escape(og_id)} could not be written, so the group's "
        f"day-90 deletion was <strong>postponed</strong>. The cron retries "
        f"daily.</p><p>Error: <code>{html.escape(error[:500])}</code></p>",
        "payroll_archive_write_failed",
    )


def send_payroll_archive_refund_alert(
    og_id: str,
    checkout_session_id: str,
    payment_intent_id: str | None,
    reason: str,
    refunded: bool,
) -> bool:
    """A payroll-archive payment (#218) that cannot be honored was refunded
    (or the refund failed and needs doing by hand). `reason` is
    "duplicate" (the group already has an archive) or "group_deleted" (the
    payment landed after the day-90 deletion). Never raises.
    """
    logger.warning(
        "[OPERATOR] payroll_archive_refund og=%s session=%s pi=%s reason=%s refunded=%s",
        og_id, checkout_session_id, payment_intent_id, reason, refunded,
    )
    outcome = (
        "was refunded automatically"
        if refunded
        else "<strong>could NOT be refunded automatically: refund it in Stripe</strong>"
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive payment {reason} for og={og_id}",
        f"<p>A payroll-archive Checkout session "
        f"(<code>{html.escape(checkout_session_id)}</code>, payment "
        f"<code>{html.escape(payment_intent_id or '?')}</code>) for "
        f"og={html.escape(og_id)} could not be honored ({html.escape(reason)}) "
        f"and {outcome}.</p>",
        "payroll_archive_refund",
    )


def send_payroll_archive_review_alert(
    og_id: str, checkout_session_id: str, payment_intent_id: str | None, reason: str
) -> bool:
    """A paid payroll-archive session (#218) arrived that cannot be matched
    to its group (say a customer mismatch). It may be genuine, so nothing
    was recorded or refunded: a person should look. Never raises."""
    logger.warning(
        "[OPERATOR] payroll_archive_needs_review og=%s session=%s pi=%s reason=%s",
        og_id, checkout_session_id, payment_intent_id, reason,
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive payment needs review for og={og_id}",
        f"<p>A paid payroll-archive Checkout session "
        f"(<code>{html.escape(checkout_session_id)}</code>, payment "
        f"<code>{html.escape(payment_intent_id or '?')}</code>) for "
        f"og={html.escape(og_id)} could not be verified ({html.escape(reason)}). "
        f"Nothing was recorded or refunded. Check it in Stripe and decide "
        f"whether to record it or refund it.</p>",
        "payroll_archive_needs_review",
    )
