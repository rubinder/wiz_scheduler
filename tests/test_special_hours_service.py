"""Unit tests for the clone_template_for_date helper."""
import copy
from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate
from backend.services.special_hours import clone_template_for_date


@pytest.mark.asyncio
async def test_clone_extracts_matching_dow_and_overrides_times(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[
            {"day_of_week": 0, "roles": [{"role_id": "r1", "role_name": "Cashier",
                                          "required_headcount": 2,
                                          "start_time": "09:00", "end_time": "17:00"}]},
            {"day_of_week": 3, "roles": [{"role_id": "r2", "role_name": "Server",
                                          "required_headcount": 3,
                                          "start_time": "11:00", "end_time": "23:00"}]},
        ],
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    # Thursday 2026-12-24 → dow=3 → matches the second entry
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label="Christmas Eve",
    )
    assert clone.specific_date == date(2026, 12, 24)
    assert clone.name == "Weekly — Christmas Eve"
    assert len(clone.weekly_schedule) == 1
    assert clone.weekly_schedule[0]["day_of_week"] == 3
    roles = clone.weekly_schedule[0]["roles"]
    assert len(roles) == 1
    assert roles[0]["role_name"] == "Server"
    assert roles[0]["start_time"] == "09:00:00"
    assert roles[0]["end_time"] == "14:00:00"
    # Source is unmodified
    assert source.weekly_schedule[1]["roles"][0]["start_time"] == "11:00"


@pytest.mark.asyncio
async def test_clone_falls_back_to_first_nonempty_day_when_dow_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Sparse",
        weekly_schedule=[
            {"day_of_week": 0, "roles": []},  # empty Monday
            {"day_of_week": 1, "roles": [{"role_id": "r1", "role_name": "X",
                                          "required_headcount": 1,
                                          "start_time": "08:00", "end_time": "12:00"}]},
        ],
    )
    db_session.add(source)
    await db_session.commit()

    # Sunday 2026-12-27 → dow=6 → not in source → fallback to first non-empty
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 27),
        open_time=time(10, 0),
        close_time=time(15, 0),
        label=None,
    )
    assert clone.weekly_schedule[0]["day_of_week"] == 6  # mirror the target's dow
    assert clone.weekly_schedule[0]["roles"][0]["role_name"] == "X"
    assert clone.weekly_schedule[0]["roles"][0]["start_time"] == "10:00:00"


@pytest.mark.asyncio
async def test_clone_name_falls_back_to_iso_date_when_label_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Base",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(source)
    await db_session.commit()

    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label=None,
    )
    assert clone.name == "Base — 2026-12-24"
