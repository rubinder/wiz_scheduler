"""Signup signals are written at registration and enforced on by nothing.

The "enforced on by nothing" half is the load-bearing test: the columns are
there to be looked at, and the moment something starts denying on them it
should be a deliberate change with its own tests, not a drift.
"""
import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company
from backend.models.ownership_group import OwnershipGroup

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, company: str, **extra):
    body = {
        "email": email,
        "password": "secret123",
        "full_name": "Signal User",
        "company_name": company,
        "privacy_accepted": True,
        "terms_accepted": True,
    }
    body.update(extra)
    return await client.post("/api/v1/auth/register", json=body)


async def _og_for(db: AsyncSession, company_name: str) -> OwnershipGroup:
    og_id = (await db.execute(
        select(Company.ownership_group_id).where(Company.name == company_name)
    )).scalar_one()
    return await db.get(OwnershipGroup, og_id)


async def test_registration_records_all_signals(
    client: AsyncClient, db_session: AsyncSession
):
    resp = await _register(
        client, "signals@example.com", "SignalCo", device_id="device-abc-123"
    )
    assert resp.status_code == 201

    og = await _og_for(db_session, "SignalCo")
    assert og.signup_device_id == "device-abc-123"
    assert og.signup_email_normalized == "signals@example.com"
    assert og.signup_ip_masked  # masked to a /16, never absent
    assert og.signup_user_agent_hash is None or len(og.signup_user_agent_hash) == 64


async def test_signals_store_the_normalized_address(
    client: AsyncClient, db_session: AsyncSession
):
    """The whole point: `me+2@gmail.com` must land on the same key as
    `m.e@gmail.com` so the two signups cluster."""
    assert (await _register(
        client, "Serial+2@GoogleMail.com", "SerialCo"
    )).status_code == 201

    og = await _og_for(db_session, "SerialCo")
    assert og.signup_email_normalized == "serial@gmail.com"


async def test_two_signups_from_one_mailbox_share_a_key(
    client: AsyncClient, db_session: AsyncSession
):
    assert (await _register(client, "farm+a@gmail.com", "FarmA")).status_code == 201
    assert (await _register(client, "f.a.r.m@gmail.com", "FarmB")).status_code == 201

    a = await _og_for(db_session, "FarmA")
    b = await _og_for(db_session, "FarmB")
    assert a.id != b.id
    assert a.signup_email_normalized == b.signup_email_normalized == "farm@gmail.com"


async def test_missing_device_id_is_fine(
    client: AsyncClient, db_session: AsyncSession
):
    """API clients and anyone with storage disabled send nothing. That is an
    expected gap in an observe-only signal, not an error."""
    assert (await _register(client, "nodev@example.com", "NoDevCo")).status_code == 201

    og = await _og_for(db_session, "NoDevCo")
    assert og.signup_device_id is None
    assert og.signup_email_normalized == "nodev@example.com"


async def test_user_agent_is_hashed_not_stored(
    client: AsyncClient, db_session: AsyncSession
):
    ua = "Mozilla/5.0 (Macintosh) SignalTest/1.0"
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ua@example.com",
            "password": "secret123",
            "full_name": "UA User",
            "company_name": "UaCo",
            "privacy_accepted": True,
            "terms_accepted": True,
        },
        headers={"user-agent": ua},
    )
    assert resp.status_code == 201

    og = await _og_for(db_session, "UaCo")
    assert og.signup_user_agent_hash == hashlib.sha256(ua.encode()).hexdigest()
    assert ua not in (og.signup_user_agent_hash or "")


async def test_an_oversized_device_id_is_truncated_not_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """The value is caller-controlled. Bound it, but never fail a signup
    over a signal."""
    assert (await _register(
        client, "big@example.com", "BigCo", device_id="x" * 500
    )).status_code == 201

    og = await _og_for(db_session, "BigCo")
    assert og.signup_device_id is not None
    assert len(og.signup_device_id) == 64


async def test_signals_do_not_block_a_repeat_signup(
    client: AsyncClient, db_session: AsyncSession
):
    """OBSERVE-ONLY. Same device, same normalized mailbox, second account —
    and it still succeeds. If this test ever fails, someone wired enforcement
    onto these columns; that needs its own decision, not a silent change."""
    assert (await _register(
        client, "twice+1@gmail.com", "TwiceA", device_id="same-device"
    )).status_code == 201
    resp = await _register(
        client, "twice+2@gmail.com", "TwiceB", device_id="same-device"
    )
    assert resp.status_code == 201

    ogs = (await db_session.execute(
        select(OwnershipGroup).where(
            OwnershipGroup.signup_device_id == "same-device"
        )
    )).scalars().all()
    assert len(ogs) == 2


async def test_retention_clears_old_signals_but_keeps_the_group(
    client: AsyncClient, db_session: AsyncSession
):
    from datetime import datetime, timedelta, timezone

    from backend.config import settings
    from backend.services.data_retention import run_data_retention

    assert (await _register(
        client, "old@example.com", "OldCo", device_id="old-device"
    )).status_code == 201

    og = await _og_for(db_session, "OldCo")
    og_id = og.id
    og.created_at = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_SIGNUP_SIGNALS_DAYS + 1
    )
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["signup_signals_cleared"] == 1

    og = await db_session.get(OwnershipGroup, og_id)
    assert og is not None  # the tenant survives; only the breadcrumbs go
    assert og.signup_device_id is None
    assert og.signup_email_normalized is None
    assert og.signup_ip_masked is None
    assert og.signup_user_agent_hash is None


async def test_retention_leaves_recent_signals_alone(
    client: AsyncClient, db_session: AsyncSession
):
    from backend.services.data_retention import run_data_retention

    assert (await _register(
        client, "recent@example.com", "RecentCo", device_id="recent-device"
    )).status_code == 201

    await run_data_retention(db_session)

    og = await _og_for(db_session, "RecentCo")
    assert og.signup_device_id == "recent-device"
