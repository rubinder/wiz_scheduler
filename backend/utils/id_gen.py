"""Short alphanumeric ID generator for all database primary keys."""

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars, 36^8 ≈ 2.8 trillion combinations

# Reserved namespace for test companies (allows for multiple test fixtures)
# No real company can have a slug starting with 'integ'
TEST_COMPANY_SLUG_PREFIX = "integ"

def generate_short_id() -> str:
    """Generate an 8-character alphanumeric ID."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def generate_company_slug() -> str:
    """Generate a unique company slug, excluding reserved test namespace.

    Regenerates if slug starts with 'integ' to ensure test companies
    cannot collide with real companies. Allows multiple test fixtures
    (integ-test-001, integ-test-002, etc.) without collision risk.
    """
    max_attempts = 1000
    for _ in range(max_attempts):
        slug = secrets.token_hex(3)  # 6-character hex string (0-9a-f)
        # Reject if starts with reserved test namespace
        if not slug.startswith(TEST_COMPANY_SLUG_PREFIX):
            return slug

    # Astronomically unlikely, but fail hard if it happens
    raise RuntimeError(
        f"Failed to generate company slug after {max_attempts} attempts. "
        f"All generated slugs started with reserved prefix '{TEST_COMPANY_SLUG_PREFIX}'."
    )
