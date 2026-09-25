"""One week, scan to CSV, through the HTTP surface only.

Every earlier test file proves one component. This one proves they compose:
a scanned shift and a missed one become two payable rows, approval gates the
export, the export stamps exactly once, and the attestation rate reports what
happened without blocking any of it.
"""

import csv
import io

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import PayrollExport, TimeEntry
from backend.services.payroll_export import CSV_HEADER
from tests.test_payroll_api import (
    TODAY, WEEK_AGO, _range_body, _tenant, _worked_shift,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_a_whole_week_from_scan_to_csv(
    client: AsyncClient, db_session: AsyncSession, paid
):
    # Two worked shifts: one scanned, one where the phone died.
    await _worked_shift(db_session, paid, days_ago=3, hour=9, checked_in=True)
    missed = await _worked_shift(db_session, paid, days_ago=2, hour=9,
                                 checked_in=False)
    # And one that has not happened yet, which must stay out of everything.
    await _worked_shift(db_session, paid, days_ago=-3, checked_in=False)

    # 1. Derive: the scanned shift becomes an entry, the missed one an
    #    exception, the future one neither.
    derived = await client.post("/api/v1/payroll/entries/derive",
                                json=_range_body(paid),
                                headers=paid.manager_headers)
    assert derived.status_code == 200, derived.text
    assert derived.json() == {"created": 1, "existing": 0, "exception_count": 1}

    exceptions = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert [r["shift_id"] for r in exceptions.json()["rows"]] == [missed]

    # 2. Attest the missed one. Now there are two payable rows.
    attested = await client.post("/api/v1/payroll/attest",
                                 json={"shift_id": missed,
                                       "reason": "Phone battery died"},
                                 headers=paid.manager_headers)
    assert attested.status_code == 201, attested.text

    listed = await client.get(
        f"/api/v1/payroll/entries?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )
    assert listed.json()["total_entries"] == 2
    assert listed.json()["total_paid_minutes"] == 960

    # 3. Nothing exports unapproved.
    refused = await client.post("/api/v1/payroll/export",
                                json=_range_body(paid, include_exported=False),
                                headers=paid.manager_headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "nothing_to_export"

    # 4. Approve, then export.
    approved = await client.post("/api/v1/payroll/approve",
                                 json=_range_body(paid),
                                 headers=paid.manager_headers)
    assert approved.json() == {"approved": 2, "already_approved": 0}

    exported = await client.post("/api/v1/payroll/export",
                                 json=_range_body(paid, include_exported=False),
                                 headers=paid.manager_headers)
    assert exported.status_code == 200, exported.text

    rows = list(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert rows[0] == CSV_HEADER
    assert len(rows) == 3
    sources = {r[CSV_HEADER.index("source")] for r in rows[1:]}
    assert sources == {"checked_in", "manager_attested"}
    # The attestation reason is an internal note; the CSV leaves the building.
    assert "Phone battery died" not in exported.text

    attested_row = next(r for r in rows[1:]
                        if r[CSV_HEADER.index("source")] == "manager_attested")
    assert attested_row[CSV_HEADER.index("checked_in_at")] == ""
    assert attested_row[CSV_HEADER.index("lateness_minutes")] == ""
    assert attested_row[CSV_HEADER.index("paid_hours")] == "8.00"

    # 5. Exactly one audit row, and every entry stamped exactly once.
    exports = (await db_session.execute(select(PayrollExport))).scalars().all()
    assert len(exports) == 1
    assert exports[0].entry_count == 2
    assert exports[0].paid_minutes_total == 960

    entries = (await db_session.execute(select(TimeEntry))).scalars().all()
    assert len(entries) == 2
    for entry in entries:
        await db_session.refresh(entry)
        assert entry.exported_at is not None
        assert entry.payroll_export_id == exports[0].id

    # 6. A second export of the same range changes nothing.
    again = await client.post("/api/v1/payroll/export",
                              json=_range_body(paid, include_exported=False),
                              headers=paid.manager_headers)
    assert again.status_code == 409

    # 7. The attestation rate reports what happened and blocks nothing.
    report = await client.get("/api/v1/check-ins/report",
                              headers=paid.manager_headers)
    rate_row = next(r for r in report.json()["attestation"]
                    if r["location_id"] == paid.location_id)
    assert rate_row["entries"] == 2
    assert rate_row["attested"] == 1
    assert rate_row["rate"] == 0.5


async def test_re_deriving_after_the_whole_flow_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """The page calls derive on every load, including after an export. It must
    not resurrect, duplicate or un-stamp anything."""
    await _worked_shift(db_session, paid, checked_in=True)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/approve", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/export",
                      json=_range_body(paid, include_exported=False),
                      headers=paid.manager_headers)
    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    await db_session.refresh(entry)
    stamp = entry.exported_at

    again = await client.post("/api/v1/payroll/entries/derive",
                              json=_range_body(paid),
                              headers=paid.manager_headers)

    assert again.json() == {"created": 0, "existing": 1, "exception_count": 0}
    await db_session.refresh(entry)
    assert entry.exported_at == stamp
    assert entry.approved_at is not None
    assert len((await db_session.execute(select(TimeEntry))).scalars().all()) == 1
