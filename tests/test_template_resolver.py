"""Tests for resolve_templates_for_week."""
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate
from backend.scheduling.template_resolver import (
    LocationMissingTemplate,
    resolve_templates_for_week,
)


WEEK = [date(2026, 12, 21) + timedelta(days=i) for i in range(7)]  # Mon..Sun


@pytest.mark.asyncio
async def test_resolver_picks_specific_date_when_present(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[{"day_of_week": i, "roles": []} for i in range(7)],
    )
    special = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Xmas Eve",
        weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=date(2026, 12, 24),
    )
    db_session.add_all([recurring, special])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    assert result[date(2026, 12, 24)].id == special.id
    for d in WEEK:
        if d != date(2026, 12, 24):
            assert result[d].id == recurring.id


@pytest.mark.asyncio
async def test_resolver_returns_recurring_when_no_override(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(recurring)
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    for d in WEEK:
        assert result[d].id == recurring.id


@pytest.mark.asyncio
async def test_resolver_honours_selected_template_ids(
    db_session: AsyncSession, seed_location, seed_company
):
    a = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="A",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    b = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="B",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add_all([a, b])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=[b.id],
    )
    for d in WEEK:
        assert result[d].id == b.id


@pytest.mark.asyncio
async def test_resolver_raises_when_no_recurring_and_no_override(
    db_session: AsyncSession, seed_location
):
    with pytest.raises(LocationMissingTemplate):
        await resolve_templates_for_week(
            db_session,
            location_id=seed_location.id,
            week_dates=WEEK,
            selected_template_ids=None,
        )


@pytest.mark.asyncio
async def test_resolver_ignores_specific_dates_outside_the_week(
    db_session: AsyncSession, seed_location, seed_company
):
    recurring = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    outside = ShiftTemplate(
        company_id=seed_company.id, location_id=seed_location.id,
        name="Outside",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
        specific_date=date(2027, 1, 15),
    )
    db_session.add_all([recurring, outside])
    await db_session.commit()

    result = await resolve_templates_for_week(
        db_session,
        location_id=seed_location.id,
        week_dates=WEEK,
        selected_template_ids=None,
    )
    for d in WEEK:
        assert result[d].id == recurring.id
