"""Resend-backed email helper for manager invites."""
from __future__ import annotations

import html as _html
import logging

from backend.config import settings

logger = logging.getLogger(__name__)


async def send_manager_invite_email(
    email: str,
    group_name: str,
    invite_url: str,
) -> None:
    """Send the manager-invite email. No-op when RESEND_API_KEY is unset.

    Mirrors the visual style of the existing employee-invite email
    (backend/routers/invites.py::_send_invite_email).
    """
    if not settings.RESEND_API_KEY:
        logger.info(
            "RESEND_API_KEY not set — skipping manager-invite email to %s",
            (email[:3] + "***") if email else "?",
        )
        return

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [email],
            "subject": f"You've been invited as a manager on {_html.escape(group_name)}",
            "html": (
                f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"<p>Hello,</p>"
                f"<p>You've been invited to become a manager on "
                f"<strong>{_html.escape(group_name)}</strong> in WizScheduler. "
                f"Click below to set up your account and pick which company "
                f"you'll manage:</p>"
                f'<p><a href="{invite_url}" style="display:inline-block;'
                f"padding:10px 24px;background-color:#4f46e5;color:#ffffff;"
                f"text-decoration:none;border-radius:6px;font-weight:600;\">"
                f"Set Up Your Manager Account</a></p>"
                f"<p>This link expires in 7 days.</p>"
                f'<p style="color:#6b7280;font-size:12px;">If the button doesn'
                f"'t work, copy and paste this URL: {invite_url}</p>"
                f"</div>"
            ),
        })
    except Exception:
        logger.exception("Failed to send manager-invite email")
