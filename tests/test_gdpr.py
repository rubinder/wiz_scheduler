"""Tests for /api/v1/gdpr endpoints and data retention service."""

from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Employee,
    EmployeeAvailability,
    EmployeeInvite,
    EmployeeRole,
    Shift,
    ShiftSchedule,
    User,
)
from backend.models.consent import UserConsent
from backend.models.failure_log import FailureLog
from backend.services.data_retention import run_data_retention
from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    EMPLOYEE_USER_ID,
    LOCATION_ID,
    MANAGER_USER_ID,
    ROLE_FLOOR_ID,
    _id,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /gdpr/privacy-policy  (no auth required)
# ---------------------------------------------------------------------------


async def test_privacy_policy(client: AsyncClient):
    resp = await client.get("/api/v1/gdpr/privacy-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0"
    assert "PRIVACY POLICY" in data["content"]


# ---------------------------------------------------------------------------
# GET /gdpr/terms  (no auth required)
# ---------------------------------------------------------------------------


async def test_terms_of_service(client: AsyncClient):
    resp = await client.get("/api/v1/gdpr/terms")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0"
    assert "TERMS OF SERVICE" in data["content"]


# ---------------------------------------------------------------------------
# GET /gdpr/dpa  (no auth required)
# ---------------------------------------------------------------------------


async def test_data_processing_agreement(client: AsyncClient):
    resp = await client.get("/api/v1/gdpr/dpa")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0"
    assert len(data["processors"]) > 0
    assert any(p["name"] == "Anthropic" for p in data["processors"])


# ---------------------------------------------------------------------------
# GET /gdpr/export  (auth required)
# ---------------------------------------------------------------------------


async def test_export_data_manager(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    resp = await client.get(
        "/api/v1/gdpr/export",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "manager@test.com"
    assert "exported_at" in data


async def test_export_data_employee_with_profile(
    client: AsyncClient,
    employee_token: str,
    seed_employee_user,
    db_session: AsyncSession,
    seed_employees,
):
    """Link employee user to an employee record and verify export includes it."""
    # Link user to employee
    result = await db_session.execute(
        select(Employee).where(Employee.id == EMPLOYEE1_ID)
    )
    emp = result.scalar_one()
    emp.user_id = EMPLOYEE_USER_ID
    await db_session.commit()

    resp = await client.get(
        "/api/v1/gdpr/export",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["employee"] is not None
    assert data["employee"]["full_name"] == "Alice Johnson"


async def test_export_data_no_employee_profile(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    """Manager without linked employee should have employee=None."""
    resp = await client.get(
        "/api/v1/gdpr/export",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["employee"] is None


# ---------------------------------------------------------------------------
# GET /gdpr/consents
# ---------------------------------------------------------------------------


async def test_list_consents_empty(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    resp = await client.get(
        "/api/v1/gdpr/consents",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_consents_with_data(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
    db_session: AsyncSession,
):
    consent = UserConsent(
        user_id=MANAGER_USER_ID,
        company_id=COMPANY_ID,
        consent_type="privacy_policy",
        version="1.0",
    )
    db_session.add(consent)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/gdpr/consents",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["consent_type"] == "privacy_policy"


# ---------------------------------------------------------------------------
# POST /gdpr/consents/revoke
# ---------------------------------------------------------------------------


async def test_revoke_consent(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
    db_session: AsyncSession,
):
    consent = UserConsent(
        id=_id(),
        user_id=MANAGER_USER_ID,
        company_id=COMPANY_ID,
        consent_type="data_processing_ai",
        version="1.0",
    )
    db_session.add(consent)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/gdpr/consents/revoke",
        headers={"Authorization": f"Bearer {manager_token}"},
        params={"consent_id": consent.id},
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True


async def test_revoke_consent_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    resp = await client.post(
        "/api/v1/gdpr/consents/revoke",
        headers={"Authorization": f"Bearer {manager_token}"},
        params={"consent_id": "NOPE"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /gdpr/delete-account
# ---------------------------------------------------------------------------


async def test_delete_account(
    client: AsyncClient,
    employee_token: str,
    seed_employee_user,
    db_session: AsyncSession,
    seed_employees,
):
    # Link user to employee
    result = await db_session.execute(
        select(Employee).where(Employee.id == EMPLOYEE1_ID)
    )
    emp = result.scalar_one()
    emp.user_id = EMPLOYEE_USER_ID
    await db_session.commit()

    resp = await client.delete(
        "/api/v1/gdpr/delete-account",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Verify user is gone
    result = await db_session.execute(
        select(User).where(User.id == EMPLOYEE_USER_ID)
    )
    assert result.scalar_one_or_none() is None

    # Verify employee is gone
    result = await db_session.execute(
        select(Employee).where(Employee.id == EMPLOYEE1_ID)
    )
    assert result.scalar_one_or_none() is None


async def test_delete_account_no_employee(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    """Delete account for user with no linked employee."""
    resp = await client.delete(
        "/api/v1/gdpr/delete-account",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


# ---------------------------------------------------------------------------
# POST /gdpr/retention/purge  (manager only)
# ---------------------------------------------------------------------------


async def test_retention_purge_endpoint(
    client: AsyncClient,
    manager_token: str,
    seed_manager,
):
    resp = await client.post(
        "/api/v1/gdpr/retention/purge",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert "summary" in data


async def test_retention_purge_requires_manager(
    client: AsyncClient,
    employee_token: str,
    seed_employee_user,
):
    resp = await client.post(
        "/api/v1/gdpr/retention/purge",
        headers={"Authorization": f"Bearer {employee_token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Data retention service unit tests
# ---------------------------------------------------------------------------


async def test_data_retention_deletes_old_rejected_schedules(
    db_session: AsyncSession,
    seed_location,
    seed_roles,
):
    old_time = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_REJECTED_SCHEDULES_DAYS + 1
    )
    sched = ShiftSchedule(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 1),
        status="rejected",
        created_at=old_time,
    )
    db_session.add(sched)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["rejected_schedules_deleted"] >= 1


async def test_data_retention_keeps_recent_rejected(
    db_session: AsyncSession,
    seed_location,
    seed_roles,
):
    sched = ShiftSchedule(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 10),
        status="rejected",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sched)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["rejected_schedules_deleted"] == 0


async def test_data_retention_deletes_stale_drafts(
    db_session: AsyncSession,
    seed_location,
    seed_roles,
):
    old_time = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_STALE_DRAFTS_DAYS + 1
    )
    sched = ShiftSchedule(
        id=_id(),
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 1, 1),
        status="draft",
        created_at=old_time,
    )
    db_session.add(sched)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["stale_drafts_deleted"] >= 1


async def test_data_retention_deletes_old_failure_logs(
    db_session: AsyncSession,
    seed_company,
):
    old_time = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_FAILURE_LOGS_DAYS + 1
    )
    log = FailureLog(
        id=_id(),
        company_id=COMPANY_ID,
        category="TEST",
        severity="error",
        source="test",
        message="old error",
        created_at=old_time,
    )
    db_session.add(log)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["old_failure_logs_deleted"] >= 1


async def test_data_retention_deletes_expired_invites(
    db_session: AsyncSession,
    seed_employees,
):
    old_expiry = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_EXPIRED_INVITES_DAYS + 1
    )
    invite = EmployeeInvite(
        id=_id(),
        company_id=COMPANY_ID,
        employee_id=EMPLOYEE1_ID,
        token="expired-token-12345678",
        email="old@test.com",
        expires_at=old_expiry,
    )
    db_session.add(invite)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["expired_invites_deleted"] >= 1


async def test_data_retention_deletes_old_revoked_consents(
    db_session: AsyncSession,
    seed_manager,
):
    old_revoke = datetime.now(timezone.utc) - timedelta(
        days=settings.RETENTION_REVOKED_CONSENTS_DAYS + 1
    )
    consent = UserConsent(
        id=_id(),
        user_id=MANAGER_USER_ID,
        company_id=COMPANY_ID,
        consent_type="privacy_policy",
        version="1.0",
        revoked_at=old_revoke,
    )
    db_session.add(consent)
    await db_session.commit()

    summary = await run_data_retention(db_session)
    assert summary["old_revoked_consents_deleted"] >= 1


async def test_data_retention_empty_db(db_session: AsyncSession):
    """Running retention on empty DB should return all zeros."""
    summary = await run_data_retention(db_session)
    assert summary["rejected_schedules_deleted"] == 0
    assert summary["stale_drafts_deleted"] == 0
    assert summary["old_failure_logs_deleted"] == 0
    assert summary["expired_invites_deleted"] == 0
    assert summary["old_revoked_consents_deleted"] == 0
