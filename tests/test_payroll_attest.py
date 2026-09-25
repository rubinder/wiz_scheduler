"""Attestation: a pay-affecting action one person takes on another's behalf.

Every path here leaves an audit trail — source, attester, timestamp, reason —
because that is what makes it safe to pay someone who never scanned.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import TimeEntry
from tests.conftest import _id
from tests.test_payroll_api import TODAY, WEEK_AGO, _tenant, _worked_shift

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def paid(db_session: AsyncSession):
    return await _tenant(db_session, paid=True)


async def test_attesting_creates_an_audited_entry(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    resp = await client.post(
        "/api/v1/payroll/attest",
        json={"shift_id": shift_id, "reason": "Phone battery died"},
        headers=paid.manager_headers,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "manager_attested"
    assert body["attested_by_name"] == "Mo Manager"
    assert body["attestation_reason"] == "Phone battery died"
    assert body["attested_at"] is not None
    assert body["paid_minutes"] == 480

    entry = (await db_session.execute(select(TimeEntry))).scalar_one()
    assert entry.attested_by_user_id == paid.manager_id


async def test_an_attested_entry_reports_no_scan_and_no_lateness(
    client: AsyncClient, db_session: AsyncSession, paid
):
    """There is no scan to report, and inventing a lateness of zero would put
    a fact in the export that nobody observed."""
    shift_id = await _worked_shift(db_session, paid, checked_in=False)

    body = (await client.post("/api/v1/payroll/attest",
                              json={"shift_id": shift_id},
                              headers=paid.manager_headers)).json()

    assert body["checked_in_at"] is None
    assert body["lateness_minutes"] is None


async def test_attesting_removes_the_shift_from_the_exception_queue(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    await client.post("/api/v1/payroll/attest", json={"shift_id": shift_id},
                      headers=paid.manager_headers)

    resp = await client.get(
        f"/api/v1/payroll/exceptions?range_start={WEEK_AGO}&range_end={TODAY}",
        headers=paid.manager_headers,
    )

    assert resp.json()["rows"] == []


async def test_a_shift_that_has_not_started_cannot_be_pre_attested(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, days_ago=-3,
                                   checked_in=False)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_not_started"


async def test_a_shift_that_already_has_an_entry_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=True)
    await client.post("/api/v1/payroll/entries/derive",
                      json={"range_start": WEEK_AGO.isoformat(),
                            "range_end": TODAY.isoformat(),
                            "location_id": None},
                      headers=paid.manager_headers)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "entry_exists"


async def test_attesting_twice_is_refused_the_second_time(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False)
    body = {"shift_id": shift_id}
    first = await client.post("/api/v1/payroll/attest", json=body,
                              headers=paid.manager_headers)
    second = await client.post("/api/v1/payroll/attest", json=body,
                               headers=paid.manager_headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "entry_exists"


async def test_a_draft_schedule_shift_is_refused(
    client: AsyncClient, db_session: AsyncSession, paid
):
    shift_id = await _worked_shift(db_session, paid, checked_in=False,
                                   status="draft")

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "shift_not_approved"


async def test_another_companys_shift_is_not_found(
    client: AsyncClient, db_session: AsyncSession, paid
):
    other = await _tenant(db_session, paid=True)
    shift_id = await _worked_shift(db_session, other, checked_in=False)

    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": shift_id},
                             headers=paid.manager_headers)

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "shift_not_found"


async def test_a_shift_that_does_not_exist_is_not_found(
    client: AsyncClient, paid
):
    resp = await client.post("/api/v1/payroll/attest",
                             json={"shift_id": _id()},
                             headers=paid.manager_headers)

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "shift_not_found"
