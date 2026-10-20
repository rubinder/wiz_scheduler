"""Pay history shows names as worked, after renames and archives (#126)."""

import csv
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, PayrollExport, TimeEntry, User
from backend.services.payroll_export import CSV_HEADER
from backend.services.time_entries import attest_shift, derive_time_entries
from tests.test_payroll_api_integration import (
    TODAY, WEEK_AGO, _range_body, _tenant, _worked_shift,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ORIGINAL_NAME = "Dana Okafor"
ORIGINAL_LOCATION = "Flatbush Ave"
ORIGINAL_TZ = "America/New_York"


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def _approved_entry(client: AsyncClient, db: AsyncSession, t) -> None:
    await _worked_shift(db, t, days_ago=2, hour=9, checked_in=True)
    resp = await client.post("/api/v1/payroll/entries/derive",
                             json=_range_body(t), headers=t.manager_headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post("/api/v1/payroll/approve", json=_range_body(t),
                             headers=t.manager_headers)
    assert resp.status_code == 200, resp.text


async def _rename_and_archive(db: AsyncSession, t) -> None:
    employee = await db.get(Employee, t.employee_id)
    location = await db.get(Location, t.location_id)
    employee.full_name = "Renamed Person"
    employee.archived_at = datetime.now(timezone.utc)
    location.name = "Renamed Loc"
    location.timezone = "Asia/Tokyo"
    await db.commit()


async def test_export_uses_names_as_worked(
    client: AsyncClient, db_session: AsyncSession, paid
):
    await _approved_entry(client, db_session, paid)
    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    original_start = entry.start_time
    await _rename_and_archive(db_session, paid)

    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)
    assert resp.status_code == 200, resp.text

    rows = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    assert len(rows) == 2
    row = dict(zip(CSV_HEADER, rows[1]))
    assert row["employee_name"] == ORIGINAL_NAME
    assert row["location_name"] == ORIGINAL_LOCATION
    assert "Renamed Person" not in resp.text
    assert "Renamed Loc" not in resp.text
    # 09:00 UTC is 05:00 (EDT) / 04:00 (EST) in the original zone; Tokyo
    # would read 18:00. The offset is the original zone's, not +09:00.
    if original_start.tzinfo is None:
        original_start = original_start.replace(tzinfo=timezone.utc)
    expected = original_start.astimezone(ZoneInfo(ORIGINAL_TZ)).isoformat()
    assert row["start_time"] == expected
    assert not row["start_time"].endswith("+09:00")


async def test_entries_list_uses_snapshots(
    client: AsyncClient, db_session: AsyncSession, paid
):
    await _approved_entry(client, db_session, paid)
    await _rename_and_archive(db_session, paid)

    resp = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["employee_name"] == ORIGINAL_NAME
    assert rows[0]["location_name"] == ORIGINAL_LOCATION
    # Wall-clock face stays in the original zone (NY), not Tokyo.
    assert rows[0]["start_time"].endswith(("-04:00", "-05:00"))


async def test_derive_and_attest_fill_snapshots(
    db_session: AsyncSession, paid
):
    await _worked_shift(db_session, paid, days_ago=3, hour=9, checked_in=True)
    missed = await _worked_shift(db_session, paid, days_ago=2, hour=9,
                                 checked_in=False)

    result = await derive_time_entries(db_session, paid.company_id, WEEK_AGO, TODAY)
    assert result.created == 1

    manager = await db_session.get(User, paid.manager_id)
    await attest_shift(db_session, paid.company_id, missed, manager, "phone died")

    employee = await db_session.get(Employee, paid.employee_id)
    location = await db_session.get(Location, paid.location_id)
    entries = (await db_session.execute(select(TimeEntry))).scalars().all()
    assert len(entries) == 2
    for entry in entries:
        assert entry.employee_name == employee.full_name
        assert entry.location_name == location.name
        assert entry.location_timezone == location.timezone


async def test_single_location_export_records_location_name(
    client: AsyncClient, db_session: AsyncSession, paid
):
    await _approved_entry(client, db_session, paid)
    resp = await client.post(
        "/api/v1/payroll/export",
        json=_range_body(paid, include_exported=False,
                         location_id=paid.location_id),
        headers=paid.manager_headers,
    )
    assert resp.status_code == 200, resp.text
    export = (await db_session.execute(select(PayrollExport))).scalar_one()
    assert export.location_id == paid.location_id
    assert export.location_name == ORIGINAL_LOCATION


async def test_all_locations_export_has_no_location_name(
    client: AsyncClient, db_session: AsyncSession, paid
):
    await _approved_entry(client, db_session, paid)
    resp = await client.post("/api/v1/payroll/export",
                             json=_range_body(paid, include_exported=False),
                             headers=paid.manager_headers)
    assert resp.status_code == 200, resp.text
    export = (await db_session.execute(select(PayrollExport))).scalar_one()
    assert export.location_id is None
    assert export.location_name is None
