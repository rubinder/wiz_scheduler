"""Tests for the /api/v1/auth endpoints."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, User
from backend.models.ownership_group import OwnershipGroup

pytestmark = pytest.mark.asyncio


async def test_register_creates_user_and_company(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "secret123",
            "full_name": "New User",
            "company_name": "NewCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_login_correct_credentials(client: AsyncClient, seed_manager):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "testpass"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


async def test_login_wrong_password(client: AsyncClient, seed_manager):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


async def test_login_wrong_password_emits_log(client: AsyncClient, seed_manager, caplog):
    """401 from an existing-email-wrong-password attempt must emit a structured log."""
    import logging
    caplog.set_level(logging.INFO, logger="backend.routers.auth")
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401
    matching = [r for r in caplog.records if "login.fail" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "reason=wrong_password" in msg
    assert "manager@test.com" not in msg  # email must be masked
    assert "man***@test.com" in msg


async def test_login_no_user_emits_log(client: AsyncClient, caplog):
    """401 when no user has that email at all must log reason=no_user_with_email."""
    import logging
    caplog.set_level(logging.INFO, logger="backend.routers.auth")
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.test", "password": "anything"},
    )
    assert resp.status_code == 401
    matching = [r for r in caplog.records if "login.fail" in r.getMessage()]
    assert len(matching) == 1
    assert "reason=no_user_with_email" in matching[0].getMessage()


async def test_me_with_valid_token(client: AsyncClient, manager_token: str):
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "manager@test.com"
    assert data["user_role"] == "manager"


async def test_me_without_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


async def test_manager_route_with_employee_token(
    client: AsyncClient,
    employee_token: str,
    seed_employees,
):
    """Employee token should be rejected by manager-only endpoints."""
    resp = await client.get(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Google-based registration
# ---------------------------------------------------------------------------


async def test_register_with_google_token(client: AsyncClient, db_session, monkeypatch):
    """Registering with a verified Google id_token creates a user with
    google_id set, no human-known password, and links the OG."""
    from backend.routers import auth as auth_router
    from sqlalchemy import select
    from backend.models import User

    async def fake_verify(_token):
        return {"sub": "google-sub-123", "email": "alice@example.com",
                "email_verified": True, "name": "Alice"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "google_id_token": "fake-id-token",
            "full_name": "Alice",
            "company_name": "AliceCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["token_type"] == "bearer"

    user = (await db_session.execute(
        select(User).where(User.email == "alice@example.com")
    )).scalar_one()
    assert user.google_id == "google-sub-123"
    # hashed_password is set (random) but not derivable from anything caller knows
    assert user.hashed_password
    assert user.hashed_password.startswith("$2b$")  # bcrypt


async def test_register_with_google_requires_exactly_one_credential(client: AsyncClient):
    # Both password AND google_id_token → 400
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com", "password": "abc", "google_id_token": "tok",
            "full_name": "X", "company_name": "X",
            "privacy_accepted": True, "terms_accepted": True,
        },
    )
    assert resp.status_code == 400
    assert "exactly one" in resp.json()["detail"].lower()

    # Neither → 400
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com", "full_name": "X", "company_name": "X",
            "privacy_accepted": True, "terms_accepted": True,
        },
    )
    assert resp.status_code == 400


async def test_register_google_token_invalid(client: AsyncClient, monkeypatch):
    from backend.routers import auth as auth_router
    async def fake_verify(_token):
        return None
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com", "google_id_token": "bogus",
            "full_name": "X", "company_name": "X",
            "privacy_accepted": True, "terms_accepted": True,
        },
    )
    assert resp.status_code == 401


async def test_register_google_email_mismatch(client: AsyncClient, monkeypatch):
    """The Google verified email must match the registration email."""
    from backend.routers import auth as auth_router
    async def fake_verify(_token):
        return {"sub": "g-sub", "email": "actual-google@example.com",
                "email_verified": True, "name": "X"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "different@example.com", "google_id_token": "tok",
            "full_name": "X", "company_name": "X",
            "privacy_accepted": True, "terms_accepted": True,
        },
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"].lower()


async def test_register_google_email_not_verified(client: AsyncClient, monkeypatch):
    from backend.routers import auth as auth_router
    async def fake_verify(_token):
        return {"sub": "g-sub", "email": "x@example.com",
                "email_verified": False, "name": "X"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com", "google_id_token": "tok",
            "full_name": "X", "company_name": "X",
            "privacy_accepted": True, "terms_accepted": True,
        },
    )
    assert resp.status_code == 400
    assert "not verified" in resp.json()["detail"].lower()


# ── /auth/google/link-current ──

async def test_google_link_current_happy_path(
    client: AsyncClient, db_session, manager_token: str, seed_manager, monkeypatch
):
    """JWT-authenticated user links Google by id_token; google_id is persisted."""
    from backend.routers import auth as auth_router
    from sqlalchemy import select
    from backend.models import User

    async def fake_verify(_token):
        return {"sub": "google-sub-mgr", "email": "manager@test.com",
                "email_verified": True, "name": "Test Manager"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/google/link-current",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"id_token": "fake-id-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"

    await db_session.refresh(seed_manager)
    assert seed_manager.google_id == "google-sub-mgr"


async def test_google_link_current_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/google/link-current",
        json={"id_token": "anything"},
    )
    assert resp.status_code in (401, 403)


async def test_google_link_current_invalid_token(
    client: AsyncClient, manager_token: str, monkeypatch
):
    from backend.routers import auth as auth_router
    async def fake_verify(_token):
        return None
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/google/link-current",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"id_token": "bogus"},
    )
    assert resp.status_code == 401


async def test_google_link_current_email_mismatch(
    client: AsyncClient, manager_token: str, monkeypatch
):
    """Google account email must match the logged-in user's email."""
    from backend.routers import auth as auth_router
    async def fake_verify(_token):
        return {"sub": "g-sub", "email": "someone-else@example.com",
                "email_verified": True, "name": "X"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/google/link-current",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"id_token": "fake"},
    )
    assert resp.status_code == 400
    assert "must match" in resp.json()["detail"].lower()


