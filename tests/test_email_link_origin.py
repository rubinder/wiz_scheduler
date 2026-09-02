"""Emailed links must never point at a caller-controlled host.

Every one of these links carries a single-use token — password reset, email
verification, manager and employee invites. If an attacker can choose the
host, an unauthenticated POST with a forged Origin makes us mail the victim
a genuine link that hands their token to the attacker. Redeeming it is
account takeover.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import EmailVerificationToken, PasswordResetToken, User
from backend.utils.base_url import trusted_base_url

pytestmark = pytest.mark.asyncio

EVIL = "https://evil.example"


class _FakeRequest:
    """Minimal stand-in — trusted_base_url only reads two headers."""

    def __init__(self, **headers: str):
        self.headers = headers


def test_unknown_origin_falls_back_to_the_configured_frontend():
    assert trusted_base_url(_FakeRequest(origin=EVIL)) == settings.FRONTEND_URL


def test_unknown_referer_falls_back_too():
    """Referer is checked when Origin is absent, and is just as forgeable."""
    assert (
        trusted_base_url(_FakeRequest(referer=f"{EVIL}/login"))
        == settings.FRONTEND_URL
    )


def test_missing_headers_fall_back():
    assert trusted_base_url(_FakeRequest()) == settings.FRONTEND_URL


def test_the_configured_frontend_is_trusted():
    assert (
        trusted_base_url(_FakeRequest(origin=settings.FRONTEND_URL))
        == settings.FRONTEND_URL
    )


def test_an_allowlisted_origin_is_honoured(monkeypatch):
    """Staging and preview frontends keep working — once an operator has
    named them in CORS_ORIGINS."""
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://staging.example")
    assert (
        trusted_base_url(_FakeRequest(origin="https://staging.example"))
        == "https://staging.example"
    )


def test_wildcard_cors_does_not_trust_everything(monkeypatch):
    """CORS_ORIGINS="*" is the DEFAULT. A wildcard means something coherent
    for CORS and nothing coherent here — "any host may appear in an emailed
    link" is the vulnerability itself."""
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*")
    assert trusted_base_url(_FakeRequest(origin=EVIL)) == settings.FRONTEND_URL


def test_origin_matching_ignores_case_but_not_host(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://App.Example")
    assert (
        trusted_base_url(_FakeRequest(origin="https://app.example"))
        == "https://app.example"
    )
    # A lookalike host is not the allowlisted one.
    assert (
        trusted_base_url(_FakeRequest(origin="https://app.example.evil.test"))
        == settings.FRONTEND_URL
    )


def test_a_subdomain_of_an_allowed_origin_is_not_allowed(monkeypatch):
    """Exact match only. Suffix matching is how allowlists get bypassed."""
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://example.test")
    assert (
        trusted_base_url(_FakeRequest(origin="https://evil.example.test"))
        == settings.FRONTEND_URL
    )


def test_scheme_downgrade_is_not_allowed(monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://app.example")
    assert (
        trusted_base_url(_FakeRequest(origin="http://app.example"))
        == settings.FRONTEND_URL
    )


# ---------------------------------------------------------------------------
# End to end: the emailed URL itself
# ---------------------------------------------------------------------------

async def test_forged_origin_cannot_steal_a_password_reset_link(
    client: AsyncClient, db_session: AsyncSession, seed_manager, monkeypatch
):
    sent: dict = {}

    async def capture(email: str, reset_url: str) -> bool:
        sent["url"] = reset_url
        return True

    import backend.services.password_reset_email as mod

    monkeypatch.setattr(mod, "send_password_reset_email", capture)

    resp = await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "manager@test.com"},
        headers={"origin": EVIL},
    )
    assert resp.status_code == 204

    # The token was still minted and mailed — but to our own host.
    token = (await db_session.execute(select(PasswordResetToken))).scalars().first()
    assert token is not None
    assert sent["url"].startswith(settings.FRONTEND_URL)
    assert EVIL not in sent["url"]


async def test_forged_origin_cannot_steal_a_verification_link(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    sent: dict = {}

    async def capture(email: str, verify_url: str) -> bool:
        sent["url"] = verify_url
        return True

    import backend.services.email_verification_email as mod

    monkeypatch.setattr(mod, "send_email_verification_email", capture)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "victim@example.com",
            "password": "secret123",
            "full_name": "Victim",
            "company_name": "VictimCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
        headers={"origin": EVIL},
    )
    assert resp.status_code == 201

    minted = (await db_session.execute(
        select(EmailVerificationToken)
    )).scalars().all()
    assert len(minted) == 1
    assert sent["url"].startswith(settings.FRONTEND_URL)
    assert EVIL not in sent["url"]


async def test_forged_origin_cannot_steal_a_resent_verification_link(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The unauthenticated resend endpoint is the sharpest edge: an attacker
    who knows an unverified signup's address can trigger a real email to it."""
    sent: dict = {}

    async def capture(email: str, verify_url: str) -> bool:
        sent["url"] = verify_url
        return True

    import backend.services.email_verification_email as mod

    monkeypatch.setattr(mod, "send_email_verification_email", capture)

    from backend.models import Company
    from backend.models.ownership_group import OwnershipGroup
    from tests.conftest import _id

    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    db_session.add(User(
        id=_id(),
        company_id=company_id,
        email="target@example.com",
        hashed_password="x",
        full_name="Target",
        user_role="manager",
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "target@example.com"},
        headers={"origin": EVIL},
    )
    assert resp.status_code == 204
    assert sent["url"].startswith(settings.FRONTEND_URL)
    assert EVIL not in sent["url"]
