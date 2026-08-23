"""The public demo tenant's fixed roster.

The demo is one tenant shown to every visitor at once. Its roster is seeded to
a known shape — 21 employees, each available 9-5 every day — and the shift
template and the landing flow assume it. A visitor deleting the staff would
leave the demo broken for everyone arriving after, with nothing to restore it
but a manual re-seed.

So adds and removes are refused for the demo ownership group, while EDITS stay
open: changing an employee, their roles or their availability is the part a
visitor is meant to try, and it heals on the next seed run.

This is a 403 with its own code, not a 402 plan limit — paying does not unlock
the demo tenant, so "upgrade to add more" would be a lie.
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, Employee, Location, Region
from backend.models.ownership_group import OwnershipGroup
from backend.services.plan import assert_roster_editable
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def demo_company(db_session: AsyncSession) -> str:
    """A company inside the ownership group settings names as the demo."""
    og_id = settings.DEMO_OWNERSHIP_GROUP_ID
    company_id = _id()
    db_session.add(OwnershipGroup(id=og_id, name="Demo Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Demo Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return company_id


@pytest_asyncio.fixture
async def plain_company(db_session: AsyncSession) -> str:
    """An ordinary free company, for contrast."""
    og_id, company_id = _id(), _id()
    db_session.add(OwnershipGroup(id=og_id, name="Plain Group"))
    await db_session.flush()
    db_session.add(Company(id=company_id, name="Plain Co", slug=_id(),
                           ownership_group_id=og_id))
    await db_session.commit()
    return company_id


# --- the guard itself -------------------------------------------------------


async def test_demo_group_roster_is_locked(
    db_session: AsyncSession, demo_company: str
):
    with pytest.raises(HTTPException) as exc:
        await assert_roster_editable(db_session, demo_company)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "demo_roster_locked"


async def test_ordinary_free_group_is_not_locked(
    db_session: AsyncSession, plain_company: str
):
    """The lock keys on the demo group, not on being free."""
    await assert_roster_editable(db_session, plain_company)  # does not raise


async def test_company_without_ownership_group_is_not_locked(
    db_session: AsyncSession
):
    """Seed/dev data with no group stays ungated, as everywhere else."""
    company_id = _id()
    db_session.add(Company(id=company_id, name="Loose Co", slug=_id()))
    await db_session.commit()

    await assert_roster_editable(db_session, company_id)  # does not raise


async def test_lock_turns_off_with_the_demo_group_id(
    db_session: AsyncSession, demo_company: str, monkeypatch
):
    """Setting DEMO_OWNERSHIP_GROUP_ID to "" disables every demo exception."""
    monkeypatch.setattr(settings, "DEMO_OWNERSHIP_GROUP_ID", "")

    await assert_roster_editable(db_session, demo_company)  # does not raise


# --- what the API does with it ---------------------------------------------


async def _login(db: AsyncSession, company_id: str) -> dict:
    """A manager token for *company_id*."""
    from backend.models import User
    from tests.conftest import _make_token

    user_id = _id()
    db.add(User(
        id=user_id, company_id=company_id, email=f"{_id()}@example.com",
        hashed_password="x", full_name="Demo Manager", user_role="manager",
    ))
    await db.commit()
    return {"Authorization": f"Bearer {_make_token(user_id, company_id, 'manager')}"}


async def test_api_refuses_to_create_an_employee_on_the_demo(
    client, db_session: AsyncSession, demo_company: str
):
    headers = await _login(db_session, demo_company)

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "Walk In", "email": "walk.in@example.com",
              "location_ids": []},
        headers=headers,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "demo_roster_locked"


async def test_api_refuses_to_delete_an_employee_on_the_demo(
    client, db_session: AsyncSession, demo_company: str
):
    emp_id = _id()
    db_session.add(Employee(
        id=emp_id, company_id=demo_company, full_name="Alice Johnson",
        email="alice.johnson@example.com", location_ids=[],
    ))
    await db_session.commit()
    headers = await _login(db_session, demo_company)

    resp = await client.delete(f"/api/v1/employees/{emp_id}", headers=headers)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "demo_roster_locked"

    # And the employee is still there.
    assert await db_session.get(Employee, emp_id) is not None


async def test_demo_delete_lock_covers_employees_without_a_user(
    client, db_session: AsyncSession, demo_company: str
):
    """The check this replaced only fired when employee.user_id was set, which
    left every seeded employee but the first one deletable."""
    emp_id = _id()
    db_session.add(Employee(
        id=emp_id, company_id=demo_company, full_name="Bob Smith",
        email="bob.smith@example.com", location_ids=[], user_id=None,
    ))
    await db_session.commit()
    headers = await _login(db_session, demo_company)

    resp = await client.delete(f"/api/v1/employees/{emp_id}", headers=headers)

    assert resp.status_code == 403
    assert await db_session.get(Employee, emp_id) is not None


async def test_api_refuses_a_bulk_upload_on_the_demo(
    client, db_session: AsyncSession, demo_company: str
):
    headers = await _login(db_session, demo_company)

    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        files={"file": ("roster.csv",
                        "full_name,email\nWalk In,walk.in@example.com\n",
                        "text/csv")},
        headers=headers,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "demo_roster_locked"


async def test_editing_an_existing_demo_employee_still_works(
    client, db_session: AsyncSession, demo_company: str
):
    """The lock is scoped to add and remove. Editing is the demo's whole point."""
    emp_id = _id()
    db_session.add(Employee(
        id=emp_id, company_id=demo_company, full_name="Carol Davis",
        email="carol.davis@example.com", location_ids=[],
    ))
    await db_session.commit()
    headers = await _login(db_session, demo_company)

    resp = await client.put(
        f"/api/v1/employees/{emp_id}",
        json={"full_name": "Carol Davis-Renamed",
              "email": "carol.davis@example.com", "location_ids": []},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text


async def test_an_ordinary_free_company_can_still_add_employees(
    client, db_session: AsyncSession, plain_company: str
):
    """Guarding against the lock leaking onto every free tenant."""
    headers = await _login(db_session, plain_company)

    resp = await client.post(
        "/api/v1/employees/",
        json={"full_name": "New Hire", "email": "new.hire@example.com",
              "location_ids": []},
        headers=headers,
    )

    assert resp.status_code == 201, resp.text


# --- what the UI is told ----------------------------------------------------
#
# The frontend hides these controls with <DemoGuard>, which keys on the
# `is_demo` flag from /auth/me. That flag and assert_roster_editable must
# agree: if the UI thinks a tenant is ordinary, it renders live buttons that
# the server then rejects with a 403.


async def test_auth_me_marks_the_demo_company(
    client, db_session: AsyncSession, demo_company: str
):
    headers = await _login(db_session, demo_company)

    resp = await client.get("/api/v1/auth/me", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["is_demo"] is True


async def test_auth_me_does_not_mark_an_ordinary_company(
    client, db_session: AsyncSession, plain_company: str
):
    headers = await _login(db_session, plain_company)

    resp = await client.get("/api/v1/auth/me", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["is_demo"] is False


async def test_is_demo_agrees_with_the_roster_lock(
    client, db_session: AsyncSession, demo_company: str, plain_company: str
):
    """The two must never disagree — that is what puts a live button in front
    of a guaranteed 403."""
    for company_id in (demo_company, plain_company):
        headers = await _login(db_session, company_id)
        flagged = (await client.get(
            "/api/v1/auth/me", headers=headers
        )).json()["is_demo"]

        try:
            await assert_roster_editable(db_session, company_id)
            locked = False
        except HTTPException:
            locked = True

        assert flagged is locked
