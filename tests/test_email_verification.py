"""Email verification: minting, redeeming, and the generation gate.

The gate's shape is the point: an unverified address blocks
POST /schedules/generate and nothing else.
"""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, EmailVerificationToken, User
from backend.models.ownership_group import OwnershipGroup
from backend.services.email_verification import (
    assert_email_verified,
    has_fresh_token,
    mint_token,
    redeem,
)
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def unverified(db_session: AsyncSession) -> User:
    """A fresh password signup that hasn't clicked the link yet."""
    og_id, company_id, user_id = _id(), _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.flush()
    user = User(
        id=user_id,
        company_id=company_id,
        email="new@example.com",
        hashed_password="x",
        full_name="New Manager",
        user_role="manager",
        email_normalized="new@example.com",
    )
    db_session.add(user)
    await db_session.commit()
    return user


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

async def test_unverified_user_is_blocked(unverified: User):
    with pytest.raises(HTTPException) as exc:
        assert_email_verified(unverified)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "email_not_verified"


async def test_verified_user_passes(unverified: User):
    unverified.email_verified_at = datetime.now(timezone.utc)
    assert_email_verified(unverified)  # does not raise


async def test_gate_is_403_not_402(unverified: User):
    """402 means "pay us". This gate is cleared for free by clicking a link,
    and the frontend switches on the difference to pick which prompt to show.
    """
    with pytest.raises(HTTPException) as exc:
        assert_email_verified(unverified)
    assert exc.value.status_code != 402


async def test_gate_can_be_switched_off(unverified: User, monkeypatch):
    """Escape hatch for a broken Resend pipeline — otherwise every new tenant
    is locked out of the core action until a deploy."""
    monkeypatch.setattr(
        settings, "EMAIL_VERIFICATION_REQUIRED_FOR_GENERATE", False
    )
    assert_email_verified(unverified)  # does not raise


# ---------------------------------------------------------------------------
# Redeeming
# ---------------------------------------------------------------------------

async def test_redeem_marks_verified_and_consumes_token(
    db_session: AsyncSession, unverified: User
):
    token = await mint_token(db_session, unverified)
    await db_session.commit()

    user = await redeem(db_session, token)
    await db_session.commit()

    assert user is not None and user.id == unverified.id
    assert user.email_verified_at is not None

    row = (await db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token == token
        )
    )).scalar_one()
    assert row.used_at is not None


async def test_redeem_is_single_use(db_session: AsyncSession, unverified: User):
    token = await mint_token(db_session, unverified)
    await db_session.commit()

    assert await redeem(db_session, token) is not None
    await db_session.commit()
    assert await redeem(db_session, token) is None


async def test_redeem_rejects_expired_token(
    db_session: AsyncSession, unverified: User
):
    db_session.add(EmailVerificationToken(
        id=_id(),
        user_id=unverified.id,
        token="expired-token-value",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    await db_session.commit()

    assert await redeem(db_session, "expired-token-value") is None
    await db_session.refresh(unverified)
    assert unverified.email_verified_at is None


async def test_redeem_rejects_unknown_token(db_session: AsyncSession):
    assert await redeem(db_session, "never-minted") is None


async def test_redeem_verifies_every_row_sharing_the_address(
    db_session: AsyncSession, unverified: User
):
    """One mailbox can own several companies (see /auth/login's
    multiple_ownership_groups flow). Proving it once proves it everywhere,
    matching how /auth/reset-password stamps the password."""
    other_company_id = _id()
    db_session.add(Company(id=other_company_id, name="C2", slug=_id()))
    await db_session.flush()
    twin = User(
        id=_id(),
        company_id=other_company_id,
        email=unverified.email,
        hashed_password="x",
        full_name="Same Person",
        user_role="manager",
    )
    db_session.add(twin)
    token = await mint_token(db_session, unverified)
    await db_session.commit()

    await redeem(db_session, token)
    await db_session.commit()

    await db_session.refresh(twin)
    assert twin.email_verified_at is not None


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

async def test_fresh_token_suppresses_a_second_send(
    db_session: AsyncSession, unverified: User
):
    assert await has_fresh_token(db_session, unverified) is False
    await mint_token(db_session, unverified)
    await db_session.commit()
    assert await has_fresh_token(db_session, unverified) is True


async def test_used_token_does_not_suppress_a_resend(
    db_session: AsyncSession, unverified: User
):
    """Otherwise a user who verified one of two accounts could never get a
    link for the second one inside the cooldown window."""
    token = await mint_token(db_session, unverified)
    await db_session.commit()
    await redeem(db_session, token)
    await db_session.commit()

    assert await has_fresh_token(db_session, unverified) is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

async def test_verify_email_endpoint_returns_a_session(
    client: AsyncClient, db_session: AsyncSession, unverified: User
):
    """The link doubles as a login — signed up on the laptop, opened the
    email on the phone is the common case."""
    token = await mint_token(db_session, unverified)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


@pytest.mark.parametrize("token", ["never-minted", ""])
async def test_verify_email_endpoint_rejects_bad_token(
    client: AsyncClient, token: str
):
    resp = await client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 410
    assert resp.json()["detail"]["code"] == "verification_link_invalid"


async def test_resend_is_204_for_unknown_address(client: AsyncClient):
    """No leak about which addresses have accounts, same as forgot-password."""
    resp = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "nobody@example.test"}
    )
    assert resp.status_code == 204


