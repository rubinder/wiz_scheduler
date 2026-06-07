"""Tests for /auth/forgot-password + /auth/reset-password."""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import PasswordResetToken, User


@pytest.mark.asyncio
async def test_forgot_password_existing_email_creates_token(
    client: AsyncClient, db_session: AsyncSession, seed_manager
):
    resp = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "manager@test.com"}
    )
    assert resp.status_code == 204

    rows = (await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == seed_manager.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].used_at is None
    # SQLite drops tzinfo on DateTime round-trip; reattach UTC for the compare.
    exp = rows[0].expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_forgot_password_unknown_email_no_token(
    client: AsyncClient, db_session: AsyncSession
):
    """No row created, but the response is still 204 — no leak."""
    resp = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "nobody@example.test"}
    )
    assert resp.status_code == 204

    rows = (await db_session.execute(select(PasswordResetToken))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_reset_password_happy_path(
    client: AsyncClient, db_session: AsyncSession, seed_manager
):
    db_session.add(PasswordResetToken(
        user_id=seed_manager.id,
        token="reset_token_happy_path_xxxxxxx",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "reset_token_happy_path_xxxxxxx", "new_password": "new-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"

    # Old password no longer works
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "testpass"},
    )
    assert bad.status_code == 401

    # New password works
    good = await client.post(
        "/api/v1/auth/login",
        json={"email": "manager@test.com", "password": "new-secret"},
    )
    assert good.status_code == 200

    # Token marked used
    token_row = (await db_session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token == "reset_token_happy_path_xxxxxxx"
        )
    )).scalar_one()
    assert token_row.used_at is not None


@pytest.mark.asyncio
async def test_reset_password_expired_returns_410(
    client: AsyncClient, db_session: AsyncSession, seed_manager
):
    db_session.add(PasswordResetToken(
        user_id=seed_manager.id,
        token="reset_token_expired_xxxxxxxxxxxx",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "reset_token_expired_xxxxxxxxxxxx", "new_password": "anything"},
    )
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_reset_password_already_used_returns_410(
    client: AsyncClient, db_session: AsyncSession, seed_manager
):
    db_session.add(PasswordResetToken(
        user_id=seed_manager.id,
        token="reset_token_used_xxxxxxxxxxxxxxx",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        used_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "reset_token_used_xxxxxxxxxxxxxxx", "new_password": "anything"},
    )
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_reset_password_unknown_token_returns_404(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "totally_made_up_token", "new_password": "anything"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reset_password_rejects_short_password(
    client: AsyncClient, db_session: AsyncSession, seed_manager
):
    db_session.add(PasswordResetToken(
        user_id=seed_manager.id,
        token="reset_token_shortpw_xxxxxxxxxxx",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "reset_token_shortpw_xxxxxxxxxxx", "new_password": "abc"},
    )
    assert resp.status_code == 400
    assert "6 characters" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_forgot_password_per_email_cooldown_skips_second_send(
    client: AsyncClient, db_session: AsyncSession, seed_manager, monkeypatch
):
    """A second forgot-password within the cooldown window creates no new
    token and sends no email, but still returns 204 (no-leak)."""
    sent: list = []

    async def fake_send(email: str, reset_url: str):
        sent.append((email, reset_url))
        return True

    monkeypatch.setattr(
        "backend.services.password_reset_email.send_password_reset_email",
        fake_send,
    )

    # First call mints a token + sends an email.
    r1 = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "manager@test.com"}
    )
    assert r1.status_code == 204

    rows_after_first = (await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == seed_manager.id)
    )).scalars().all()
    assert len(rows_after_first) == 1
    assert len(sent) == 1

    # Second call within the cooldown — no new token, no new email, still 204.
    r2 = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "manager@test.com"}
    )
    assert r2.status_code == 204

    rows_after_second = (await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == seed_manager.id)
    )).scalars().all()
    assert len(rows_after_second) == 1, "no new token should be created during cooldown"
    assert len(sent) == 1, "no second email should be sent during cooldown"


@pytest.mark.asyncio
async def test_forgot_password_cooldown_expires_lets_new_token_mint(
    client: AsyncClient, db_session: AsyncSession, seed_manager, monkeypatch
):
    """After the cooldown window passes, a new request mints a fresh token."""
    # Plant an existing token created OUTSIDE the cooldown (older than
    # FORGOT_PASSWORD_COOLDOWN_MINUTES). Bypass the API for setup so we
    # can backdate created_at deterministically.
    old_created = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.add(PasswordResetToken(
        id="old0t0kn",
        user_id=seed_manager.id,
        token="t_old_outside_cooldown_window_aa",
        created_at=old_created,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    ))
    await db_session.commit()

    async def fake_send(email: str, reset_url: str):
        return True
    monkeypatch.setattr(
        "backend.services.password_reset_email.send_password_reset_email",
        fake_send,
    )

    r = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "manager@test.com"}
    )
    assert r.status_code == 204

    rows = (await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == seed_manager.id)
    )).scalars().all()
    assert len(rows) == 2, "old token outside cooldown should not block new mint"


@pytest.mark.asyncio
async def test_forgot_password_per_ip_rate_limit_returns_429(
    client: AsyncClient, db_session: AsyncSession
):
    """After FORGOT_PASSWORD_RATE_LIMIT_PER_5MIN hits from the same IP,
    the next request returns 429 with the structured rate_limited code."""
    from backend.config import settings
    from backend.services.rate_limit import forgot_password_limiter
    forgot_password_limiter.reset()  # clear any state leaked from other tests

    limit = settings.FORGOT_PASSWORD_RATE_LIMIT_PER_5MIN

    for i in range(limit):
        r = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": f"unknown-{i}@example.test"},
        )
        assert r.status_code == 204, f"request {i + 1}/{limit} should pass"

    # The (limit+1)th request from the same IP is blocked.
    r = await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "another-unknown@example.test"},
    )
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_forgot_password_used_token_does_not_block_cooldown(
    client: AsyncClient, db_session: AsyncSession, seed_manager, monkeypatch
):
    """The cooldown filter only counts UNUSED tokens. If the previous token
    was already used (reset complete), a new forgot-password mints another."""
    db_session.add(PasswordResetToken(
        id="used0tok",
        user_id=seed_manager.id,
        token="t_used_within_cooldown_xxxxxxxxxx",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=29),
        used_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    async def fake_send(email, reset_url):
        return True
    monkeypatch.setattr(
        "backend.services.password_reset_email.send_password_reset_email",
        fake_send,
    )

    r = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "manager@test.com"}
    )
    assert r.status_code == 204

    rows = (await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == seed_manager.id)
    )).scalars().all()
    assert len(rows) == 2, "used token should not block a new mint"
