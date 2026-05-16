"""Tests for /api/v1/manager-invites endpoints."""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, ManagerInvite, OwnershipGroup, User


@pytest.mark.asyncio
async def test_create_invite_happy_path(
    client: AsyncClient, db_session: AsyncSession, manager_token, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    company = await db_session.get(Company, seeded_company.company_id)
    company.ownership_group_id = og.id
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"email": "new.mgr@acme.test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "new.mgr@acme.test"
    assert body["status"] == "pending"
    assert body["token"]

    rows = (await db_session.execute(select(ManagerInvite))).scalars().all()
    assert len(rows) == 1
    assert rows[0].ownership_group_id == og.id


@pytest.mark.asyncio
async def test_create_invite_400_when_company_has_no_og(
    client: AsyncClient, manager_token, seeded_company
):
    resp = await client.post(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"email": "x@y.test"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_info_returns_companies(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    db_session.add(Company(name="Sister Co", slug="sister-co", ownership_group_id=og.id))
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="new@acme.test",
        token="t_" + "y" * 40,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.get(f"/api/v1/manager-invites/info?token={invite.token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "new@acme.test"
    assert body["group_name"] == "Acme OG"
    assert body["expired"] is False
    company_names = {c["name"] for c in body["companies"]}
    assert "Sister Co" in company_names


@pytest.mark.asyncio
async def test_accept_happy_path(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="accept@acme.test",
        token="t_" + "z" * 40,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": seeded_company.company_id,
            "full_name": "New Manager",
            "password": "supersecret",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()

    await db_session.refresh(invite)
    assert invite.status == "accepted"
    assert invite.accepted_company_id == seeded_company.company_id

    new_user = (await db_session.execute(
        select(User).where(User.email == "accept@acme.test")
    )).scalar_one()
    assert new_user.user_role == "manager"
    assert new_user.company_id == seeded_company.company_id


@pytest.mark.asyncio
async def test_accept_410_on_expired(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="late@acme.test",
        token="t_expired_zz",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": seeded_company.company_id,
            "full_name": "L M",
            "password": "x" * 12,
        },
    )
    assert resp.status_code == 410
    await db_session.refresh(invite)
    assert invite.status == "expired"


@pytest.mark.asyncio
async def test_accept_400_company_outside_og(
    client: AsyncClient, db_session: AsyncSession, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    other_og = OwnershipGroup(name="Other OG")
    db_session.add_all([og, other_og])
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    outsider = Company(name="Outsider", slug="outsider", ownership_group_id=other_og.id)
    db_session.add(outsider)
    await db_session.flush()

    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="x@y.test",
        token="t_outsider_zz",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/manager-invites/accept",
        json={
            "token": invite.token,
            "company_id": outsider.id,
            "full_name": "X",
            "password": "x" * 12,
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_invites_returns_og_scope(
    client: AsyncClient, db_session: AsyncSession, manager_token, seeded_company
):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    co = await db_session.get(Company, seeded_company.company_id)
    co.ownership_group_id = og.id
    for i in range(2):
        db_session.add(ManagerInvite(
            ownership_group_id=og.id,
            invited_by_user_id=seeded_company.manager_user_id,
            email=f"m{i}@acme.test",
            token=f"t_list_{i}_aaaaaaaaaaaaaaaaaaa",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
    await db_session.commit()

    resp = await client.get(
        "/api/v1/manager-invites/",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2
