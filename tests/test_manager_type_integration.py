"""Managers get a manager_type (#149), and /auth/me reports it."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import User
from backend.services.passwords import hash_password
from tests.conftest import COMPANY_ID, MANAGER_USER_ID, _id, _make_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_registering_creates_an_admin_manager(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post("/api/v1/auth/register", json={
        "email": "founder@example.test", "password": "hunter2hunter2",
        "full_name": "Founder", "company_name": "Founded Co",
        "privacy_accepted": True, "terms_accepted": True,
    })
    assert resp.status_code == 201, resp.text
    user = (await db_session.execute(
        select(User).where(User.email == "founder@example.test")
    )).scalar_one()
    assert user.manager_type == "admin"

    me = await client.get("/api/v1/auth/me", headers=_h(resp.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["manager_type"] == "admin"


async def _regular_manager(db: AsyncSession) -> User:
    user = User(
        id=_id(), company_id=COMPANY_ID, email="regular@test.com",
        hashed_password=hash_password("x"), full_name="Regular",
        user_role="manager", manager_type="regular",
    )
    db.add(user)
    await db.commit()
    return user


async def test_a_regular_manager_sees_only_their_own_assignments(
    client: AsyncClient, db_session: AsyncSession, seed_manager, seed_location,
) -> None:
    regular = await _regular_manager(db_session)
    token = _make_token(regular.id, COMPANY_ID, "manager")

    own = await client.get(f"/api/v1/managers/{regular.id}/locations", headers=_h(token))
    assert own.status_code == 200, own.text

    other = await client.get(f"/api/v1/managers/{MANAGER_USER_ID}/locations", headers=_h(token))
    assert other.status_code == 403


async def test_an_admin_sees_any_managers_assignments(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, seed_location,
) -> None:
    regular = await _regular_manager(db_session)
    resp = await client.get(f"/api/v1/managers/{regular.id}/locations", headers=_h(manager_token))
    assert resp.status_code == 200, resp.text
