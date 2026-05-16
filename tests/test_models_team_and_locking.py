"""Verify the new ORM models map to their tables and have the expected
columns and constraints."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    ManagerInvite,
    OwnershipGroup,
    ScheduleLock,
    User,
)


@pytest.mark.asyncio
async def test_manager_invite_round_trip(db_session: AsyncSession, seeded_company):
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    seeded_company.ownership_group_id = og.id
    await db_session.flush()

    invite = ManagerInvite(
        ownership_group_id=og.id,
        invited_by_user_id=seeded_company.manager_user_id,
        email="newmgr@example.com",
        token="tok_" + "x" * 30,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(invite)
    await db_session.commit()

    row = (await db_session.execute(
        sa.select(ManagerInvite).where(ManagerInvite.token == invite.token)
    )).scalar_one()
    assert row.ownership_group_id == og.id
    assert row.status == "pending"
    assert row.accepted_company_id is None


@pytest.mark.asyncio
async def test_schedule_lock_unique_per_company(
    db_session: AsyncSession, seeded_company
):
    """Cannot have two active locks for the same company."""
    user_id = seeded_company.manager_user_id
    company_id = seeded_company.company_id
    now = datetime.now(timezone.utc)

    lock1 = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation="generate",
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(lock1)
    await db_session.commit()

    lock2 = ScheduleLock(
        company_id=company_id,
        locked_by_user_id=user_id,
        operation="approve",
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(lock2)
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
