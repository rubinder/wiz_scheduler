"""Verify the new ORM models map to their tables and have the expected
columns and constraints."""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

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
async def test_schedule_lock_unique_per_company_location(
    db_session: AsyncSession, seeded_company
):
    """One live lock row per (company, location) (#180); a second location
    of the same company locks independently."""
    from backend.models import Location
    from tests.conftest import REGION_ID

    user_id = seeded_company.manager_user_id
    company_id = seeded_company.company_id
    location_id = seeded_company.location_id
    now = datetime.now(timezone.utc)

    other = Location(company_id=company_id, region_id=REGION_ID,
                     name="Second", timezone="America/New_York")
    db_session.add(other)
    await db_session.flush()

    db_session.add_all([
        ScheduleLock(company_id=company_id, location_id=location_id,
                     lock_group="grp00001", locked_by_user_id=user_id,
                     operation="generate", expires_at=now + timedelta(minutes=5)),
        ScheduleLock(company_id=company_id, location_id=other.id,
                     lock_group="grp00002", locked_by_user_id=user_id,
                     operation="generate", expires_at=now + timedelta(minutes=5)),
    ])
    await db_session.commit()

    db_session.add(ScheduleLock(
        company_id=company_id, location_id=location_id,
        lock_group="grp00003", locked_by_user_id=user_id,
        operation="approve", expires_at=now + timedelta(minutes=5),
    ))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
