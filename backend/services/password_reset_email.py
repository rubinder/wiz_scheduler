"""Resend-backed email helper for the forgot-password flow."""
from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


def _mask(email: str) -> str:
    return (email[:3] + "***") if email else "?"


async def send_password_reset_email(email: str, reset_url: str) -> None:
    """Send the password-reset email. No-op when RESEND_API_KEY is unset.

    Mirrors the visual style of the existing manager-invite email
    (backend/services/manager_invite_email.py).
    """
    if not settings.RESEND_API_KEY:
        logger.info(
            "RESEND_API_KEY not set — skipping password-reset email to %s",
            _mask(email),
        )
        return

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [email],
            "subject": "Reset your WizScheduler password",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hello,</p>"
                f"<p>We received a request to reset your WizScheduler "
                f"password. Click the button below to set a new one — the "
                f"link expires in 30 minutes.</p>"
                f'<p><a href="{reset_url}" style="display:inline-block;'
                f"padding:10px 24px;background-color:#4f46e5;color:#ffffff;"
                f"text-decoration:none;border-radius:6px;font-weight:600;\">"
                f"Reset Password</a></p>"
                f"<p>If you didn't request this, you can ignore this email — "
                f"your password won't change.</p>"
                f'<p style="color:#6b7280;font-size:12px;">If the button '
                f"doesn't work, copy and paste this URL: {reset_url}</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.exception(
            "Failed to send password-reset email to %s", _mask(email)
        )
