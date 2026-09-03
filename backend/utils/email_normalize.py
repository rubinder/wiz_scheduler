"""Canonical form of an email address, for grouping signups that are the
same mailbox wearing different clothes.

Used as a SIGNAL, never as a constraint. One email deliberately maps to
several ownership groups in this product — /auth/login has a whole
`multiple_ownership_groups` 409 flow for exactly that — so a unique index
on the normalized form would break a supported state. The value exists so
that `me+1@`, `me+2@` and `m.e@` cluster together when we look at signup
patterns.

Plus-addressing is stripped for every domain. It is not universal (a few
hosts treat `+` as a literal), so this over-groups slightly. That is the
right direction of error for a signal: a false cluster gets eyeballed, a
missed one never gets looked at.
"""
from __future__ import annotations

# Google delivers both domains to the same mailbox, and ignores dots in the
# local part. No other major provider does the dot thing.
_GOOGLE_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_email(email: str) -> str:
    """Return the canonical form of *email*.

    Falls back to a lowercased, stripped copy for anything that isn't
    shaped like an address — the caller stores whatever comes back, and a
    malformed address is still a usable grouping key.
    """
    cleaned = (email or "").strip().lower()
    local, sep, domain = cleaned.rpartition("@")
    if not sep or not local or not domain:
        return cleaned

    local = local.partition("+")[0]

    if domain in _GOOGLE_DOMAINS:
        domain = "gmail.com"
        local = local.replace(".", "")

    if not local:
        # Something like "+tag@gmail.com" — nothing left to key on. Keep the
        # cleaned original rather than returning a bare "@domain".
        return cleaned

    return f"{local}@{domain}"