async def test_google_link_current_409_when_linked_to_other_user(
    client: AsyncClient, db_session, manager_token: str, seed_manager, monkeypatch
):
    """If the google_id is already linked to a different person, return 409."""
    from backend.routers import auth as auth_router
    from passlib.context import CryptContext
    from backend.models import Company, User

    # Seed a second User in a different Company with the same google_id we're
    # about to try to link.
    other_co = Company(name="OtherCo", slug="other-co-slug")
    db_session.add(other_co)
    await db_session.flush()
    pwd = CryptContext(schemes=["bcrypt"]).hash("doesntmatter")
    db_session.add(User(
        company_id=other_co.id,
        email="someone-else@test.com",  # different email
        hashed_password=pwd,
        full_name="Other Person",
        user_role="manager",
        google_id="already-claimed-sub",
    ))
    await db_session.commit()

    async def fake_verify(_token):
        return {"sub": "already-claimed-sub", "email": "manager@test.com",
                "email_verified": True, "name": "Test Manager"}
    monkeypatch.setattr(auth_router, "_verify_google_token", fake_verify)

    resp = await client.post(
        "/api/v1/auth/google/link-current",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"id_token": "fake"},
    )
    assert resp.status_code == 409


async def test_me_returns_has_google(
    client: AsyncClient, db_session, manager_token: str, seed_manager
):
    """GET /me must include has_google so the Dashboard can hide the card after linking."""
    # Before linking: has_google=False
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_google"] is False

    # After linking: has_google=True
    seed_manager.google_id = "some-google-sub"
    await db_session.commit()
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.json()["has_google"] is True


# ---------------------------------------------------------------------------
# Per-IP rate limits (Tier 1B + 1C)
# ---------------------------------------------------------------------------


async def test_login_per_ip_rate_limit_returns_429(client: AsyncClient):
    """After LOGIN_RATE_LIMIT_PER_5MIN attempts from the same IP, the next
    one returns 429 with the structured rate_limited code.

    Uses non-existent emails so no bcrypt verify runs — keeps the test fast.
    """
    from backend.config import settings
    from backend.services.rate_limit import login_limiter
    login_limiter.reset()

    limit = settings.LOGIN_RATE_LIMIT_PER_5MIN

    for i in range(limit):
        r = await client.post(
            "/api/v1/auth/login",
            json={"email": f"nobody-{i}@example.test", "password": "x"},
        )
        # Unknown email returns 401 — slot still consumed.
        assert r.status_code == 401, f"attempt {i + 1}/{limit} should reach the handler"

    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "another-nobody@example.test", "password": "x"},
    )
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"


async def test_login_rate_limit_isolated_per_ip(client: AsyncClient):
    """A second IP isn't affected by the first IP's blocks."""
    from backend.config import settings
    from backend.services.rate_limit import login_limiter
    login_limiter.reset()

    # Burn the limit from IP A.
    for i in range(settings.LOGIN_RATE_LIMIT_PER_5MIN):
        await client.post(
            "/api/v1/auth/login",
            json={"email": f"a-{i}@example.test", "password": "x"},
            headers={"X-Forwarded-For": "10.0.0.1"},
        )

    # IP A is blocked.
    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "a-extra@example.test", "password": "x"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert blocked.status_code == 429

    # IP B is still free.
    ok = await client.post(
        "/api/v1/auth/login",
        json={"email": "b@example.test", "password": "x"},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert ok.status_code == 401  # reached the handler


async def test_register_per_ip_rate_limit_returns_429(client: AsyncClient):
    """After REGISTER_RATE_LIMIT_PER_HOUR registrations from the same IP,
    the next one returns 429."""
    from backend.config import settings
    from backend.services.rate_limit import register_limiter
    register_limiter.reset()

    limit = settings.REGISTER_RATE_LIMIT_PER_HOUR

    for i in range(limit):
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"new-{i}@example.com",
                "password": "secret123",
                "full_name": f"New {i}",
                "company_name": f"Co{i}",
                "privacy_accepted": True,
                "terms_accepted": True,
            },
        )
        assert r.status_code == 201, f"registration {i + 1}/{limit} should succeed"

    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "overflow@example.com",
            "password": "secret123",
            "full_name": "Overflow",
            "company_name": "OverflowCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"


