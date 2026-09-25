"""The payroll endpoints: gating, range validation, and the read paths.

Paid-plan and manager gating are asserted on EVERY endpoint, because what
gates the feature is the tenant's plan and the caller's role, not the shape of
the request.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company, Employee, EmployeeCheckIn, Location, Region, Role, Shift,
    ShiftSchedule, User,
)
from backend.models.employee_check_in import CHECK_IN_MATCHED
from backend.models.ownership_group import OwnershipGroup
from tests.conftest import _id, _make_token

pytestmark = pytest.mark.asyncio

TODAY = datetime.now(timezone.utc).date()
WEEK_AGO = TODAY - timedelta(days=7)


async def _tenant(db: AsyncSession, *, paid: bool) -> SimpleNamespace:
    og_id, company_id, region_id = _id(), _id(), _id()
    db.add(OwnershipGroup(id=og_id, name="G",
                          stripe_subscription_id="sub_x" if paid else None))
    await db.flush()
    db.add(Company(id=company_id, name="C", slug=f"slug-{company_id}",
                   ownership_group_id=og_id))
    await db.flush()
    db.add(Region(id=region_id, company_id=company_id, name="R"))
    await db.flush()
    location_id, role_id, employee_id = _id(), _id(), _id()
    manager_id, employee_user_id = _id(), _id()
    db.add(Location(id=location_id, company_id=company_id, region_id=region_id,
                    name="Flatbush Ave", timezone="America/New_York"))
    role = Role(id=role_id, company_id=company_id, name="Role A")
    db.add(role)
    db.add(User(id=manager_id, company_id=company_id,
                email=f"{manager_id}@example.com", hashed_password="x",
                full_name="Mo Manager", user_role="manager"))
    db.add(User(id=employee_user_id, company_id=company_id,
                email=f"{employee_user_id}@example.com", hashed_password="x",
                full_name="E", user_role="employee"))
    db.add(Employee(id=employee_id, company_id=company_id,
                    full_name="Dana Okafor", email=f"{employee_id}@example.com",
                    location_ids=[location_id], user_id=employee_user_id))
    await db.commit()
    return SimpleNamespace(
        og_id=og_id, company_id=company_id, location_id=location_id,
        role_id=role_id, role_name=role.name, employee_id=employee_id,
        manager_id=manager_id,
        manager_headers={"Authorization":
                         f"Bearer {_make_token(manager_id, company_id, 'manager')}"},
        employee_headers={"Authorization":
                          f"Bearer {_make_token(employee_user_id, company_id, 'employee')}"},
    )


async def _worked_shift(
    db: AsyncSession, t: SimpleNamespace, *, days_ago: int = 1,
    hour: int = 9, hours: int = 8, checked_in: bool = True,
    status: str = "approved",
) -> str:
    """An approved shift that has already started, optionally with its scan."""
    start = datetime.combine(
        TODAY - timedelta(days=days_ago), datetime.min.time(),
        tzinfo=timezone.utc,
    ).replace(hour=hour)
    schedule_id, shift_id = _id(), _id()
    db.add(ShiftSchedule(id=schedule_id, company_id=t.company_id,
                         location_id=t.location_id,
                         week_start_date=start.date(), status=status))
    await db.flush()
    db.add(Shift(id=shift_id, company_id=t.company_id,
                 shift_schedule_id=schedule_id, location_id=t.location_id,
                 employee_id=t.employee_id, role_id=t.role_id,
                 role_name=t.role_name, date=start.date(), start_time=start,
                 end_time=start + timedelta(hours=hours)))
    if checked_in:
        db.add(EmployeeCheckIn(
            id=_id(), company_id=t.company_id, location_id=t.location_id,
            employee_id=t.employee_id, shift_id=shift_id,
            checked_in_at=start - timedelta(minutes=4), local_date=start.date(),
            counter=0, status=CHECK_IN_MATCHED, minutes_from_start=-4,
        ))
    await db.commit()
    return shift_id


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=True)


@pytest_asyncio.fixture
async def free(db_session: AsyncSession) -> SimpleNamespace:
    return await _tenant(db_session, paid=False)


def _range_body(t: SimpleNamespace, **extra) -> dict:
    body = {"range_start": WEEK_AGO.isoformat(), "range_end": TODAY.isoformat(),
            "location_id": None}
    body.update(extra)
    return body


# --- plan gating ------------------------------------------------------------

# Tasks 5, 6, and 7: remove your endpoint's xfail mark below once its route exists.
_GATED_ENDPOINTS = [
    ("POST", "/payroll/entries/derive", "derive"),
    ("GET", "/payroll/entries", "entries"),
    ("GET", "/payroll/exceptions", "exceptions"),
    ("POST", "/payroll/attest", "attest"),
    pytest.param("POST", "/payroll/approve", "approve",
                 marks=pytest.mark.xfail(strict=True, reason="built in Task 6")),
    pytest.param("POST", "/payroll/export", "export",
                 marks=pytest.mark.xfail(strict=True, reason="built in Task 7")),
]


def _sweep_call(client: AsyncClient, t: SimpleNamespace, headers: dict,
                method: str, path: str, kind: str):
    url = f"/api/v1{path}"
    if kind in ("entries", "exceptions"):
        return client.request(
            method, f"{url}?range_start={WEEK_AGO}&range_end={TODAY}",
            headers=headers,
        )
    if kind == "attest":
        json = {"shift_id": _id()}
    elif kind == "export":
        json = _range_body(t, include_exported=False)
    else:  # derive, approve
        json = _range_body(t)
    return client.request(method, url, json=json, headers=headers)


@pytest.mark.parametrize("method,path,kind", _GATED_ENDPOINTS)
async def test_every_endpoint_is_paid_only(
    client: AsyncClient, free: SimpleNamespace, method: str, path: str, kind: str
):
    resp = await _sweep_call(client, free, free.manager_headers, method, path, kind)
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == "payroll_requires_paid_plan"


@pytest.mark.parametrize("method,path,kind", _GATED_ENDPOINTS)
async def test_every_endpoint_is_manager_only(
    client: AsyncClient, paid: SimpleNamespace, method: str, path: str, kind: str
):
    resp = await _sweep_call(client, paid, paid.employee_headers, method, path, kind)
    assert resp.status_code == 403, resp.text


# --- range validation -------------------------------------------------------

async def test_a_backwards_range_is_rejected(
    client: AsyncClient, paid: SimpleNamespace
):
    resp = await client.post(
        "/api/v1/payroll/entries/derive",
        json=_range_body(paid, range_start=TODAY.isoformat(),
                         range_end=WEEK_AGO.isoformat()),
        headers=paid.manager_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_range"


async def test_a_ninety_day_span_is_rejected(
    client: AsyncClient, paid: SimpleNamespace
):
    """62 days is two monthly pay periods — far beyond any real cadence, and
    it bounds every query on the page."""
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={TODAY - timedelta(days=90)}"
        f"&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_range"


async def test_a_sixty_two_day_span_is_accepted(
    client: AsyncClient, paid: SimpleNamespace
):
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={TODAY - timedelta(days=61)}"
        f"&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text


# --- the read paths ---------------------------------------------------------

async def test_derive_then_list(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    shift_id = await _worked_shift(db_session, paid)

    derived = await client.post("/api/v1/payroll/entries/derive",
                                json=_range_body(paid),
                                headers=paid.manager_headers)
    assert derived.status_code == 200, derived.text
    assert derived.json() == {"created": 1, "existing": 0, "exception_count": 0}

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    body = listed.json()
    assert body["total_entries"] == 1
    assert body["total_paid_minutes"] == 480
    assert body["approved_entries"] == 0
    row = body["rows"][0]
    assert row["shift_id"] == shift_id
    assert row["employee_name"] == "Dana Okafor"
    assert row["location_name"] == "Flatbush Ave"
    assert row["source"] == "checked_in"
    assert row["lateness_minutes"] == -4
    assert row["attested_by_name"] is None


async def test_the_exception_queue_lists_a_shift_with_no_scan(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    resp = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["rows"][0]["shift_id"] == shift_id
    assert body["rows"][0]["paid_minutes"] == 480


async def test_another_companys_entries_are_invisible(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    other = await _tenant(db_session, paid=True)
    await _worked_shift(db_session, other)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(other),
                      headers=other.manager_headers)

    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.json()["rows"] == []
