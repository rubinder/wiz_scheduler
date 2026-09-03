"""Resend-backed email helper for the email-verification flow.

Mirrors services/password_reset_email.py — same swallow-and-report contract,
same visual style as the manager-invite and reset emails.
"""
from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


def _mask(email: str) -> str:
    return (email[:3] + "***") if email else "?"


async def send_email_verification_email(email: str, verify_url: str) -> bool:
    """Send the verification email. No-op when RESEND_API_KEY is unset.

    Returns True only when Resend accepted the send; False on a missing API
    key or a swallowed failure, so a broken email pipeline shows up in the
    logs rather than reading as success.
    """
    if not settings.RESEND_API_KEY:
        logger.info(
            "RESEND_API_KEY not set — skipping verification email to %s",
            _mask(email),
        )
        return False

    hours = settings.EMAIL_VERIFICATION_TTL_HOURS
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [email],
            "subject": "Confirm your WizScheduler email",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hello,</p>"
                f"<p>Confirm this address to start generating schedules in "
                f"WizScheduler. The link expires in {hours} hours.</p>"
                f'<p><a href="{verify_url}" style="display:inline-block;'
                f"padding:10px 24px;background-color:#4f46e5;color:#ffffff;"
                f"text-decoration:none;border-radius:6px;font-weight:600;\">"
                f"Confirm Email</a></p>"
                f"<p>If you didn't create a WizScheduler account, you can "
                f"ignore this email.</p>"
                f'<p style="color:#6b7280;font-size:12px;">If the button '
                f"doesn't work, copy and paste this URL: {verify_url}</p>"
                f"</div>"
            ),
        })
        return True
    except Exception:
        logger.exception(
            "Failed to send verification email to %s", _mask(email)
        )
        return False
