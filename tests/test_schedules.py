"""Tests for /api/v1/schedules endpoints."""

import json
from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import (
    COMPANY_ID,
    EMPLOYEE1_ID,
    EMPLOYEE2_ID,
    LOCATION_ID,
    ROLE_FLOOR_ID,
    ROLE_LEAD_ID,
    _id,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_schedule(
    db_session: AsyncSession,
    *,
    status: str = "draft",
    raw_shifts: list[dict] | None = None,
    schedule_id: str | None = None,
    strategy: str | None = "random",
) -> str:
    """Insert a ShiftSchedule row directly and return its ID."""
    from backend.models import ShiftSchedule

    sid = schedule_id or _id()
    sched = ShiftSchedule(
        id=sid,
        company_id=COMPANY_ID,
        location_id=LOCATION_ID,
        week_start_date=date(2026, 4, 13),
        status=status,
        raw_llm_output=json.dumps(raw_shifts) if raw_shifts else None,
        strategy=strategy,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sched)
    await db_session.commit()
    return sid


def _sample_shifts() -> list[dict]:
    return [
        {
            "employee_id": EMPLOYEE1_ID,
            "employee_name": "Alice Johnson",
            "role_id": ROLE_FLOOR_ID,
            "role_name": "Floor Associate",
            "location_id": LOCATION_ID,
            "date": "2026-04-13",
            "start_time": "2026-04-13T09:00:00",
            "end_time": "2026-04-13T17:00:00",
            "status": "ok",
        },
        {
            "employee_id": EMPLOYEE2_ID,
            "employee_name": "Bob Smith",
            "role_id": ROLE_LEAD_ID,
            "role_name": "Team Lead",
            "location_id": LOCATION_ID,
            "date": "2026-04-13",
            "start_time": "2026-04-13T09:00:00",
            "end_time": "2026-04-13T17:00:00",
            "status": "ok",
        },
    ]


# ---------------------------------------------------------------------------
# PUT /schedules/{id}/shifts
# ---------------------------------------------------------------------------


async def test_update_shifts_ok(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session)
    resp = await client.put(
        f"/api/v1/schedules/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": _sample_shifts()},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_update_shifts_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    resp = await client.put(
        "/api/v1/schedules/NOTREAL/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": []},
    )
    assert resp.status_code == 404


async def test_update_shifts_already_approved(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session, status="approved")
    resp = await client.put(
        f"/api/v1/schedules/{sid}/shifts",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"shifts": _sample_shifts()},
    )
    assert resp.status_code == 400
    assert "approved" in resp.json()["detail"].lower()


