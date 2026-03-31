"""Short alphanumeric ID generator for all database primary keys."""

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars, 36^8 ≈ 2.8 trillion combinations

def generate_short_id() -> str:
    """Generate an 8-character alphanumeric ID."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))
