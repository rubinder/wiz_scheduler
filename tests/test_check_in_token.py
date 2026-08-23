"""The HMAC payload behind the rotating QR code.

The four inputs are all public — slug, location, date, and a small integer
counter an attacker can enumerate. The secret is what makes the code
unforgeable, so these tests care mostly about what happens when any one input
or the key is wrong.
"""

from datetime import date

import pytest

from backend.config import settings
from backend.services.check_in_token import (
    build_check_in_token,
    check_in_deep_link,
    verify_check_in_token,
)

ARGS = ("acme-corp", "locn0001", date(2026, 8, 23), 0)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


def test_token_verifies_against_its_own_inputs():
    assert verify_check_in_token(build_check_in_token(*ARGS), *ARGS) is True


def test_token_is_stable_for_the_same_inputs():
    assert build_check_in_token(*ARGS) == build_check_in_token(*ARGS)


@pytest.mark.parametrize(
    "wrong",
    [
        ("other-corp", "locn0001", date(2026, 8, 23), 0),
        ("acme-corp", "locn0003", date(2026, 8, 23), 0),
        ("acme-corp", "locn0001", date(2026, 8, 24), 0),
        ("acme-corp", "locn0001", date(2026, 8, 23), 1),
    ],
    ids=["slug", "location", "date", "counter"],
)
def test_token_rejects_when_any_input_differs(wrong):
    assert verify_check_in_token(build_check_in_token(*ARGS), *wrong) is False


def test_counter_advance_invalidates_the_previous_token():
    """This IS the single-use property: recording a check-in raises the
    counter, so the code on screen stops verifying."""
    spent = build_check_in_token("acme-corp", "locn0001", date(2026, 8, 23), 0)
    assert verify_check_in_token(
        spent, "acme-corp", "locn0001", date(2026, 8, 23), 1
    ) is False


def test_token_rejects_under_a_different_key(monkeypatch):
    token = build_check_in_token(*ARGS)
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "a-different-secret")
    assert verify_check_in_token(token, *ARGS) is False


def test_token_is_url_safe():
    """It is carried in a query string inside the QR payload."""
    token = build_check_in_token(*ARGS)
    assert token
    assert all(c.isalnum() or c in "-_" for c in token)


def test_malformed_token_returns_false_rather_than_raising():
    for junk in ("", "!!!!", "x" * 500):
        assert verify_check_in_token(junk, *ARGS) is False


def test_missing_secret_refuses_to_build(monkeypatch):
    """A predictable key is the same as no key — never fall back to a default."""
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "")
    with pytest.raises(RuntimeError, match="CHECKIN_QR_SECRET"):
        build_check_in_token(*ARGS)


def test_deep_link_embeds_the_token_and_location():
    link = check_in_deep_link("abc123", "locn0001")
    assert link.endswith("/employee/check-in?t=abc123&l=locn0001")


# --- deployment guards ------------------------------------------------------
#
# Both of these turn a silent production misconfiguration into a loud one.
# The check-in feature has two variables that must be provisioned, and until
# this branch only one of them refused when it was wrong.


def test_the_terraform_placeholder_is_treated_as_unset(monkeypatch):
    """Terraform seeds the secret with a fixed placeholder so ECS can start.

    That value is committed to this repo, so a deployment still running on it
    has no secret at all — anyone reading the source could forge codes.
    """
    from backend.services.check_in_token import PLACEHOLDER_SECRET

    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", PLACEHOLDER_SECRET)
    with pytest.raises(RuntimeError, match="placeholder"):
        build_check_in_token(*ARGS)


def test_a_real_secret_is_accepted(monkeypatch):
    """Guard against the placeholder check rejecting everything."""
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "a-real-random-secret")
    assert build_check_in_token(*ARGS)


@pytest.mark.parametrize(
    "bad", ["", "wizscheduler.com", "/employee", "localhost:5173"]
)
def test_a_non_absolute_frontend_url_is_refused(monkeypatch, bad):
    """A relative or empty origin yields a QR that scans and goes nowhere.

    This is the failure mode that has no other symptom — the code renders,
    the camera opens it, and nothing is there.
    """
    monkeypatch.setattr(settings, "FRONTEND_URL", bad)
    with pytest.raises(RuntimeError, match="absolute URL"):
        check_in_deep_link("abc123", "locn0001")


@pytest.mark.parametrize(
    "good", ["https://wizscheduler.com", "https://wizscheduler.com/",
             "http://localhost:5173"]
)
def test_an_absolute_frontend_url_is_accepted(monkeypatch, good):
    monkeypatch.setattr(settings, "FRONTEND_URL", good)
    link = check_in_deep_link("abc123", "locn0001")
    assert link.endswith("/employee/check-in?t=abc123&l=locn0001")
    assert "//employee" not in link  # trailing slash was stripped, not doubled