async def test_update_shifts_requires_manager(
    client: AsyncClient,
    employee_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session)
    resp = await client.put(
        f"/api/v1/schedules/{sid}/shifts",
        headers={"Authorization": f"Bearer {employee_token}"},
        json={"shifts": []},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /schedules/{id}/approve
# ---------------------------------------------------------------------------


async def test_approve_schedule(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session, raw_shifts=_sample_shifts())
    resp = await client.post(
        f"/api/v1/schedules/{sid}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["id"] == sid


async def test_approve_schedule_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    resp = await client.post(
        "/api/v1/schedules/NOTREAL/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 404


async def test_approve_already_approved(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session, status="approved")
    resp = await client.post(
        f"/api/v1/schedules/{sid}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 400


async def test_approve_skips_vacant_shifts(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    """VACANT placeholder shifts should not create Shift records."""
    shifts = [
        {
            "employee_id": "VACANT",
            "employee_name": "VACANT",
            "role_id": ROLE_FLOOR_ID,
            "role_name": "Floor Associate",
            "location_id": LOCATION_ID,
            "date": "2026-04-13",
            "start_time": "2026-04-13T09:00:00",
            "end_time": "2026-04-13T17:00:00",
            "status": "ok",
        },
    ]
    sid = await _create_schedule(db_session, raw_shifts=shifts)
    resp = await client.post(
        f"/api/v1/schedules/{sid}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["shifts"] == []


# ---------------------------------------------------------------------------
# POST /schedules/{id}/reject
# ---------------------------------------------------------------------------


async def test_reject_schedule(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    sid = await _create_schedule(db_session)
    resp = await client.post(
        f"/api/v1/schedules/{sid}/reject",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_reject_not_found(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    resp = await client.post(
        "/api/v1/schedules/NOTREAL/reject",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /schedules/week/{date}
# ---------------------------------------------------------------------------


async def test_get_week_schedules(
    client: AsyncClient,
    manager_token: str,
    db_session: AsyncSession,
    seed_employees,
    seed_location,
    seed_roles,
):
    await _create_schedule(db_session)
    resp = await client.get(
        "/api/v1/schedules/week/2026-04-13",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["week_start_date"] == "2026-04-13"


async def test_get_week_schedules_empty(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    resp = await client.get(
        "/api/v1/schedules/week/2020-01-01",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /schedules/ai-credits & /schedules/schedule-quota
# ---------------------------------------------------------------------------


async def test_ai_credits_no_ownership_group(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    """Company without ownership group returns default credits."""
    resp = await client.get(
        "/api/v1/schedules/ai-credits",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_generate"] is True


async def test_schedule_quota_no_ownership_group(
    client: AsyncClient,
    manager_token: str,
    seed_employees,
):
    resp = await client.get(
        "/api/v1/schedules/schedule-quota",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_generate"] is True


# ---------------------------------------------------------------------------
# POST /schedules/generate — schedule lock integration
# ---------------------------------------------------------------------------


async def test_generate_returns_409_when_locked(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company,
):
    """If a non-expired lock exists for the Company, /generate returns 409."""
    from datetime import datetime, timedelta, timezone
    from backend.models import ScheduleLock

    db_session.add(ScheduleLock(
        company_id=seeded_company.company_id,
        locked_by_user_id=seeded_company.manager_user_id,
        operation="generate",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "schedule_locked"
    assert "locked_by" in body["detail"]
    assert "expires_at" in body["detail"]


async def test_generate_releases_lock_after_stream(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """When the stream completes successfully, the lock row is gone."""
    from sqlalchemy import select
    from backend.models import ScheduleLock

    async def fake_pipeline(**kwargs):
        if False:
            yield {}
        return
    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", fake_pipeline
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    assert resp.status_code == 200
    async for _ in resp.aiter_lines():
        pass

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert rows == []


async def test_generate_releases_lock_on_exception(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company, monkeypatch
):
    """If the pipeline raises mid-stream, the lock is still released."""
    from sqlalchemy import select
    from backend.models import ScheduleLock

    async def boom(**kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover
    monkeypatch.setattr(
        "backend.scheduling.graph.run_scheduling_pipeline", boom
    )

    resp = await client.post(
        "/api/v1/schedules/generate",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"week_start_date": "2026-05-18", "use_local": True},
    )
    async for _ in resp.aiter_lines():
        pass

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# POST /schedules/{id}/approve — schedule lock integration
# ---------------------------------------------------------------------------


async def test_approve_returns_409_when_locked(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company,
):
    from datetime import datetime, timedelta, timezone
    from backend.models import ScheduleLock, ShiftSchedule

    sched = ShiftSchedule(
        company_id=seeded_company.company_id,
        location_id=seeded_company.location_id,
        week_start_date=datetime(2026, 5, 18).date(),
        status="draft",
        raw_llm_output="[]",
    )
    db_session.add(sched)
    db_session.add(ScheduleLock(
        company_id=seeded_company.company_id,
        locked_by_user_id=seeded_company.manager_user_id,
        operation="generate",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    ))
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/schedules/{sched.id}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "schedule_locked"


async def test_approve_releases_lock_on_success(
    client: AsyncClient, manager_token: str, db_session: AsyncSession,
    seeded_company,
):
    """Approving a draft schedule must remove the lock row when it completes."""
    from sqlalchemy import select
    from backend.models import ScheduleLock, ShiftSchedule

    sched = ShiftSchedule(
        company_id=seeded_company.company_id,
        location_id=seeded_company.location_id,
        week_start_date=datetime(2026, 5, 18).date(),
        status="draft",
        raw_llm_output="[]",
    )
    db_session.add(sched)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/schedules/{sched.id}/approve",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200

    rows = (await db_session.execute(
        select(ScheduleLock).where(
            ScheduleLock.company_id == seeded_company.company_id
        )
    )).scalars().all()
    assert rows == []
