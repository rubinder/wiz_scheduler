"""Decision email for employee preference submissions (#189).

The email goes only to the submitting employee and lists what was approved
in plain terms. It never carries a weight, an affinity level, the
employee's importance or a relationship label: coworkers appear by name
only, exactly like the in-app `last_decision.approved` summary.
"""
from __future__ import annotations

import html as _html
import logging
from typing import Any, Literal

from backend.config import settings

logger = logging.getLogger(__name__)

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _e(value: Any) -> str:
    return _html.escape(str(value))


def _day(dow: int) -> str:
    return DAY_NAMES[dow] if 0 <= dow < len(DAY_NAMES) else str(dow)


def _range(start: str, end: str) -> str:
    return f"{_e(start)}&ndash;{_e(end)}"


def _hours(value: float) -> str:
    return f"{float(value):g}"


def _section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f"<p><strong>{_e(title)}</strong></p><ul>{lis}</ul>"


def _approved_html(approved: dict[str, Any], coworker_names: dict[str, str]) -> str:
    parts: list[str] = []
    max_hours = approved.get("max_hours_per_week")
    parts.append(_section("Maximum hours per week", [
        f"Up to {_hours(max_hours)} hours" if max_hours is not None else "No maximum",
    ]))
    parts.append(_section("Preferred days", [
        _e(_day(d["day_of_week"])) for d in approved.get("day_preferences", [])
    ]))
    parts.append(_section("Preferred hours", [
        _range(r["start_time"], r["end_time"]) for r in approved.get("hour_range_preferences", [])
    ]))
    parts.append(_section("Shift caps", [
        f"At most {int(c['max_per_week'])} shifts per week between "
        f"{_range(c['start_time'], c['end_time'])}"
        for c in approved.get("caps", [])
    ]))
    parts.append(_section("Unavailable times", [
        f"{_e(_day(b['day_of_week']))} {_range(b['start_time'], b['end_time'])}"
        for b in approved.get("blackouts", [])
    ]))
    names = [
        coworker_names[a["target_employee_id"]]
        for a in approved.get("affinities", [])
        if a.get("target_employee_id") in coworker_names
    ]
    parts.append(_section("Coworkers", [_e(n) for n in names]))
    return "".join(parts)


def render_decision_email(
    *,
    employee_name: str,
    decision: Literal["approved", "denied"],
    approved: dict[str, Any] | None,
    denial_reason: str | None,
    coworker_names: dict[str, str],
    group_name: str,
) -> tuple[str, str]:
    """(subject, html). Pure; everything interpolated is html-escaped."""
    if decision == "approved":
        subject = "Your scheduling preferences were approved"
        body = (
            f"<p>Your manager at <strong>{_e(group_name)}</strong> approved your "
            f"scheduling preferences. These are now in effect:</p>"
            f"{_approved_html(approved or {}, coworker_names)}"
        )
    else:
        subject = "Your scheduling preferences were not approved"
        reason = (
            f"<p><strong>Reason:</strong> {_e(denial_reason)}</p>" if denial_reason else ""
        )
        body = (
            f"<p>Your manager at <strong>{_e(group_name)}</strong> did not approve your "
            f"scheduling preference update. Your previous preferences stay in effect.</p>"
            f"{reason}"
        )
    html = (
        f'<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
        f"<p>Hello {_e(employee_name)},</p>"
        f"{body}"
        f'<p style="color:#6b7280;font-size:12px;">You can review your preferences '
        f"in WizScheduler at any time.</p>"
        f"</div>"
    )
    return subject, html


async def send_preference_decision_email(to: str, subject: str, html: str) -> None:
    """Send via Resend. No-op without RESEND_API_KEY; never raises."""
    if not settings.RESEND_API_KEY:
        logger.info(
            "RESEND_API_KEY not set — skipping preference-decision email to %s",
            (to[:3] + "***") if to else "?",
        )
        return
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
    except Exception:
        logger.exception("Failed to send preference-decision email")