async def test_resend_is_204_and_mints_nothing_for_a_verified_address(
    client: AsyncClient, db_session: AsyncSession, unverified: User
):
    unverified.email_verified_at = datetime.now(timezone.utc)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/resend-verification", json={"email": unverified.email}
    )
    assert resp.status_code == 204

    rows = (await db_session.execute(select(EmailVerificationToken))).scalars().all()
    assert rows == []


async def test_resend_is_rate_limited(client: AsyncClient):
    cap = settings.RESEND_VERIFICATION_RATE_LIMIT_PER_HOUR
    for _ in range(cap):
        resp = await client.post(
            "/api/v1/auth/resend-verification", json={"email": "a@example.test"}
        )
        assert resp.status_code == 204

    resp = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "a@example.test"}
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"


# ---------------------------------------------------------------------------
# Registration wiring
# ---------------------------------------------------------------------------

async def test_password_signup_starts_unverified_with_a_token(
    client: AsyncClient, db_session: AsyncSession
):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "Signup+Tag@Gmail.com",
            "password": "secret123",
            "full_name": "Signup User",
            "company_name": "SignupCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert resp.status_code == 201

    user = (await db_session.execute(
        select(User).where(User.email == "Signup+Tag@Gmail.com")
    )).scalar_one()
    assert user.email_verified_at is None
    # The canonical form is what makes serial signups from one mailbox
    # cluster; the raw address is preserved untouched for display and login.
    assert user.email_normalized == "signup@gmail.com"

    rows = (await db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    )).scalars().all()
    assert len(rows) == 1


async def test_google_signup_is_verified_without_an_email(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Google already asserted email_verified and the address was matched
    against the registration email — a confirmation link would prove nothing
    the ID token didn't."""
    import backend.routers.auth as auth_router

    async def fake_verify(id_token: str):
        return {"sub": "google-sub-verify", "email": "gverify@example.com",
                "email_verified": True, "name": "G"}

    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "gverify@example.com",
            "google_id_token": "fake-id-token",
            "full_name": "G",
            "company_name": "GCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert resp.status_code == 201

    user = (await db_session.execute(
        select(User).where(User.email == "gverify@example.com")
    )).scalar_one()
    assert user.email_verified_at is not None

    rows = (await db_session.execute(select(EmailVerificationToken))).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# What the gate does and does NOT block
# ---------------------------------------------------------------------------

async def test_unverified_manager_cannot_generate(
    client: AsyncClient, db_session: AsyncSession, unverified: User
):
    from tests.conftest import _make_token

    token = _make_token(unverified.id, unverified.company_id, "manager")
    resp = await client.post(
        "/api/v1/schedules/generate",
        json={"week_start_date": "2026-06-01", "use_local": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "email_not_verified"


async def test_unverified_manager_can_still_use_the_rest_of_the_app(
    client: AsyncClient, db_session: AsyncSession, unverified: User
):
    """The whole point of gating generation rather than login: setup stays
    open, so the prompt lands when the user is already invested."""
    from tests.conftest import _make_token

    token = _make_token(unverified.id, unverified.company_id, "manager")
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/locations/", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/employees/", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/roles/", headers=headers)).status_code == 200


async def test_me_reports_verification_state(
    client: AsyncClient, db_session: AsyncSession, unverified: User
):
    from tests.conftest import _make_token

    headers = {
        "Authorization": f"Bearer {_make_token(unverified.id, unverified.company_id, 'manager')}"
    }
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["email_verified"] is False

    unverified.email_verified_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["email_verified"] is True
