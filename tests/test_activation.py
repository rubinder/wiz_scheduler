"""Activation funnel: signup -> first_location -> first_employee ->
first_generation -> upgraded, recorded once per ownership group.

Companion to test_signup_signals.py and test_abuse_report.py, which cover
the observe-only signup signals and the report built on them; this file
covers the funnel milestones in backend/services/activation.py, the five
call sites, and the cohort report in backend/services/activation_report.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Company, Employee, Location, Region, User
from backend.models.activation_event import ActivationEvent
from backend.models.ownership_group import OwnershipGroup
from backend.services.activation import record_milestone
from backend.services.activation_report import build_activation_report
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def og_tenant(db_session: AsyncSession) -> dict:
    """A free ownership group with one Region, a manager User, and a
    manager JWT — enough to hit /locations, /employees and
    /schedules/generate as an authenticated manager.

    Deliberately does NOT pre-create a Location: the free plan allows only
    FREE_PLAN_MAX_LOCATIONS (2), and the location-milestone tests below
    each create two locations of their own to prove idempotency, which
    would exceed the cap if one already existed. `location_id` is a bare
    id used only as the LocationResult.location_id in the mocked-pipeline
    generation tests — nothing there requires a real Location row.
    """
    og_id, company_id, region_id, location_id, user_id = (
        _id(), _id(), _id(), _id(), _id()
    )
    db_session.add(OwnershipGroup(id=og_id, name="Activation Co"))
    await db_session.flush()
    db_session.add(Company(
        id=company_id, name="Activation Co", slug=_id(),
        ownership_group_id=og_id,
    ))
    await db_session.flush()
    db_session.add(Region(id=region_id, company_id=company_id, name="R"))
    await db_session.flush()
    db_session.add(User(
        id=user_id, company_id=company_id, email="m@activation.test",
        hashed_password="x", full_name="Manager", user_role="manager",
        email_verified_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()
    return {
        "og_id": og_id,
        "company_id": company_id,
        "region_id": region_id,
        "location_id": location_id,
        "user_id": user_id,
        "token": _make_token(user_id, company_id, "manager"),
    }


async def _events(db: AsyncSession, og_id: str) -> list[ActivationEvent]:
    return (await db.execute(
        select(ActivationEvent).where(ActivationEvent.ownership_group_id == og_id)
    )).scalars().all()


def _register_body(email: str, company: str) -> dict:
    return {
        "email": email,
        "password": "secret123",
        "full_name": "Activation User",
        "company_name": company,
        "privacy_accepted": True,
        "terms_accepted": True,
    }


# ---------------------------------------------------------------------------
# services/activation.py — the service directly
# ---------------------------------------------------------------------------

async def test_record_milestone_idempotent(db_session: AsyncSession, og_tenant: dict):
    """A repeated call for the same (group, event) adds nothing."""
    await record_milestone(db_session, og_tenant["og_id"], "signup")
    await record_milestone(db_session, og_tenant["og_id"], "signup")
    await record_milestone(db_session, og_tenant["og_id"], "signup")

    rows = await _events(db_session, og_tenant["og_id"])
    assert len(rows) == 1
    assert rows[0].event == "signup"


async def test_record_milestone_distinct_events_each_recorded(
    db_session: AsyncSession, og_tenant: dict
):
    await record_milestone(db_session, og_tenant["og_id"], "signup")
    await record_milestone(db_session, og_tenant["og_id"], "first_location")

    rows = await _events(db_session, og_tenant["og_id"])
    assert {r.event for r in rows} == {"signup", "first_location"}


async def test_record_milestone_stamps_user_id(
    db_session: AsyncSession, og_tenant: dict
):
    await record_milestone(
        db_session, og_tenant["og_id"], "signup", user_id=og_tenant["user_id"]
    )
    rows = await _events(db_session, og_tenant["og_id"])
    assert rows[0].user_id == og_tenant["user_id"]


async def test_record_milestone_no_ownership_group_is_noop(db_session: AsyncSession):
    """No ownership group (seed/dev data) — nothing to attribute this to,
    and definitely not an error."""
    await record_milestone(db_session, None, "signup")
    await record_milestone(db_session, "", "signup")
    # No exception, and nothing written anywhere to check against a
    # nonexistent group — the absence of a raise is the assertion.


async def test_record_milestone_unknown_event_is_noop(
    db_session: AsyncSession, og_tenant: dict
):
    await record_milestone(db_session, og_tenant["og_id"], "not_a_real_event")
    rows = await _events(db_session, og_tenant["og_id"])
    assert rows == []


async def test_record_milestone_swallows_db_failure(
    db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """A failing insert must not raise into the caller, and must not leave
    a partial row behind.

    record_milestone runs on its own private AsyncSession (never the
    caller's), so the injected failure targets that class of session
    generally — nothing in this test calls db_session.commit() while the
    patch is active, so db_session itself is unaffected.
    """
    from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession

    async def boom(self):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(SAAsyncSession, "commit", boom)

    # Must not raise.
    await record_milestone(db_session, og_tenant["og_id"], "signup")

    monkeypatch.undo()
    rows = await _events(db_session, og_tenant["og_id"])
    assert rows == []


# ---------------------------------------------------------------------------
# Call site 1: registration -> signup
# ---------------------------------------------------------------------------

async def test_register_records_signup(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/v1/auth/register", json=_register_body("act1@example.com", "ActCo1")
    )
    assert resp.status_code == 201

    og_id = (await db_session.execute(
        select(Company.ownership_group_id).where(Company.name == "ActCo1")
    )).scalar_one()

    rows = await _events(db_session, og_id)
    assert len(rows) == 1
    assert rows[0].event == "signup"
    assert rows[0].user_id is not None


async def test_register_twice_for_different_companies_each_get_one_signup_row(
    client: AsyncClient, db_session: AsyncSession
):
    """Two independent registrations are two independent ownership groups,
    each with exactly one signup row — not a shared/duplicated one."""
    assert (await client.post(
        "/api/v1/auth/register", json=_register_body("act2a@example.com", "ActCo2A")
    )).status_code == 201
    assert (await client.post(
        "/api/v1/auth/register", json=_register_body("act2b@example.com", "ActCo2B")
    )).status_code == 201

    og_a = (await db_session.execute(
        select(Company.ownership_group_id).where(Company.name == "ActCo2A")
    )).scalar_one()
    og_b = (await db_session.execute(
        select(Company.ownership_group_id).where(Company.name == "ActCo2B")
    )).scalar_one()

    assert len(await _events(db_session, og_a)) == 1
    assert len(await _events(db_session, og_b)) == 1


# ---------------------------------------------------------------------------
# Call site 2: create_location -> first_location
# ---------------------------------------------------------------------------

async def test_create_location_records_first_location(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={
            "region_id": og_tenant["region_id"],
            "name": "Second Store",
            "timezone": "America/New_York",
        },
    )
    assert resp.status_code == 201

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == ["first_location"]


async def test_create_location_twice_records_first_location_once(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    for name in ("Store A", "Store B"):
        resp = await client.post(
            "/api/v1/locations/",
            headers={"Authorization": f"Bearer {og_tenant['token']}"},
            json={
                "region_id": og_tenant["region_id"],
                "name": name,
                "timezone": "America/New_York",
            },
        )
        assert resp.status_code == 201

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_location"
    ]
    assert len(rows) == 1


async def test_first_location_failing_milestone_insert_does_not_fail_request(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """The location is created and the response succeeds even if the
    milestone insert's own (private-session) commit blows up.

    The patch lets db_session's own commit through untouched — it only
    fails commits from record_milestone's private session — so this
    exercises a real request end to end rather than mocking the endpoint.
    """
    from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession

    original_commit = SAAsyncSession.commit

    async def flaky_commit(self):
        if self is db_session:
            return await original_commit(self)
        raise RuntimeError("boom")

    monkeypatch.setattr(SAAsyncSession, "commit", flaky_commit)

    resp = await client.post(
        "/api/v1/locations/",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={
            "region_id": og_tenant["region_id"],
            "name": "Resilient Store",
            "timezone": "America/New_York",
        },
    )
    assert resp.status_code == 201

    monkeypatch.undo()
    rows = await _events(db_session, og_tenant["og_id"])
    assert rows == []  # the milestone insert failed; the location did not


# ---------------------------------------------------------------------------
# Call site 3: create_employee -> first_employee
# ---------------------------------------------------------------------------

async def test_create_employee_records_first_employee(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    resp = await client.post(
        "/api/v1/employees/",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"full_name": "Alice Worker"},
    )
    assert resp.status_code == 201

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == ["first_employee"]


async def test_bulk_upload_locations_records_first_location(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    csv_content = (
        "name,region_name,address,timezone\n"
        "Bulk Store,R,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_location"
    ]
    assert len(rows) == 1


async def test_bulk_upload_locations_all_skipped_records_nothing(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    """A CSV that creates nothing (every row skipped) must not record the
    milestone — it never actually reached first_location."""
    csv_content = (
        "name,region_name,address,timezone\n"
        ",R,,UTC\n"
    )
    resp = await client.post(
        "/api/v1/locations/bulk-upload",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        files={"file": ("locations.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 0

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == []


async def test_create_employee_twice_records_first_employee_once(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    for name in ("Alice", "Bob"):
        resp = await client.post(
            "/api/v1/employees/",
            headers={"Authorization": f"Bearer {og_tenant['token']}"},
            json={"full_name": name},
        )
        assert resp.status_code == 201

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_employee"
    ]
    assert len(rows) == 1


async def test_bulk_upload_employees_records_first_employee(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    csv_content = "full_name,email,role_names,skill_levels,location_names\nBulk Alice,,,,\n"
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        files={"file": ("employees.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_employee"
    ]
    assert len(rows) == 1


async def test_bulk_upload_employees_all_skipped_records_nothing(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict
):
    """A CSV that creates nothing (every row skipped) must not record the
    milestone — it never actually reached first_employee."""
    csv_content = "full_name,email,role_names,skill_levels,location_names\n,,,,\n"
    resp = await client.post(
        "/api/v1/employees/bulk-upload",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        files={"file": ("employees.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 0

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == []


# ---------------------------------------------------------------------------
# Call site 4: POST /schedules/generate -> first_generation
# ---------------------------------------------------------------------------

def _location_result(status: str, location_id: str) -> dict:
    return {
        "location_id": location_id,
        "location_name": "HQ",
        "shifts": [],
        "errors": [] if status == "ok" else [f"{status} for location {location_id}"],
        "status": status,
    }


async def test_generate_does_not_record_on_parse_error(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    async def fake_pipeline(**kwargs):
        yield _location_result("PARSE_ERROR", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"week_start_date": "2026-10-05", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == []


async def test_generate_does_not_record_on_conflict(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    async def fake_pipeline(**kwargs):
        yield _location_result("CONFLICT", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"week_start_date": "2026-10-05", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == []


async def test_generate_does_not_record_on_quota_exceeded(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """QUOTA_EXCEEDED means nothing was generated for that location — it's
    exactly the stalled-cohort case the funnel exists to measure, so it
    must not count as reaching first_generation."""
    async def fake_pipeline(**kwargs):
        yield _location_result("QUOTA_EXCEEDED", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"week_start_date": "2026-10-05", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = await _events(db_session, og_tenant["og_id"])
    assert [r.event for r in rows] == []


async def test_generate_records_first_generation_on_ok_result(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    async def fake_pipeline(**kwargs):
        yield _location_result("ok", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"week_start_date": "2026-10-05", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_generation"
    ]
    assert len(rows) == 1


async def test_generate_error_then_ok_records_exactly_once(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """A PARSE_ERROR result followed by an ok result in the same run
    records the milestone once, from the ok result."""
    async def fake_pipeline(**kwargs):
        yield _location_result("PARSE_ERROR", og_tenant["location_id"])
        yield _location_result("ok", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
        json={"week_start_date": "2026-10-05", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_generation"
    ]
    assert len(rows) == 1


async def test_generate_twice_records_first_generation_once(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    async def fake_pipeline(**kwargs):
        yield _location_result("ok", og_tenant["location_id"])

    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    for _ in range(2):
        resp = await client.post(
            "/api/v1/schedules/generate",
            headers={"Authorization": f"Bearer {og_tenant['token']}"},
            json={"week_start_date": "2026-10-05", "use_local": True},
        )
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "first_generation"
    ]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Call site 5: POST /billing/confirm-upgrade -> upgraded
# ---------------------------------------------------------------------------

async def test_confirm_upgrade_records_upgraded(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_act_1",
        subscription="sub_act_1",
        client_reference_id=str(og_tenant["og_id"]),
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/billing/confirm-upgrade",
        json={"session_id": "cs_test_act_1"},
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["upgraded"] is True

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "upgraded"
    ]
    assert len(rows) == 1
    assert rows[0].user_id == og_tenant["user_id"]


async def test_confirm_upgrade_twice_records_upgraded_once(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """Calling confirm-upgrade twice for the same group still records the
    milestone only once — the row marks reaching paid at all, not each
    individual Checkout confirmation."""
    import stripe
    from backend.config import settings as _s

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_act_2",
        subscription="sub_act_2",
        client_reference_id=str(og_tenant["og_id"]),
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    for _ in range(2):
        resp = await client.post(
            "/api/v1/billing/confirm-upgrade",
            json={"session_id": "cs_test_act_2"},
            headers={"Authorization": f"Bearer {og_tenant['token']}"},
        )
        assert resp.status_code == 200

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "upgraded"
    ]
    assert len(rows) == 1


async def test_confirm_reactivation_records_upgraded(
    client: AsyncClient, db_session: AsyncSession, og_tenant: dict, monkeypatch
):
    """Reactivating a canceled subscription is also a "this group is paid"
    transition, and must record the same milestone as a fresh upgrade."""
    import stripe

    from backend.config import settings as _s
    from backend.models.ownership_group import OwnershipGroup

    monkeypatch.setattr(_s, "STRIPE_SECRET_KEY", "sk_test_x")

    og = await db_session.get(OwnershipGroup, og_tenant["og_id"])
    og.stripe_customer_id = "cus_react_1"
    og.canceled_at = datetime.now(timezone.utc)
    await db_session.commit()

    fake_session = MagicMock(
        payment_status="paid",
        customer="cus_react_1",
        subscription="sub_react_1",
    )
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: fake_session)

    resp = await client.post(
        "/api/v1/billing/confirm-reactivation",
        json={"session_id": "cs_test_react_1"},
        headers={"Authorization": f"Bearer {og_tenant['token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["reactivated"] is True

    rows = [
        r for r in await _events(db_session, og_tenant["og_id"])
        if r.event == "upgraded"
    ]
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# backend/services/activation_report.py — cohort math
# ---------------------------------------------------------------------------

async def _og_with_events(
    db: AsyncSession,
    *,
    signup_age_hours: float,
    events: dict[str, float] | None = None,
) -> str:
    """An ownership group whose signup happened *signup_age_hours* ago, with
    activation_events for each {event: hours_after_signup} in *events*."""
    og_id = _id()
    signup_time = datetime.now(timezone.utc) - timedelta(hours=signup_age_hours)
    db.add(OwnershipGroup(id=og_id, name=f"Cohort-{og_id}", created_at=signup_time))
    await db.flush()
    db.add(ActivationEvent(
        id=_id(), ownership_group_id=og_id, event="signup", occurred_at=signup_time,
    ))
    for event, hours_after in (events or {}).items():
        db.add(ActivationEvent(
            id=_id(), ownership_group_id=og_id, event=event,
            occurred_at=signup_time + timedelta(hours=hours_after),
        ))
    await db.commit()
    return og_id


async def test_report_counts_and_percentages_for_current_week_cohort(
    db_session: AsyncSession,
):
    # 3 signups this week; 2 reach first_location, 1 reaches upgraded.
    await _og_with_events(db_session, signup_age_hours=10, events={"first_location": 2})
    await _og_with_events(
        db_session, signup_age_hours=20,
        events={"first_location": 4, "upgraded": 40},
    )
    await _og_with_events(db_session, signup_age_hours=5)

    report = await build_activation_report(db_session, weeks=1)
    cohort = report["cohorts"][0]

    assert cohort["signups"] == 3
    assert cohort["milestones"]["first_location"]["count"] == 2
    assert cohort["milestones"]["first_location"]["pct"] == pytest.approx(66.7, abs=0.1)
    assert cohort["milestones"]["upgraded"]["count"] == 1
    assert cohort["milestones"]["upgraded"]["pct"] == pytest.approx(33.3, abs=0.1)


async def test_report_median_hours_from_signup(db_session: AsyncSession):
    await _og_with_events(db_session, signup_age_hours=100, events={"first_employee": 2})
    await _og_with_events(db_session, signup_age_hours=100, events={"first_employee": 4})
    await _og_with_events(db_session, signup_age_hours=100, events={"first_employee": 6})

    report = await build_activation_report(db_session, weeks=1)
    cohort = report["cohorts"][0]

    assert cohort["milestones"]["first_employee"]["median_hours"] == pytest.approx(4.0)


async def test_report_milestone_never_reached_has_no_median(db_session: AsyncSession):
    await _og_with_events(db_session, signup_age_hours=10)

    report = await build_activation_report(db_session, weeks=1)
    cohort = report["cohorts"][0]

    assert cohort["milestones"]["first_generation"]["count"] == 0
    assert cohort["milestones"]["first_generation"]["median_hours"] is None


async def test_report_older_cohort_excluded_from_more_recent_week(
    db_session: AsyncSession,
):
    """A group that signed up 3 weeks ago must not appear in this week's
    cohort bucket."""
    await _og_with_events(db_session, signup_age_hours=24 * 21)

    report = await build_activation_report(db_session, weeks=1)
    assert report["cohorts"][0]["signups"] == 0


async def test_report_separates_signups_into_correct_weekly_buckets(
    db_session: AsyncSession,
):
    await _og_with_events(db_session, signup_age_hours=10)       # this week
    await _og_with_events(db_session, signup_age_hours=24 * 10)  # 2 weeks ago

    report = await build_activation_report(db_session, weeks=3)

    assert report["cohorts"][0]["signups"] == 1   # most recent week
    assert report["cohorts"][1]["signups"] == 1   # the 2-weeks-ago bucket
    assert report["cohorts"][2]["signups"] == 0


async def test_report_empty_cohort_reports_zero_not_a_crash(db_session: AsyncSession):
    report = await build_activation_report(db_session, weeks=2)
    for cohort in report["cohorts"]:
        assert cohort["signups"] == 0
        for stats in cohort["milestones"].values():
            assert stats == {"count": 0, "pct": 0.0, "median_hours": None}
