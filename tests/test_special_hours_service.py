"""Unit tests for the clone_template_for_date helper."""
from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate
from backend.services.special_hours import clone_template_for_date


# Helper: pull entries from the clone keyed by role_name for stable assertions.
def _by_role(entries):
    return {e["role_name"]: e for e in entries}


@pytest.mark.asyncio
async def test_clone_from_legacy_flat_shape_extracts_target_day(
    db_session: AsyncSession, seed_location, seed_company
):
    """The seed / production shape: each role+day is a flat entry. The clone
    should extract entries matching the target day name and emit the same
    flat shape with overridden times."""
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekday Standard",
        weekly_schedule=[
            {"day": "Thursday", "role_id": "r1", "role_name": "Floor Associate",
             "headcount": 3, "start_time": "09:00", "end_time": "17:00"},
            {"day": "Thursday", "role_id": "r2", "role_name": "Team Lead",
             "headcount": 1, "start_time": "09:00", "end_time": "17:00"},
            {"day": "Friday", "role_id": "r1", "role_name": "Floor Associate",
             "headcount": 4, "start_time": "09:00", "end_time": "17:00"},
        ],
    )
    db_session.add(source)
    await db_session.commit()

    # Thursday 2026-12-24 → dow=3 → matches the two Thursday entries
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label="Christmas Eve",
    )
    assert clone.specific_date == date(2026, 12, 24)
    assert clone.name == "Weekday Standard — Christmas Eve"

    # Flat-shape output (one entry per role/day, NOT dow-grouped)
    entries = clone.weekly_schedule
    assert len(entries) == 2
    by_role = _by_role(entries)
    for name in ("Floor Associate", "Team Lead"):
        e = by_role[name]
        assert e["day"] == "Thursday"
        assert e["start_time"] == "09:00"
        assert e["end_time"] == "14:00"
        assert e["role_id"] in ("r1", "r2")
    assert by_role["Floor Associate"]["headcount"] == 3
    assert by_role["Team Lead"]["headcount"] == 1

    # Source is unmodified
    assert source.weekly_schedule[0]["start_time"] == "09:00"
    assert source.weekly_schedule[0]["end_time"] == "17:00"


@pytest.mark.asyncio
async def test_clone_from_dow_grouped_shape_unrolls_into_flat(
    db_session: AsyncSession, seed_location, seed_company
):
    """If the source happens to be in the dow-grouped shape (older clones or
    externally-built templates), unroll its roles into flat entries."""
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="DowShaped",
        weekly_schedule=[
            {"day_of_week": 3, "roles": [
                {"role_id": "r1", "role_name": "Server",
                 "headcount": 2, "start_time": "11:00", "end_time": "23:00"},
            ]},
        ],
    )
    db_session.add(source)
    await db_session.commit()

    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 24),  # Thursday
        open_time=time(10, 0),
        close_time=time(15, 0),
        label=None,
    )
    assert len(clone.weekly_schedule) == 1
    e = clone.weekly_schedule[0]
    assert e["day"] == "Thursday"
    assert e["role_name"] == "Server"
    assert e["start_time"] == "10:00"
    assert e["end_time"] == "15:00"
    assert e["headcount"] == 2


@pytest.mark.asyncio
async def test_clone_falls_back_to_busiest_day_when_target_dow_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    """No entries for target dow → fall back to the day with the most entries;
    rewrite their ``day`` to the target."""
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekday Only",
        weekly_schedule=[
            {"day": "Monday", "role_id": "r1", "role_name": "X",
             "headcount": 1, "start_time": "08:00", "end_time": "16:00"},
            {"day": "Tuesday", "role_id": "r1", "role_name": "X",
             "headcount": 1, "start_time": "08:00", "end_time": "16:00"},
            {"day": "Tuesday", "role_id": "r2", "role_name": "Y",
             "headcount": 1, "start_time": "08:00", "end_time": "16:00"},
        ],
    )
    db_session.add(source)
    await db_session.commit()

    # Sunday 2026-12-27 → dow=6 → not in source. Tuesday has most entries.
    clone = await clone_template_for_date(
        db_session,
        source=source,
        target_date=date(2026, 12, 27),
        open_time=time(10, 0),
        close_time=time(15, 0),
        label=None,
    )
    entries = clone.weekly_schedule
    assert len(entries) == 2
    assert {e["role_name"] for e in entries} == {"X", "Y"}
    for e in entries:
        assert e["day"] == "Sunday"
        assert e["start_time"] == "10:00"
        assert e["end_time"] == "15:00"


@pytest.mark.asyncio
async def test_clone_name_falls_back_to_iso_date_when_label_missing(
    db_session: AsyncSession, seed_location, seed_company
):
    source = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Base",
        weekly_schedule=[{"day": "Thursday", "role_id": "r1",
                          "role_name": "X", "headcount": 1,
                          "start_time": "09:00", "end_time": "17:00"}],
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
