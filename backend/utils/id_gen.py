"""Short alphanumeric ID generator for all database primary keys."""

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars, 36^8 ≈ 2.8 trillion combinations

# Dedicated test company slug (cannot be generated randomly)
TEST_COMPANY_SLUG = "integ-test-001"

def generate_short_id() -> str:
    """Generate an 8-character alphanumeric ID."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def generate_company_slug() -> str:
    """Generate a unique company slug, excluding the reserved test slug.

    Regenerates if collision with TEST_COMPANY_SLUG to ensure test companies
    cannot be created with the reserved slug.
    """
    max_attempts = 100
    for _ in range(max_attempts):
        slug = secrets.token_hex(3)  # 6-character hex string
        if slug != TEST_COMPANY_SLUG:
            return slug

    # Should be astronomically unlikely, but fail hard if it happens
    raise RuntimeError(
        f"Failed to generate company slug after {max_attempts} attempts. "
        "All generated slugs collided with reserved test slug."
    )
