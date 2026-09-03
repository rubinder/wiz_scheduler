"""normalize_email is a grouping signal, not a validator."""

import pytest

from backend.utils.email_normalize import normalize_email


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Case and surrounding whitespace never survive.
        ("  Manager@Example.COM ", "manager@example.com"),
        # Plus-addressing is the cheapest way to mint "new" emails.
        ("me+1@example.com", "me@example.com"),
        ("me+1+2@example.com", "me@example.com"),
        # Gmail additionally ignores dots in the local part.
        ("m.a.n.a.g.e.r@gmail.com", "manager@gmail.com"),
        ("manager+wiz@googlemail.com", "manager@gmail.com"),
        # Dots are significant everywhere else — do NOT strip them.
        ("first.last@example.com", "first.last@example.com"),
        # Subaddressed googlemail folds onto gmail.
        ("a.b+tag@GoogleMail.com", "ab@gmail.com"),
    ],
)
def test_normalizes(raw: str, expected: str):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-an-email", "@example.com", "local@"],
)
def test_malformed_falls_back_to_cleaned_copy(raw: str):
    """Never raise. A malformed address is still a usable grouping key."""
    assert normalize_email(raw) == raw.strip().lower()


def test_plus_only_local_keeps_the_cleaned_original():
    """Stripping would leave a bare "@gmail.com", which groups unrelated
    signups together. Keep something that at least stays distinct."""
    assert normalize_email("+tag@gmail.com") == "+tag@gmail.com"


def test_distinct_mailboxes_stay_distinct():
    assert normalize_email("a@example.com") != normalize_email("b@example.com")
    # Dots matter off-gmail, so these are two different people.
    assert normalize_email("a.b@fastmail.com") != normalize_email("ab@fastmail.com")