async def test_register_rate_limit_blocks_before_any_work(
    client: AsyncClient, db_session
):
    """Rate-limited register must not create a User, Company, or call Stripe."""
    from sqlalchemy import select
    from backend.config import settings
    from backend.models import Company, User
    from backend.services.rate_limit import register_limiter
    register_limiter.reset()

    # Burn the slots with successful registrations.
    for i in range(settings.REGISTER_RATE_LIMIT_PER_HOUR):
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"burn-{i}@example.com",
                "password": "secret123",
                "full_name": "Burn",
                "company_name": f"BurnCo{i}",
                "privacy_accepted": True,
                "terms_accepted": True,
            },
        )

    pre_users = (await db_session.execute(select(User))).scalars().all()
    pre_companies = (await db_session.execute(select(Company))).scalars().all()

    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "blocked@example.com",
            "password": "secret123",
            "full_name": "Blocked",
            "company_name": "ShouldNotExist",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )
    assert r.status_code == 429

    post_users = (await db_session.execute(select(User))).scalars().all()
    post_companies = (await db_session.execute(select(Company))).scalars().all()
    assert len(post_users) == len(pre_users)
    assert len(post_companies) == len(pre_companies)


# ---------------------------------------------------------------------------
# Free-by-default registration (Task 8)
# ---------------------------------------------------------------------------


async def test_register_without_stripe_session_creates_free_account(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Registration no longer requires a completed Checkout session."""
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(_s, "STRIPE_PRICE_ID", "price_x")

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "free@example.test",
            "password": "hunter2hunter2",
            "full_name": "Free User",
            "company_name": "Free Co",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
    )

    assert resp.status_code == 201
    assert "access_token" in resp.json()

    og = (await db_session.execute(
        select(OwnershipGroup).where(OwnershipGroup.name == "Free Co")
    )).scalar_one()
    assert og.stripe_subscription_id is None
    assert og.canceled_at is None


async def test_register_with_paid_stripe_session_creates_paid_account(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A valid, paid Checkout session still verifies via Stripe and the
    ownership group is created with both stripe_customer_id and
    stripe_subscription_id populated from the session."""
    import stripe

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_test_paid_abc",
        subscription="sub_test_paid_xyz",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "paid@example.test",
            "password": "hunter2hunter2",
            "full_name": "Paid User",
            "company_name": "Paid Co",
            "privacy_accepted": True,
            "terms_accepted": True,
            "stripe_session_id": "cs_test_paid_1",
        },
    )

    assert resp.status_code == 201
    assert "access_token" in resp.json()

    og = (await db_session.execute(
        select(OwnershipGroup).where(OwnershipGroup.name == "Paid Co")
    )).scalar_one()
    assert og.stripe_customer_id == "cus_test_paid_abc"
    assert og.stripe_subscription_id == "sub_test_paid_xyz"


async def test_register_with_unpaid_stripe_session_rejected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A Checkout session that has not completed payment must be rejected,
    and no OwnershipGroup / Company / User row may be created."""
    import stripe

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    fake_session = MagicMock(
        payment_status="unpaid",
        customer="cus_test_unpaid_abc",
        subscription=None,
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "unpaid@example.test",
            "password": "hunter2hunter2",
            "full_name": "Unpaid User",
            "company_name": "Unpaid Co",
            "privacy_accepted": True,
            "terms_accepted": True,
            "stripe_session_id": "cs_test_unpaid_1",
        },
    )

    assert resp.status_code == 400

    og = (await db_session.execute(
        select(OwnershipGroup).where(OwnershipGroup.name == "Unpaid Co")
    )).scalar_one_or_none()
    assert og is None
    user = (await db_session.execute(
        select(User).where(User.email == "unpaid@example.test")
    )).scalar_one_or_none()
    assert user is None
    company = (await db_session.execute(
        select(Company).where(Company.name == "Unpaid Co")
    )).scalar_one_or_none()
    assert company is None


async def test_register_with_invalid_stripe_session_rejected(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """If Stripe rejects the session id (bad/expired/nonexistent), registration
    must fail with 400 rather than propagating the Stripe error."""
    import stripe

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_dummy")

    def boom(sid):
        raise stripe.error.InvalidRequestError("No such checkout session", "id")
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", boom)

    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalidsession@example.test",
            "password": "hunter2hunter2",
            "full_name": "Invalid Session User",
            "company_name": "InvalidSession Co",
            "privacy_accepted": True,
            "terms_accepted": True,
            "stripe_session_id": "cs_test_bogus",
        },
    )

    assert resp.status_code == 400
