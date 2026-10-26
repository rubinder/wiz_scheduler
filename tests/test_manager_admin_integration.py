"""Manager admin (#149, #150, spec build step 2, Q5, Q6): the team list,
changing a manager's type and locations, regular-manager invites, and the
last-admin rule."""
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Location, ManagerLocation, OwnershipGroup, Region, User
from tests.conftest import (
    COMPANY2_ID,
    COMPANY_ID,
    LOCATION_ID,
    MANAGER_USER_ID,
    OTHER_LOCATION_ID,
    REGION_ID,
    REGULAR_MANAGER_ID,
    _id,
    _make_token,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

API = "/api/v1"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _locations_of(db: AsyncSession, manager_id: str) -> set[str]:
    rows = await db.execute(
        select(ManagerLocation.location_id).where(ManagerLocation.manager_id == manager_id)
    )
    return set(rows.scalars().all())


SECOND_ADMIN_ID = _id()


@pytest_asyncio.fixture
async def second_admin(db_session: AsyncSession, seed_manager: User) -> User:
    user = User(
        id=SECOND_ADMIN_ID,
        company_id=COMPANY_ID,
        email="second-admin@test.com",
        hashed_password="x",
        full_name="Second Admin",
        user_role="manager",
        manager_type="admin",
        email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def with_og(db_session: AsyncSession, seed_company: Company) -> OwnershipGroup:
    og = OwnershipGroup(name="Acme OG")
    db_session.add(og)
    await db_session.flush()
    company = await db_session.get(Company, COMPANY_ID)
    company.ownership_group_id = og.id
    await db_session.commit()
    return og


# --------------------------------------------------------------------------
# GET /managers
# --------------------------------------------------------------------------

async def test_list_managers(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, regular_manager: User
) -> None:
    # Another company's manager is not listed; neither is an employee user.
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    db_session.add(User(
        id=_id(), company_id=COMPANY2_ID, email="elsewhere@test.com",
        hashed_password="x", user_role="manager", manager_type="admin",
    ))
    # A pre-0049 NULL manager is reported as admin.
    legacy_id = _id()
    db_session.add(User(
        id=legacy_id, company_id=COMPANY_ID, email="legacy@test.com",
        hashed_password="x", user_role="manager", manager_type=None,
    ))
    # An archived assigned location is listed, flagged.
    other = await db_session.get(Location, OTHER_LOCATION_ID)
    other.archived_at = datetime.now(timezone.utc)
    db_session.add(ManagerLocation(manager_id=REGULAR_MANAGER_ID, location_id=OTHER_LOCATION_ID))
    await db_session.commit()

    resp = await client.get(f"{API}/managers/", headers=_auth(manager_token))
    assert resp.status_code == 200, resp.text
    by_id = {m["id"]: m for m in resp.json()}
    assert set(by_id) == {MANAGER_USER_ID, REGULAR_MANAGER_ID, legacy_id}

    assert by_id[MANAGER_USER_ID]["manager_type"] == "admin"
    assert by_id[MANAGER_USER_ID]["assigned_locations"] == []
    assert by_id[legacy_id]["manager_type"] == "admin"

    regular = by_id[REGULAR_MANAGER_ID]
    assert regular["manager_type"] == "regular"
    assert regular["email"] == "regular-manager@test.com"
    assert regular["full_name"] == "Regular Manager"
    locs = {loc["id"]: loc["archived"] for loc in regular["assigned_locations"]}
    assert locs == {LOCATION_ID: False, OTHER_LOCATION_ID: True}


# --------------------------------------------------------------------------
# PUT /managers/{id}
# --------------------------------------------------------------------------

async def test_demote_admin_to_regular(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    second_admin: User, other_location: Location,
) -> None:
    resp = await client.put(
        f"{API}/managers/{SECOND_ADMIN_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "regular", "location_ids": [OTHER_LOCATION_ID, OTHER_LOCATION_ID]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["manager_type"] == "regular"
    assert [loc["id"] for loc in body["assigned_locations"]] == [OTHER_LOCATION_ID]

    db_session.expire_all()
    assert (await db_session.get(User, SECOND_ADMIN_ID)).manager_type == "regular"
    assert await _locations_of(db_session, SECOND_ADMIN_ID) == {OTHER_LOCATION_ID}

    # The demoted manager's own /auth/me now reports the scope.
    token = _make_token(SECOND_ADMIN_ID, COMPANY_ID, "manager")
    me = await client.get(f"{API}/auth/me", headers=_auth(token))
    assert me.json()["accessible_location_ids"] == [OTHER_LOCATION_ID]


async def test_demote_needs_locations(
    client: AsyncClient, manager_token: str, second_admin: User, other_location: Location,
) -> None:
    for body in ({"manager_type": "regular"}, {"manager_type": "regular", "location_ids": []}):
        resp = await client.put(
            f"{API}/managers/{SECOND_ADMIN_ID}", headers=_auth(manager_token), json=body,
        )
        assert resp.status_code == 400, (body, resp.text)


async def test_locations_must_be_active_and_in_company(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    regular_manager: User,
) -> None:
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    foreign_region = Region(id=_id(), company_id=COMPANY2_ID, name="Far")
    db_session.add(foreign_region)
    await db_session.flush()
    foreign = Location(
        id=_id(), company_id=COMPANY2_ID, region_id=foreign_region.id,
        name="Foreign", timezone="UTC",
    )
    db_session.add(foreign)
    other = await db_session.get(Location, OTHER_LOCATION_ID)
    other.archived_at = datetime.now(timezone.utc)
    await db_session.commit()

    for bad in (foreign.id, OTHER_LOCATION_ID, "nope1234"):
        resp = await client.put(
            f"{API}/managers/{REGULAR_MANAGER_ID}",
            headers=_auth(manager_token),
            json={"location_ids": [LOCATION_ID, bad]},
        )
        assert resp.status_code == 400, (bad, resp.text)
    db_session.expire_all()
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == {LOCATION_ID}


async def test_replace_locations(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, regular_manager: User,
) -> None:
    resp = await client.put(
        f"{API}/managers/{REGULAR_MANAGER_ID}",
        headers=_auth(manager_token),
        json={"location_ids": [OTHER_LOCATION_ID]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["manager_type"] == "regular"
    db_session.expire_all()
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == {OTHER_LOCATION_ID}

    resp = await client.put(
        f"{API}/managers/{REGULAR_MANAGER_ID}",
        headers=_auth(manager_token),
        json={"location_ids": [LOCATION_ID, OTHER_LOCATION_ID]},
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == {LOCATION_ID, OTHER_LOCATION_ID}

    # An empty replacement would leave a regular manager with nothing.
    resp = await client.put(
        f"{API}/managers/{REGULAR_MANAGER_ID}",
        headers=_auth(manager_token),
        json={"location_ids": []},
    )
    assert resp.status_code == 400


async def test_promote_regular_to_admin_drops_assignments(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, regular_manager: User,
) -> None:
    resp = await client.put(
        f"{API}/managers/{REGULAR_MANAGER_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "id": REGULAR_MANAGER_ID,
        "email": "regular-manager@test.com",
        "full_name": "Regular Manager",
        "manager_type": "admin",
        "assigned_locations": [],
    }
    db_session.expire_all()
    assert (await db_session.get(User, REGULAR_MANAGER_ID)).manager_type == "admin"
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == set()


async def test_admin_with_location_ids_is_400(
    client: AsyncClient, manager_token: str, regular_manager: User,
) -> None:
    resp = await client.put(
        f"{API}/managers/{REGULAR_MANAGER_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "admin", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 400


async def test_last_admin_cannot_be_demoted(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, regular_manager: User,
) -> None:
    # The seeded admin is the company's only admin: demoting themselves is refused.
    resp = await client.put(
        f"{API}/managers/{MANAGER_USER_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "regular", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 409, resp.text
    db_session.expire_all()
    assert (await db_session.get(User, MANAGER_USER_ID)).manager_type == "admin"
    assert await _locations_of(db_session, MANAGER_USER_ID) == set()


async def test_second_to_last_admin_then_last_admin(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    second_admin: User, seed_location: Location,
) -> None:
    # Two admins: one may demote the other (or themselves) ...
    resp = await client.put(
        f"{API}/managers/{MANAGER_USER_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "regular", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 200, resp.text
    # ... after which the remaining admin is the last one.
    second_token = _make_token(SECOND_ADMIN_ID, COMPANY_ID, "manager")
    resp = await client.put(
        f"{API}/managers/{SECOND_ADMIN_ID}",
        headers=_auth(second_token),
        json={"manager_type": "regular", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 409, resp.text


async def test_legacy_null_manager_counts_as_admin(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, seed_location: Location,
) -> None:
    db_session.add(User(
        id=_id(), company_id=COMPANY_ID, email="legacy@test.com",
        hashed_password="x", user_role="manager", manager_type=None,
    ))
    await db_session.commit()
    resp = await client.put(
        f"{API}/managers/{MANAGER_USER_ID}",
        headers=_auth(manager_token),
        json={"manager_type": "regular", "location_ids": [LOCATION_ID]},
    )
    assert resp.status_code == 200, resp.text


async def test_update_other_company_or_non_manager_is_404(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    seed_employee_user: User, seed_location: Location,
) -> None:
    db_session.add(Company(id=COMPANY2_ID, name="Other Co", slug="other-co"))
    await db_session.flush()
    foreign_id = _id()
    db_session.add(User(
        id=foreign_id, company_id=COMPANY2_ID, email="elsewhere@test.com",
        hashed_password="x", user_role="manager", manager_type="admin",
    ))
    await db_session.commit()
    for target in (foreign_id, seed_employee_user.id, "nope1234"):
        resp = await client.put(
            f"{API}/managers/{target}",
            headers=_auth(manager_token),
            json={"manager_type": "admin"},
        )
        assert resp.status_code == 404, (target, resp.text)


# --------------------------------------------------------------------------
# Manager invites (spec Q5)
# --------------------------------------------------------------------------

async def test_regular_invite_round_trip(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    with_og: OwnershipGroup, other_location: Location,
) -> None:
    resp = await client.post(
        f"{API}/manager-invites/",
        headers=_auth(manager_token),
        json={
            "email": "scoped@acme.test",
            "manager_type": "regular",
            "location_ids": [OTHER_LOCATION_ID],
        },
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["manager_type"] == "regular"
    assert created["location_ids"] == [OTHER_LOCATION_ID]

    listed = await client.get(f"{API}/manager-invites/", headers=_auth(manager_token))
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["id"] == created["id"])
    assert row["manager_type"] == "regular"
    assert row["location_ids"] == [OTHER_LOCATION_ID]

    accepted = await client.post(
        f"{API}/manager-invites/accept",
        json={
            "token": created["token"],
            "company_id": COMPANY_ID,
            "full_name": "Scoped Manager",
            "password": "supersecret",
        },
    )
    assert accepted.status_code == 200, accepted.text
    token = accepted.json()["access_token"]

    me = await client.get(f"{API}/auth/me", headers=_auth(token))
    assert me.status_code == 200, me.text
    assert me.json()["manager_type"] == "regular"
    assert me.json()["accessible_location_ids"] == [OTHER_LOCATION_ID]


async def test_admin_invite_default(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, with_og: OwnershipGroup,
) -> None:
    resp = await client.post(
        f"{API}/manager-invites/",
        headers=_auth(manager_token),
        json={"email": "boss@acme.test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["manager_type"] == "admin"
    accepted = await client.post(
        f"{API}/manager-invites/accept",
        json={
            "token": resp.json()["token"], "company_id": COMPANY_ID,
            "full_name": "Boss", "password": "supersecret",
        },
    )
    assert accepted.status_code == 200
    me = await client.get(f"{API}/auth/me", headers=_auth(accepted.json()["access_token"]))
    assert me.json()["manager_type"] == "admin"
    assert me.json()["accessible_location_ids"] is None


async def test_regular_invite_validation(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    with_og: OwnershipGroup, other_location: Location,
) -> None:
    other_location.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    for body in (
        {"email": "a@acme.test", "manager_type": "regular"},
        {"email": "a@acme.test", "manager_type": "regular", "location_ids": []},
        {"email": "a@acme.test", "manager_type": "regular", "location_ids": ["nope1234"]},
        {"email": "a@acme.test", "manager_type": "regular", "location_ids": [OTHER_LOCATION_ID]},
    ):
        resp = await client.post(f"{API}/manager-invites/", headers=_auth(manager_token), json=body)
        assert resp.status_code == 400, (body, resp.text)
    resp = await client.post(
        f"{API}/manager-invites/", headers=_auth(manager_token),
        json={"email": "a@acme.test", "manager_type": "boss"},
    )
    assert resp.status_code == 422


async def test_regular_invite_whose_locations_are_gone(
    client: AsyncClient, db_session: AsyncSession, manager_token: str,
    with_og: OwnershipGroup, seed_location: Location,
) -> None:
    """The invite's locations were removed before acceptance: the manager is
    created regular with no locations, and sees nothing until assigned."""
    doomed = Location(id=_id(), company_id=COMPANY_ID, region_id=REGION_ID,
                      name="Doomed", timezone="UTC")
    db_session.add(doomed)
    await db_session.commit()
    resp = await client.post(
        f"{API}/manager-invites/", headers=_auth(manager_token),
        json={"email": "late@acme.test", "manager_type": "regular", "location_ids": [doomed.id]},
    )
    assert resp.status_code == 200, resp.text
    await db_session.delete(doomed)
    await db_session.commit()

    accepted = await client.post(
        f"{API}/manager-invites/accept",
        json={"token": resp.json()["token"], "company_id": COMPANY_ID,
              "full_name": "Late", "password": "supersecret"},
    )
    assert accepted.status_code == 200, accepted.text
    me = await client.get(f"{API}/auth/me", headers=_auth(accepted.json()["access_token"]))
    assert me.json()["manager_type"] == "regular"
    assert me.json()["accessible_location_ids"] == []


# --------------------------------------------------------------------------
# Admin only
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/managers/", None),
        ("PUT", f"/managers/{MANAGER_USER_ID}", {"manager_type": "regular", "location_ids": [LOCATION_ID]}),
        ("PUT", f"/managers/{REGULAR_MANAGER_ID}", {"location_ids": [LOCATION_ID, OTHER_LOCATION_ID]}),
        ("POST", f"/managers/{REGULAR_MANAGER_ID}/locations/{OTHER_LOCATION_ID}", None),
        ("DELETE", f"/managers/{REGULAR_MANAGER_ID}/locations/{LOCATION_ID}", None),
        ("POST", "/manager-invites/", {"email": "x@acme.test"}),
        ("GET", "/manager-invites/", None),
    ],
)
async def test_regular_manager_gets_403(
    client: AsyncClient, db_session: AsyncSession, regular_token: str, with_og: OwnershipGroup,
    method: str, path: str, body: dict | None,
) -> None:
    resp = await client.request(method, f"{API}{path}", headers=_auth(regular_token), json=body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Admin access required"
    db_session.expire_all()
    assert (await db_session.get(User, MANAGER_USER_ID)).manager_type == "admin"
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == {LOCATION_ID}


async def test_admin_assign_and_revoke_still_work(
    client: AsyncClient, db_session: AsyncSession, manager_token: str, regular_manager: User,
) -> None:
    resp = await client.post(
        f"{API}/managers/{REGULAR_MANAGER_ID}/locations/{OTHER_LOCATION_ID}",
        headers=_auth(manager_token),
    )
    assert resp.status_code == 200, resp.text
    # Assigning twice is idempotent, not a unique-constraint 500.
    resp = await client.post(
        f"{API}/managers/{REGULAR_MANAGER_ID}/locations/{OTHER_LOCATION_ID}",
        headers=_auth(manager_token),
    )
    assert resp.status_code == 200, resp.text
    resp = await client.delete(
        f"{API}/managers/{REGULAR_MANAGER_ID}/locations/{LOCATION_ID}",
        headers=_auth(manager_token),
    )
    assert resp.status_code == 204
    db_session.expire_all()
    assert await _locations_of(db_session, REGULAR_MANAGER_ID) == {OTHER_LOCATION_ID}
