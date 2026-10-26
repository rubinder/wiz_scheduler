"""Admin-only routes and scoped location / export reads (#149, #150).

A regular (location-scoped) manager is refused company settings, billing,
imports, payroll and failure logs with 403 "Admin access required"; the
admin keeps the old behavior. Locations and approved-schedule exports are
filtered to the regular manager's assigned locations, and an out-of-scope
location or schedule answers 404 like another company's row.
"""

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location, Shift, ShiftSchedule, User
from tests.conftest import COMPANY_ID, LOCATION_ID, OTHER_LOCATION_ID, ROLE_FLOOR_ID, _id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
WEEK = date(2026, 8, 31)
BOGUS = str(uuid.uuid4())


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# (method, path, json body). Bodies are deliberately minimal: the regular
# manager must be refused before validation, and the admin only has to get
# past the permission check (a 404 / 422 / 402 is fine, a 403 is not).
ADMIN_ONLY: list[tuple[str, str, dict | None]] = [
    # company / ownership group writes
    ("PUT", "/company/", {}),
    ("PUT", "/ownership-group/", {}),
    ("POST", "/ownership-group/companies", {}),
    # regions writes
    ("POST", "/regions/", {}),
    ("PUT", f"/regions/{BOGUS}", {}),
    ("DELETE", f"/regions/{BOGUS}", None),
    # roles writes
    ("POST", "/roles/", {}),
    ("PUT", f"/roles/{BOGUS}", {}),
    ("POST", "/roles/bulk-upload", None),
    ("DELETE", f"/roles/{BOGUS}", None),
    ("POST", f"/roles/{BOGUS}/restore", None),
    # condensed roles writes
    ("POST", "/condensed-roles/", {}),
    ("PUT", f"/condensed-roles/{BOGUS}", {}),
    ("DELETE", f"/condensed-roles/{BOGUS}", None),
    # billing, except GET /plan
    ("GET", "/billing/usage", None),
    ("GET", "/billing/autoreload", None),
    ("PUT", "/billing/autoreload", {"enabled": "not-a-bool"}),
    ("POST", "/billing/autoreload/retry", None),
    ("POST", "/billing/credits/purchase", {}),
    ("GET", "/billing/charges", None),
    ("GET", "/billing/portal-link", None),
    ("POST", "/billing/reactivate-checkout", None),
    ("POST", "/billing/confirm-reactivation", {}),
    ("POST", "/billing/upgrade-checkout", None),
    ("POST", "/billing/confirm-upgrade", {}),
    # importers
    ("POST", "/import/7shifts", {}),
    ("POST", "/import/7shifts/availabilities", {}),
    ("POST", "/import/deputy", None),
    ("POST", "/import/deputy/availabilities", None),
    # payroll (whole router)
    ("POST", "/payroll/entries/derive", {}),
    ("GET", "/payroll/entries", None),
    ("GET", "/payroll/exceptions", None),
    ("POST", "/payroll/attest", {}),
    ("POST", "/payroll/approve", {}),
    ("POST", "/payroll/export", {}),
    # failure logs
    ("GET", "/failure-logs", None),
    # locations: create / bulk upload / archive / restore
    ("POST", "/locations/", {}),
    ("POST", "/locations/bulk-upload", None),
    ("DELETE", f"/locations/{LOCATION_ID}", None),
    ("POST", f"/locations/{LOCATION_ID}/restore", None),
    # export push to 7shifts
    ("POST", "/export/7shifts", {}),
]

# Company-wide reads ordinary pages need (ruling R4): open to both.
OPEN_READS: list[str] = [
    "/company/",
    "/company/all",
    "/ownership-group/",
    "/regions/",
    "/roles/",
    "/condensed-roles/",
    "/billing/plan",
]


async def _call(client: AsyncClient, method: str, path: str, body: dict | None, token: str):
    kwargs: dict = {"headers": _h(token)}
    if body is not None:
        kwargs["json"] = body
    return await client.request(method, f"/api/v1{path}", **kwargs)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=[f"{m} {p}" for m, p, _ in ADMIN_ONLY])
