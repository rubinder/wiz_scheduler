"""Signup signals: recorded at registration, enforced on by nothing.

Serial free-tier signups (new email, same person, every couple of weeks)
are cheap to suspect and expensive to act on wrongly — a false positive
blocks a real customer at the door. So this module only writes. Deciding a
threshold comes after there is a distribution to look at; picking one now
would be picking it blind.

Three signals, weakest to strongest for the case we care about:
  * masked IP — /16 only (utils.privacy.mask_ip), so it corroborates and
    never identifies. Shared offices and CGNAT sit behind one /16.
  * normalized email — catches `me+1@`, `m.e@` (utils.email_normalize).
  * device id — a random id the frontend keeps in localStorage. Highest
    signal, trivially defeated by incognito. That trade is fine here.

Never trust a value in this module: `device_id` is supplied by the client
and is only ever compared to other client-supplied values.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import Request

from backend.models.ownership_group import OwnershipGroup
from backend.utils.email_normalize import normalize_email
from backend.utils.privacy import mask_ip

logger = logging.getLogger(__name__)

# Bound what we store from a caller-controlled header/field. A user agent is
# usually <200 chars and a device id is a UUID; anything longer is either a
# bug or someone testing our column widths.
_MAX_DEVICE_ID_LEN = 64


def _user_agent_hash(user_agent: str) -> str | None:
    """Hash rather than store the raw UA string.

    The raw value is a fingerprinting surface with no upside here — we only
    ever ask "same browser build?", which equality over a digest answers.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return None
    return hashlib.sha256(ua.encode("utf-8", "replace")).hexdigest()


def record_signup_signals(
    og: OwnershipGroup,
    request: Request,
    *,
    email: str,
    device_id: str | None,
) -> None:
    """Stamp *og* with the signals from this registration request.

    Mutates in place and does not commit — the caller owns the signup
    transaction. Never raises: a signal that fails to record must not fail
    a registration.
    """
    try:
        from backend.services.rate_limit import source_ip_from_request

        og.signup_ip_masked = mask_ip(source_ip_from_request(request))
        og.signup_email_normalized = normalize_email(email)
        og.signup_device_id = (
            device_id.strip()[:_MAX_DEVICE_ID_LEN] if device_id else None
        )
        og.signup_user_agent_hash = _user_agent_hash(
            request.headers.get("user-agent", "")
        )
        logger.info(
            "register.signals og=%s ip=%s email_norm=%s device=%s",
            og.id,
            og.signup_ip_masked,
            og.signup_email_normalized,
            og.signup_device_id or "-",
        )
    except Exception:
        logger.exception("register.signals_failed og=%s", getattr(og, "id", "?"))
