"""Tests for /api/v1/special-hours endpoints."""
from datetime import date, time

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate, SpecialHoursDay


@pytest.mark.asyncio
async def test_create_with_explicit_draft_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_company, seed_shift_template
):
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "label": "Christmas Eve",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "Christmas Eve"
    assert body["shift_template_id"]

    cloned = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == body["shift_template_id"])
    )).scalar_one()
    assert cloned.specific_date == date(2026, 12, 24)
    assert cloned.location_id == seed_location.id


@pytest.mark.asyncio
async def test_create_picks_recurring_when_no_draft_template_provided(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-25",
            "open_time": "10:00",
            "close_time": "16:00",
        },
    )
    assert resp.status_code == 200
    cloned_id = resp.json()["shift_template_id"]
    cloned = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == cloned_id)
    )).scalar_one()
    assert cloned.name.startswith(seed_shift_template.name + " — ")


@pytest.mark.asyncio
async def test_create_400_when_location_has_no_recurring_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location
):
    # seed_shift_template fixture NOT pulled → no recurring template exists
    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "no_recurring_template"


@pytest.mark.asyncio
async def test_create_409_on_duplicate_location_date(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    first = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "10:00",
            "close_time": "15:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "duplicate"


@pytest.mark.asyncio
async def test_list_filters_by_location_and_date_range(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    for d in ("2026-12-24", "2026-12-25", "2027-01-01"):
        await client.post(
            "/api/v1/special-hours/",
            headers={"Authorization": f"Bearer {manager_token}"},
            json={
                "location_id": seed_location.id,
                "date": d,
                "open_time": "09:00",
                "close_time": "14:00",
                "draft_template_id": seed_shift_template.id,
            },
        )

    resp = await client.get(
        "/api/v1/special-hours/?from_date=2026-12-25&to_date=2027-01-01",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {row["date"] for row in body} == {"2026-12-25", "2027-01-01"}


@pytest.mark.asyncio
async def test_put_updates_times_and_propagates_to_template(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    create = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    sh_id = create.json()["id"]

    upd = await client.put(
        f"/api/v1/special-hours/{sh_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"open_time": "10:00", "close_time": "13:00", "label": "Half day"},
    )
    assert upd.status_code == 200

    tmpl_id = upd.json()["shift_template_id"]
    tmpl = (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
    )).scalar_one()
    roles = tmpl.weekly_schedule[0]["roles"]
    if roles:
        assert roles[0]["start_time"] == "10:00:00"
        assert roles[0]["end_time"] == "13:00:00"


@pytest.mark.asyncio
async def test_delete_removes_both_rows(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template
):
    create = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": seed_location.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    sh_id = create.json()["id"]
    tmpl_id = create.json()["shift_template_id"]

    delete = await client.delete(
        f"/api/v1/special-hours/{sh_id}",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert delete.status_code == 204

    assert (await db_session.execute(
        select(SpecialHoursDay).where(SpecialHoursDay.id == sh_id)
    )).scalar_one_or_none() is None
    assert (await db_session.execute(
        select(ShiftTemplate).where(ShiftTemplate.id == tmpl_id)
    )).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cross_company_isolation(
    client: AsyncClient, db_session: AsyncSession,
    manager_token: str, seed_location, seed_shift_template,
):
    from backend.models import Company, Location, Region, OwnershipGroup
    other_og = OwnershipGroup(name="Other OG")
    other_co = Company(name="Other Co", slug="other-co", ownership_group_id=None)
    db_session.add_all([other_og, other_co])
    await db_session.flush()
    other_region = Region(company_id=other_co.id, name="R")
    db_session.add(other_region)
    await db_session.flush()
    other_loc = Location(
        company_id=other_co.id, region_id=other_region.id,
        name="OtherLoc", timezone="UTC",
    )
    db_session.add(other_loc)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/special-hours/",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "location_id": other_loc.id,
            "date": "2026-12-24",
            "open_time": "09:00",
            "close_time": "14:00",
            "draft_template_id": seed_shift_template.id,
        },
    )
    assert resp.status_code == 404  # location not found within caller's Company
