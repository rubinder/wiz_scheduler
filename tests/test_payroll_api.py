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

_GATED_ENDPOINTS = [
    ("POST", "/payroll/entries/derive", "derive"),
    ("GET", "/payroll/entries", "entries"),
    ("GET", "/payroll/exceptions", "exceptions"),
    ("POST", "/payroll/attest", "attest"),
    ("POST", "/payroll/approve", "approve"),
    ("POST", "/payroll/export", "export"),
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


async def test_row_timestamps_are_localized_to_the_locations_offset(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """checked_in_at, start_time and end_time are all true instants that
    Postgres returns normalised to UTC. Serializing any of them raw would
    show the manager the UTC clock instead of the location's — the same class
    of bug #92 fixed for shift times.

    2024-07-15 is a fixed date inside America/New_York's DST window (EDT,
    UTC-4), so this does not depend on when the suite runs.
    """
    scan_day = date(2024, 7, 15)
    start = datetime.combine(
        scan_day, datetime.min.time(), tzinfo=timezone.utc
    ).replace(hour=13)  # 13:00 UTC = 09:00 EDT
    schedule_id, shift_id = _id(), _id()
    db_session.add(ShiftSchedule(id=schedule_id, company_id=paid.company_id,
                                 location_id=paid.location_id,
                                 week_start_date=start.date(), status="approved"))
    await db_session.flush()
    db_session.add(Shift(id=shift_id, company_id=paid.company_id,
                         shift_schedule_id=schedule_id,
                         location_id=paid.location_id,
                         employee_id=paid.employee_id, role_id=paid.role_id,
                         role_name=paid.role_name, date=start.date(),
                         start_time=start, end_time=start + timedelta(hours=8)))
    checked_in_at = start + timedelta(minutes=2)  # 13:02 UTC
    db_session.add(EmployeeCheckIn(
        id=_id(), company_id=paid.company_id, location_id=paid.location_id,
        employee_id=paid.employee_id, shift_id=shift_id,
        checked_in_at=checked_in_at, local_date=start.date(),
        counter=0, status=CHECK_IN_MATCHED, minutes_from_start=2,
    ))
    await db_session.commit()

    await client.post(
        "/api/v1/payroll/entries/derive",
        json=_range_body(paid, range_start=scan_day.isoformat(),
                         range_end=scan_day.isoformat()),
        headers=paid.manager_headers,
    )
    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={scan_day}&range_end={scan_day}",
        headers=paid.manager_headers,
    )
    row = listed.json()["rows"][0]

    # 13:02Z lists as 09:02:00-04:00: the local wall-clock hour, with the
    # location's own offset — not the raw UTC instant.
    assert row["checked_in_at"].startswith("2024-07-15T09:02:00")
    assert row["checked_in_at"].endswith(("-04:00", "-05:00"))

    # The shift is STORED as 13:00Z but is scheduled 09:00-17:00 local, and
    # that local face — with the location's own offset — is what must reach
    # utils/shiftTime.ts, which reads the face straight off the string.
    assert row["start_time"].startswith("2024-07-15T09:00:00")
    assert row["start_time"].endswith(("-04:00", "-05:00"))
    assert row["end_time"].startswith("2024-07-15T17:00:00")
    assert row["end_time"].endswith(("-04:00", "-05:00"))


async def test_exception_rows_are_localized_to_the_locations_offset(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """The exception queue serializes the same true instants and therefore
    needs the same conversion — an unscanned shift shown at 13:00 instead of
    09:00 is the same bug on the other tab."""
    scan_day = date(2024, 7, 15)
    start = datetime.combine(
        scan_day, datetime.min.time(), tzinfo=timezone.utc
    ).replace(hour=13)  # 13:00 UTC = 09:00 EDT
    schedule_id, shift_id = _id(), _id()
    db_session.add(ShiftSchedule(id=schedule_id, company_id=paid.company_id,
                                 location_id=paid.location_id,
                                 week_start_date=start.date(), status="approved"))
    await db_session.flush()
    db_session.add(Shift(id=shift_id, company_id=paid.company_id,
                         shift_schedule_id=schedule_id,
                         location_id=paid.location_id,
                         employee_id=paid.employee_id, role_id=paid.role_id,
                         role_name=paid.role_name, date=start.date(),
                         start_time=start, end_time=start + timedelta(hours=8)))
    await db_session.commit()  # no check-in: it lands in the exception queue

    resp = await client.get(
        f"/api/v1/payroll/exceptions?range_start={scan_day}&range_end={scan_day}",
        headers=paid.manager_headers,
    )

    assert resp.status_code == 200, resp.text
    row = resp.json()["rows"][0]
    assert row["shift_id"] == shift_id
    assert row["start_time"].startswith("2024-07-15T09:00:00")
    assert row["start_time"].endswith(("-04:00", "-05:00"))
    assert row["end_time"].startswith("2024-07-15T17:00:00")
    assert row["end_time"].endswith(("-04:00", "-05:00"))


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


# --- approval ---------------------------------------------------------------

async def _derive(client: AsyncClient, t: SimpleNamespace) -> None:
    resp = await client.post("/api/v1/payroll/entries/derive",
                             json=_range_body(t), headers=t.manager_headers)
    assert resp.status_code == 200, resp.text


async def _entry_ids(client: AsyncClient, t: SimpleNamespace) -> list[str]:
    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=t.manager_headers,
    )
    return [r["id"] for r in resp.json()["rows"]]


async def test_approving_a_range_stamps_every_unapproved_entry(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _worked_shift(db_session, paid, days_ago=2)
    await _derive(client, paid)

    resp = await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"approved": 2, "already_approved": 0}

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert listed.json()["approved_entries"] == 2


async def test_approving_twice_does_not_move_the_timestamp(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """The approval timestamp is an audit fact about when a human said yes,
    and a second click must not rewrite it."""
    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                      headers=paid.manager_headers)
    first_stamp = (await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )).json()["rows"][0]["approved_at"]

    second = await client.post("/api/v1/payroll/approve",
                               json=_range_body(paid),
                               headers=paid.manager_headers)

    assert second.json() == {"approved": 0, "already_approved": 1}
    after = (await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )).json()["rows"][0]["approved_at"]
    assert after == first_stamp


