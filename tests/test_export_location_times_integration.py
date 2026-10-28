"""The Export page and JSONL download show shift times in the shift's own
location zone, not the UTC face Postgres hands back (#214)."""

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, Shift, ShiftSchedule
from tests.conftest import COMPANY_ID, EMPLOYEE1_ID, LOCATION_ID, REGION_ID, ROLE_FLOOR_ID, _id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
LA = ZoneInfo("America/Los_Angeles")
DAY = date(2026, 11, 2)


@pytest_asyncio.fixture
async def week(db_session: AsyncSession, seed_employees: list[Employee]) -> dict:
    """An approved New York schedule with a NY 9-to-5 and, materialised at a
    second (Los Angeles) location, an LA 10-to-6."""
    la_id = _id()
    db_session.add(Location(
        id=la_id, company_id=COMPANY_ID, region_id=REGION_ID,
        name="West Store", timezone="America/Los_Angeles",
    ))
    sid = _id()
    db_session.add(ShiftSchedule(
        id=sid, company_id=COMPANY_ID, location_id=LOCATION_ID,
        week_start_date=DAY, status="approved",
        created_at=datetime.now(timezone.utc),
    ))
    await db_session.flush()
    ny_shift, la_shift = _id(), _id()
    for shift_id, loc_id, zone, start, end in (
        (ny_shift, LOCATION_ID, NY, 9, 17),
        (la_shift, la_id, LA, 10, 18),
    ):
        db_session.add(Shift(
            id=shift_id, company_id=COMPANY_ID, shift_schedule_id=sid,
            location_id=loc_id, employee_id=EMPLOYEE1_ID, role_id=ROLE_FLOOR_ID,
            role_name="Floor Associate", date=DAY,
            start_time=datetime(2026, 11, 2, start, tzinfo=zone),
            end_time=datetime(2026, 11, 2, end, tzinfo=zone),
        ))
    await db_session.commit()
    return {"schedule_id": sid, "ny": ny_shift, "la": la_shift}


def _same_instant(iso: str, expected: datetime) -> bool:
    return datetime.fromisoformat(iso) == expected


async def test_approved_schedules_list_shows_location_wall_clock(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/approved-schedules?week_start_date={DAY.isoformat()}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    shifts = {s["id"]: s for s in resp.json()[0]["shifts"]}

    ny = shifts[week["ny"]]
    assert ny["start_time"].startswith("2026-11-02T09:00:00"), ny["start_time"]
    assert ny["end_time"].startswith("2026-11-02T17:00:00"), ny["end_time"]
    assert ny["start_time"].endswith("-05:00")
    # Same instant as stored; only the face changed.
    assert _same_instant(ny["start_time"], datetime(2026, 11, 2, 9, tzinfo=NY))

    la = shifts[week["la"]]
    assert la["start_time"].startswith("2026-11-02T10:00:00"), la["start_time"]
    assert la["end_time"].startswith("2026-11-02T18:00:00"), la["end_time"]


async def test_jsonl_export_shows_location_wall_clock(client, manager_token, week):
    resp = await client.get(
        f"/api/v1/export/jsonl/{week['schedule_id']}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200, resp.text
    rows = {r["shift_id"]: r for r in map(json.loads, resp.text.strip().splitlines())}
    assert rows[week["ny"]]["start_time"].startswith("2026-11-02T09:00:00")
    assert rows[week["ny"]]["end_time"].startswith("2026-11-02T17:00:00")
    assert rows[week["la"]]["start_time"].startswith("2026-11-02T10:00:00")
    assert _same_instant(rows[week["la"]]["end_time"], datetime(2026, 11, 2, 18, tzinfo=LA))