async def test_admin_only_endpoint_refuses_regular_manager(
    client: AsyncClient,
    manager_token: str,
    regular_token: str,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    regular = await _call(client, method, path, body, regular_token)
    assert regular.status_code == 403, regular.text
    assert regular.json()["detail"] == "Admin access required"

    # DELETE /locations archives the location; run the admin call last so
    # nothing above depends on it.
    admin = await _call(client, method, path, body, manager_token)
    assert admin.status_code != 403, admin.text


@pytest.mark.parametrize("path", OPEN_READS)
async def test_company_wide_reads_stay_open(
    client: AsyncClient, manager_token: str, regular_token: str, path: str
) -> None:
    # The regular manager gets exactly what the admin gets (the seed company
    # has no ownership group, so that read is a 404 for both).
    admin = await client.get(f"/api/v1{path}", headers=_h(manager_token))
    regular = await client.get(f"/api/v1{path}", headers=_h(regular_token))
    assert admin.status_code in (200, 404), admin.text
    assert (regular.status_code, regular.json()) == (admin.status_code, admin.json())


# ── locations ──


async def test_list_locations_is_scoped(
    client: AsyncClient, manager_token: str, regular_token: str
) -> None:
    admin = await client.get("/api/v1/locations/", headers=_h(manager_token))
    assert admin.status_code == 200
    assert {loc["id"] for loc in admin.json()} == {LOCATION_ID, OTHER_LOCATION_ID}

    regular = await client.get("/api/v1/locations/", headers=_h(regular_token))
    assert regular.status_code == 200
    assert [loc["id"] for loc in regular.json()] == [LOCATION_ID]


async def test_archived_assigned_location_listed_with_include_archived(
    client: AsyncClient, db_session: AsyncSession, regular_token: str, seed_location: Location
) -> None:
    seed_location.archived_at = datetime.now(timezone.utc)
    await db_session.commit()

    active_only = await client.get("/api/v1/locations/", headers=_h(regular_token))
    assert active_only.json() == []

    with_archived = await client.get(
        "/api/v1/locations/?include_archived=true", headers=_h(regular_token)
    )
    assert [loc["id"] for loc in with_archived.json()] == [LOCATION_ID]


async def test_update_location_in_scope_only(
    client: AsyncClient, manager_token: str, regular_token: str
) -> None:
    ok = await client.put(
        f"/api/v1/locations/{LOCATION_ID}", json={"name": "Renamed"}, headers=_h(regular_token)
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["name"] == "Renamed"

    out = await client.put(
        f"/api/v1/locations/{OTHER_LOCATION_ID}", json={"name": "Nope"}, headers=_h(regular_token)
    )
    assert out.status_code == 404
    assert out.json()["detail"] == "Location not found"

    admin = await client.put(
        f"/api/v1/locations/{OTHER_LOCATION_ID}", json={"name": "Admin Rename"},
        headers=_h(manager_token),
    )
    assert admin.status_code == 200
    assert admin.json()["name"] == "Admin Rename"


# ── approved-schedule exports ──


@pytest_asyncio.fixture
async def approved_weeks(
    db_session: AsyncSession, regular_manager: User, seed_roles: list
) -> dict[str, str]:
    """One approved schedule per location, same week, one shift each."""
    ids: dict[str, str] = {}
    for loc in (LOCATION_ID, OTHER_LOCATION_ID):
        sid = _id()
        ids[loc] = sid
        db_session.add(ShiftSchedule(
            id=sid, company_id=COMPANY_ID, location_id=loc,
            week_start_date=WEEK, status="approved",
            created_at=datetime.now(timezone.utc),
        ))
        await db_session.flush()
        db_session.add(Shift(
            id=_id(), company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=loc, employee_id=None, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=WEEK,
            start_time=datetime(2026, 8, 31, 9, tzinfo=NY),
            end_time=datetime(2026, 8, 31, 17, tzinfo=NY),
        ))
    await db_session.commit()
    return ids


async def test_approved_schedules_are_scoped(
    client: AsyncClient, manager_token: str, regular_token: str, approved_weeks: dict[str, str]
) -> None:
    url = f"/api/v1/export/approved-schedules?week_start_date={WEEK.isoformat()}"
    admin = await client.get(url, headers=_h(manager_token))
    assert admin.status_code == 200
    assert {s["location_id"] for s in admin.json()} == {LOCATION_ID, OTHER_LOCATION_ID}

    regular = await client.get(url, headers=_h(regular_token))
    assert regular.status_code == 200
    assert [s["location_id"] for s in regular.json()] == [LOCATION_ID]


async def test_approved_dates_are_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
    manager_token: str,
    regular_token: str,
    approved_weeks: dict[str, str],
) -> None:
    # Move the out-of-scope schedule to its own week so the dates differ.
    other_week = date(2026, 9, 7)
    sched = await db_session.get(ShiftSchedule, approved_weeks[OTHER_LOCATION_ID])
    sched.week_start_date = other_week
    await db_session.commit()

    url = f"/api/v1/export/approved-dates?anchor={WEEK.isoformat()}"
    admin = await client.get(url, headers=_h(manager_token))
    assert admin.json() == [WEEK.isoformat(), other_week.isoformat()]

    regular = await client.get(url, headers=_h(regular_token))
    assert regular.status_code == 200
    assert regular.json() == [WEEK.isoformat()]


async def test_jsonl_export_requires_schedule_in_scope(
    client: AsyncClient, manager_token: str, regular_token: str, approved_weeks: dict[str, str]
) -> None:
    mine = await client.get(
        f"/api/v1/export/jsonl/{approved_weeks[LOCATION_ID]}", headers=_h(regular_token)
    )
    assert mine.status_code == 200, mine.text

    theirs = await client.get(
        f"/api/v1/export/jsonl/{approved_weeks[OTHER_LOCATION_ID]}", headers=_h(regular_token)
    )
    assert theirs.status_code == 404
    assert theirs.json()["detail"] == "Approved schedule not found"

    admin = await client.get(
        f"/api/v1/export/jsonl/{approved_weeks[OTHER_LOCATION_ID]}", headers=_h(manager_token)
    )
    assert admin.status_code == 200
