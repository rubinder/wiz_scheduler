"""Verify the new ORM model + ShiftTemplate.specific_date column."""
from datetime import date, time, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate, SpecialHoursDay


@pytest.mark.asyncio
async def test_shift_template_specific_date_defaults_null(
    db_session: AsyncSession, seed_location, seed_company
):
    tmpl = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Weekly",
        weekly_schedule=[{"day_of_week": 0, "roles": []}],
    )
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    assert tmpl.specific_date is None


@pytest.mark.asyncio
async def test_shift_template_specific_date_can_be_set(
    db_session: AsyncSession, seed_location, seed_company
):
    target = date(2026, 12, 24)
    tmpl = ShiftTemplate(
        company_id=seed_company.id,
        location_id=seed_location.id,
        name="Christmas Eve",
        weekly_schedule=[{"day_of_week": 3, "roles": []}],
        specific_date=target,
    )
    db_session.add(tmpl)
    await db_session.commit()
    await db_session.refresh(tmpl)
    assert tmpl.specific_date == target


@pytest.mark.asyncio
async def test_special_hours_day_round_trip(
    db_session: AsyncSession, seed_location, seed_company
):
    row = SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=date(2026, 12, 24),
        open_time=time(9, 0),
        close_time=time(14, 0),
        label="Christmas Eve",
    )
    db_session.add(row)
    await db_session.commit()
    loaded = (await db_session.execute(
        sa.select(SpecialHoursDay).where(SpecialHoursDay.id == row.id)
    )).scalar_one()
    assert loaded.label == "Christmas Eve"
    assert loaded.shift_template_id is None


@pytest.mark.asyncio
async def test_special_hours_day_unique_per_location_date(
    db_session: AsyncSession, seed_location, seed_company
):
    target = date(2026, 12, 24)
    db_session.add(SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=target,
        open_time=time(9, 0),
        close_time=time(14, 0),
    ))
    await db_session.commit()

    db_session.add(SpecialHoursDay(
        company_id=seed_company.id,
        location_id=seed_location.id,
        date=target,
        open_time=time(10, 0),
        close_time=time(15, 0),
    ))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
