"""The origin emailed links are built against.

Every link we mail — password reset, email verification, manager and
employee invites — carries a single-use token in its URL. Whoever controls
the host in that URL receives the token when the recipient clicks. So the
host must never be attacker-controlled.

`Origin`, `Referer` and `Host` all are. An unauthenticated attacker can POST
to /auth/forgot-password or /auth/resend-verification with
`Origin: https://evil.example` and the victim receives a genuine email, from
our domain, whose link hands their reset or verification token to the
attacker. Redeeming it is account takeover. Deriving the base from
`request.base_url` is the same bug wearing the Host header instead.

The rule here: the configured FRONTEND_URL, always, unless the request's
origin exactly matches a value an operator explicitly listed. That keeps
staging and preview frontends working where they are configured, and makes
an unrecognized origin a silent fall back to the canonical host rather than
a trusted input.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import Request

from backend.config import settings

logger = logging.getLogger(__name__)


def _origin_of(url: str) -> str | None:
    """scheme://host[:port], lowercased, or None if *url* isn't absolute."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_origins() -> set[str]:
    """Origins an operator has explicitly named.

    CORS_ORIGINS defaults to "*". A wildcard is meaningful for CORS, where
    the browser still enforces same-origin on the response, and meaningless
    here — "any host may appear in an emailed link" is precisely the
    vulnerability. It is dropped rather than expanded.
    """
    allowed = set()
    for raw in [settings.FRONTEND_URL, *settings.cors_origin_list]:
        if raw == "*":
            continue
        origin = _origin_of(raw)
        if origin:
            allowed.add(origin)
    return allowed


def trusted_base_url(request: Request) -> str:
    """Return the origin to build an emailed link against.

    Falls back to FRONTEND_URL for any origin that isn't allowlisted,
    including a missing one. Never consults request.base_url: the Host
    header is caller-controlled too.
    """
    default = settings.FRONTEND_URL.rstrip("/")

    candidate = _origin_of(
        request.headers.get("origin") or request.headers.get("referer") or ""
    )
    if candidate is None:
        return default

    if candidate in _allowed_origins():
        return candidate

    # Worth a log line: either someone is probing, or a legitimate frontend
    # was deployed without being added to CORS_ORIGINS and its users are
    # quietly getting links to the canonical host.
    logger.warning(
        "email_link.untrusted_origin origin=%s falling_back_to=%s",
        candidate,
        default,
    )
    return default
