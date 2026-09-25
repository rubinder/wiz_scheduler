"""The attestation rate is REPORTING, not blocking.

A location attesting most of its shifts has either a broken QR flow or
something worth a conversation; withholding pay is not the response to either.
The last test in this file is the one that fails if somebody later wires
enforcement onto a reporting number.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_payroll_api import _range_body, _tenant, _worked_shift

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "CHECKIN_QR_SECRET", "test-secret-value")


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_the_report_carries_a_row_per_location_with_the_right_rate(
    client: AsyncClient, db_session: AsyncSession, paid
):
    scanned = await _worked_shift(db_session, paid, days_ago=1, checked_in=True)
    missed = await _worked_shift(db_session, paid, days_ago=2, checked_in=False)
    await client.post("/api/v1/payroll/entries/derive", json=_range_body(paid),
                      headers=paid.manager_headers)
    await client.post("/api/v1/payroll/attest", json={"shift_id": missed},
                      headers=paid.manager_headers)

    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid.manager_headers)

    assert resp.status_code == 200, resp.text
    attestation = resp.json()["attestation"]
    row = next(r for r in attestation if r["location_id"] == paid.location_id)
    assert row["location_name"] == "Flatbush Ave"
    assert row["entries"] == 2
    assert row["attested"] == 1
    assert row["rate"] == 0.5


async def test_a_location_with_no_entries_reports_zero_not_a_crash(
    client: AsyncClient, paid
):
    resp = await client.get("/api/v1/check-ins/report",
                            headers=paid.manager_headers)

    row = next(r for r in resp.json()["attestation"]
               if r["location_id"] == paid.location_id)
    assert row["entries"] == 0
    assert row["attested"] == 0
    assert row["rate"] == 0.0


async def test_a_hundred_percent_attestation_rate_blocks_nothing(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """Attest, approve and export all still succeed. Same posture as the
    weekly abuse report and ownership_groups.signup_*."""
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    attested = await client.post("/api/v1/payroll/attest",
                                 json={"shift_id": shift_id},
                                 headers=paid.manager_headers)
    assert attested.status_code == 201, attested.text

    report = await client.get("/api/v1/check-ins/report",
                              headers=paid.manager_headers)
    row = next(r for r in report.json()["attestation"]
               if r["location_id"] == paid.location_id)
    assert row["rate"] == 1.0

    approved = await client.post("/api/v1/payroll/approve",
                                 json=_range_body(paid),
                                 headers=paid.manager_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] == 1

    exported = await client.post(
        "/api/v1/payroll/export",
        json=_range_body(paid, include_exported=False),
        headers=paid.manager_headers,
    )
    assert exported.status_code == 200, exported.text
    assert "manager_attested" in exported.text


async def test_the_report_is_still_manager_only_and_paid_only(
    client: AsyncClient, db_session: AsyncSession, paid
):
    free = await _tenant(db_session, paid=False)

    assert (await client.get("/api/v1/check-ins/report",
                             headers=paid.employee_headers)).status_code == 403
    assert (await client.get("/api/v1/check-ins/report",
                             headers=free.manager_headers)).status_code == 402
