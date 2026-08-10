"""The five rows of the generation decision table."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, User
from backend.models.ownership_group import OwnershipGroup
from backend.services.plan import check_can_generate
from fastapi import HTTPException
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="G"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="C", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return {"og_id": og_id, "company_id": company_id}


async def _make_over_limit(db: AsyncSession, company_id: str) -> None:
    for i in range(6):
        db.add(Employee(id=_id(), company_id=company_id, full_name=f"E{i}"))
    await db.commit()


async def test_paid_can_generate_both(db_session: AsyncSession, tenant: dict):
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    await db_session.commit()

    await check_can_generate(db_session, tenant["company_id"], use_local=True)
    await check_can_generate(db_session, tenant["company_id"], use_local=False)


async def test_free_within_limit_local_ok_ai_blocked(
    db_session: AsyncSession, tenant: dict
):
    await check_can_generate(db_session, tenant["company_id"], use_local=True)

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=False)
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "ai_requires_paid_plan"


async def test_free_over_limit_blocks_local_too(
    db_session: AsyncSession, tenant: dict
):
    await _make_over_limit(db_session, tenant["company_id"])

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=True)
    assert exc.value.detail["code"] == "plan_limit_exceeded"


async def test_canceled_within_limit_local_ok(
    db_session: AsyncSession, tenant: dict
):
    """Cancel -> free: a shrunken tenant keeps working."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    await check_can_generate(db_session, tenant["company_id"], use_local=True)

    with pytest.raises(HTTPException) as exc:
        await check_can_generate(db_session, tenant["company_id"], use_local=False)
    assert exc.value.detail["code"] == "ai_requires_paid_plan"


async def test_canceled_over_limit_keeps_grace_block(
    db_session: AsyncSession, tenant: dict
):
    """The downgraded over-limit state — today's 90-day read-only grace."""
    og = await db_session.get(OwnershipGroup, tenant["og_id"])
    og.stripe_subscription_id = "sub_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()
    await _make_over_limit(db_session, tenant["company_id"])

    for use_local in (True, False):
        with pytest.raises(HTTPException) as exc:
            await check_can_generate(
                db_session, tenant["company_id"], use_local=use_local
            )
        assert exc.value.detail["code"] == "subscription_canceled"


async def test_no_ownership_group_can_generate(db_session: AsyncSession):
    company_id = _id()
    db_session.add(Company(id=company_id, name="Orphan", slug=_id()))
    await db_session.commit()

    await check_can_generate(db_session, company_id, use_local=True)
    await check_can_generate(db_session, company_id, use_local=False)
