"""Location timezones are real IANA zones; blank bulk rows use the caller's."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from tests.conftest import REGION_ID

BASE = "/api/v1/locations"


async def test_create_rejects_a_non_iana_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"region_id": REGION_ID, "name": "X", "timezone": "Eastern Time"},
    )
    assert resp.status_code == 422


async def test_create_accepts_an_iana_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await client.post(
        f"{BASE}/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"region_id": REGION_ID, "name": "X", "timezone": "America/Chicago"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone"] == "America/Chicago"


async def test_update_rejects_a_non_iana_zone(
    client: AsyncClient, manager_token: str, seed_location,
):
    resp = await client.put(
        f"{BASE}/{seed_location.id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"timezone": "Mars/Olympus"},
    )
    assert resp.status_code == 422


async def _bulk(client, token, csv_text, data=None):
    return await client.post(
        f"{BASE}/bulk-upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("locations.csv", csv_text, "text/csv")},
        data=data or {},
    )


async def test_bulk_blank_rows_use_the_default_timezone(
    client: AsyncClient, manager_token: str, seed_region, db_session: AsyncSession,
):
    region_name = seed_region.name
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nBlank Store,{region_name},,\n",
        data={"default_timezone": "America/Denver"},
    )
    assert resp.status_code == 200, resp.text
    loc = (await db_session.execute(
        select(Location).where(Location.name == "Blank Store")
    )).scalar_one()
    assert loc.timezone == "America/Denver"


async def test_bulk_blank_rows_fall_back_to_utc_without_a_default(
    client: AsyncClient, manager_token: str, seed_region, db_session: AsyncSession,
):
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nNo Default,{seed_region.name},,\n",
    )
    assert resp.status_code == 200, resp.text
    loc = (await db_session.execute(
        select(Location).where(Location.name == "No Default")
    )).scalar_one()
    assert loc.timezone == "UTC"


async def test_bulk_skips_a_row_with_an_invalid_zone(
    client: AsyncClient, manager_token: str, seed_region,
):
    resp = await _bulk(
        client, manager_token,
        f"name,region_name,address,timezone\nBad Zone,{seed_region.name},,Eastern Time\n",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 0
    assert any("timezone" in e for e in body["errors"])