async def test_entry_ids_approves_only_the_named_subset(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _worked_shift(db_session, paid, days_ago=2)
    await _derive(client, paid)
    ids = await _entry_ids(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(paid, entry_ids=[ids[0]]),
        headers=paid.manager_headers,
    )

    assert resp.json() == {"approved": 1, "already_approved": 0}


async def test_entry_ids_naming_another_companys_entry_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    other = await _tenant(db_session, paid=True)
    await _worked_shift(db_session, other)
    await _derive(client, other)
    stranger_ids = await _entry_ids(client, other)
    await _worked_shift(db_session, paid)
    await _derive(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(paid, entry_ids=stranger_ids),
        headers=paid.manager_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_entry_ids"


async def test_entry_ids_outside_the_range_are_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid, days_ago=1)
    await _derive(client, paid)
    ids = await _entry_ids(client, paid)

    resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(
            paid,
            range_start=(TODAY - timedelta(days=30)).isoformat(),
            range_end=(TODAY - timedelta(days=20)).isoformat(),
            entry_ids=ids,
        ),
        headers=paid.manager_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_entry_ids"


# --- export -----------------------------------------------------------------

async def _approve_all(client: AsyncClient, t: SimpleNamespace) -> None:
    resp = await client.post("/api/v1/payroll/approve", json=_range_body(t),
                             headers=t.manager_headers)
    assert resp.status_code == 200, resp.text


async def test_export_with_nothing_approved_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    from sqlalchemy import select as _select

    from backend.models import PayrollExport

    await _worked_shift(db_session, paid)
    await _derive(client, paid)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "nothing_to_export"

    exports = (await db_session.execute(_select(PayrollExport))).scalars().all()
    assert exports == []


async def test_approve_then_export_returns_csv_and_writes_one_audit_row(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    from sqlalchemy import select as _select

    from backend.models import PayrollExport, TimeEntry

    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)

    resp = await client.post(
        "/api/v1/payroll/export",
        json=_range_body(paid, location_id=paid.location_id,
                         include_exported=False),
        headers=paid.manager_headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    expected_filename = (
        f"payroll_slug-{paid.company_id}_{WEEK_AGO.isoformat()}_"
        f"{TODAY.isoformat()}.csv"
    )
    assert (resp.headers["content-disposition"]
            == f'attachment; filename="{expected_filename}"')
    assert "employee_name" in resp.text

    exports = (await db_session.execute(_select(PayrollExport))).scalars().all()
    assert len(exports) == 1
    assert exports[0].company_id == paid.company_id
    assert exports[0].ownership_group_id == paid.og_id
    assert exports[0].location_id == paid.location_id
    assert exports[0].range_start == WEEK_AGO
    assert exports[0].range_end == TODAY
    assert exports[0].entry_count == 1
    assert exports[0].paid_minutes_total == 480
    assert exports[0].format == "csv"
    assert exports[0].exported_by_user_id == paid.manager_id

    entry = (await db_session.execute(_select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    assert entry.exported_at is not None
    assert entry.payroll_export_id == exports[0].id


async def test_export_only_stamps_the_approved_entry_in_a_mixed_range(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """One approved entry and one still-unapproved entry in the same range:
    export must stamp only the approved one and leave the other's
    exported_at NULL."""
    from sqlalchemy import select as _select

    from backend.models import TimeEntry

    await _worked_shift(db_session, paid, days_ago=1)
    await _worked_shift(db_session, paid, days_ago=2)
    await _derive(client, paid)
    ids = await _entry_ids(client, paid)

    approve_resp = await client.post(
        "/api/v1/payroll/approve",
        json=_range_body(paid, entry_ids=[ids[0]]),
        headers=paid.manager_headers,
    )
    assert approve_resp.status_code == 200, approve_resp.text

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text

    entries = {
        e.id: e
        for e in (await db_session.execute(_select(TimeEntry))).scalars().all()
    }
    for e in entries.values():
        await db_session.refresh(e)
    assert entries[ids[0]].exported_at is not None
    assert entries[ids[1]].approved_at is None
    assert entries[ids[1]].exported_at is None


async def test_export_only_sees_the_callers_own_company(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """Multi-tenancy: another company's approved hours must never appear in,
    or count toward, this company's export."""
    other = await _tenant(db_session, paid=True)
    await _worked_shift(db_session, other)
    await _derive(client, other)
    await _approve_all(client, other)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "nothing_to_export"


async def test_a_second_export_of_the_same_range_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "nothing_to_export"
    assert resp.json()["detail"]["already_exported"] == 1


async def test_include_exported_redownloads_without_restamping(
    client: AsyncClient, db_session: AsyncSession, paid: SimpleNamespace
):
    """A re-download is a thing that happened and the log should say so — but
    it must not move exported_at, which is what stops a pay period being
    exported twice."""
    from sqlalchemy import select as _select

    from backend.models import PayrollExport, TimeEntry

    await _worked_shift(db_session, paid)
    await _derive(client, paid)
    await _approve_all(client, paid)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)
    entry = (await db_session.execute(_select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    first_stamp = entry.exported_at
    first_export_id = entry.payroll_export_id

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=True),
                             headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    assert "Dana Okafor" in resp.text
    await db_session.refresh(entry)
    assert entry.exported_at == first_stamp
    assert entry.payroll_export_id == first_export_id
    exports = (await db_session.execute(_select(PayrollExport))).scalars().all()
    assert len(exports) == 2
