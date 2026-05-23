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
