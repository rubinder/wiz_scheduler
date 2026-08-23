"""The HMAC payload behind the rotating check-in QR code.

Issue #63 proposed deriving the code from company slug, location, date, and
the number of employees already checked in that day. Every one of those is
known or guessable to someone who is not on site — the counter is a small
integer an attacker can simply try values for — so on their own they rotate
the code without making it unforgeable.

Running them through an HMAC keyed by a server-side secret keeps the rotation
behaviour exactly as specified while making the code impossible to derive
off-site. Nothing here touches the database: the counter is supplied by the
caller, which is what keeps this module a pure, trivially testable unit.
"""

import base64
import hashlib
import hmac
from datetime import date

from backend.config import settings

# 32 base64url chars ~ 192 bits of the digest. Full SHA-256 would make a
# denser QR for no security gain at this size.
_TOKEN_CHARS = 32


# The value Terraform seeds the Secrets Manager entry with so the ECS task can
# start before anyone has set the real one. It is committed to this repo, so a
# deployment still running on it has no secret at all — every code would be
# forgeable by anyone who can read the source. Treated as unset.
PLACEHOLDER_SECRET = "CHANGE_ME_AFTER_DEPLOY"


def _secret() -> bytes:
    secret = settings.CHECKIN_QR_SECRET
    if not secret or secret == PLACEHOLDER_SECRET:
        raise RuntimeError(
            "CHECKIN_QR_SECRET is unset or still the Terraform placeholder. "
            "Check-in codes cannot be issued without a real one — falling "
            "back to a known value would make every code derivable off-site, "
            "which is the attack the HMAC exists to stop. Set it with: "
            "aws secretsmanager put-secret-value --secret-id "
            "wizscheduler/prod/CHECKIN_QR_SECRET --secret-string <random>"
        )
    return secret.encode("utf-8")


def _message(
    company_slug: str, location_id: str, local_date: date, counter: int
) -> bytes:
    return f"{company_slug}|{location_id}|{local_date.isoformat()}|{counter}".encode()


def build_check_in_token(
    company_slug: str, location_id: str, local_date: date, counter: int
) -> str:
    """The QR payload for one specific (location, day, counter) position."""
    digest = hmac.new(
        _secret(),
        _message(company_slug, location_id, local_date, counter),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:_TOKEN_CHARS]


def verify_check_in_token(
    token: str,
    company_slug: str,
    location_id: str,
    local_date: date,
    counter: int,
) -> bool:
    """True if *token* is the code for exactly this position.

    Uses compare_digest rather than ==, which returns early on the first
    differing byte and leaks how much of a guess was right.
    """
    if not token:
        return False
    try:
        expected = build_check_in_token(
            company_slug, location_id, local_date, counter
        )
    except RuntimeError:
        raise
    return hmac.compare_digest(token, expected)


def check_in_deep_link(token: str, location_id: str) -> str:
    """The URL encoded into the QR image.

    A link rather than a bare code so an ordinary phone camera can open it —
    no in-app scanner, no camera permission, no QR *reader* dependency.

    Carries the location because the page has to say which location it is
    checking in to; it carries no identity, which comes from the bearer token
    the app already holds.

    Refuses a FRONTEND_URL that is not an absolute http(s) origin. Without
    this the failure is SILENT and expensive: the config default is
    http://localhost:5173, so an unprovisioned deployment would print
    perfectly valid QR codes that scan cleanly and open nothing, and the only
    symptom is employees saying check-in "does not work". A deployment that
    cannot build a reachable link should refuse to issue a code, the same way
    a missing secret does.
    """
    base = settings.FRONTEND_URL.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise RuntimeError(
            f"FRONTEND_URL is not an absolute URL ({settings.FRONTEND_URL!r}). "
            "It is the origin encoded into every check-in QR code, so a "
            "relative or empty value produces codes that scan but lead "
            "nowhere. Set it to the deployed frontend origin, e.g. "
            "https://wizscheduler.com"
        )
    return f"{base}/employee/check-in?t={token}&l={location_id}"
