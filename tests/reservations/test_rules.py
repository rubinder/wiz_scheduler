from datetime import date, time

import pytest
from pydantic import ValidationError

from backend.reservations.rules import (
    DateOverride,
    RulesSchema,
    ServicePeriod,
    get_rules,
    save_rules,
)
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _period(**kw):
    base = dict(name="Dinner", weekdays=[4, 5], first_seating=time(17),
                last_seating=time(21))
    base.update(kw)
    return ServicePeriod(**base)


async def test_defaults_are_valid_and_duration_bands_apply():
    r = RulesSchema()
    assert r.duration_for(2) == 90
    assert r.duration_for(5) == 120
    assert r.duration_for(500) == 150
    assert r.band_for(3).churn_minutes == 15


async def test_period_crossing_midnight_is_rejected():
    with pytest.raises(ValidationError):
        _period(first_seating=time(22), last_seating=time(1))


async def test_period_weekdays_must_be_unique_and_in_range():
    with pytest.raises(ValidationError):
        _period(weekdays=[1, 1])
    with pytest.raises(ValidationError):
        _period(weekdays=[7])


async def test_max_party_size_below_self_book_is_rejected():
    with pytest.raises(ValidationError):
        RulesSchema(max_self_book_party=8, max_party_size=6)


async def test_bad_transfer_number_is_rejected():
    with pytest.raises(ValidationError):
        RulesSchema(staff_transfer_number="555-1234")
    assert RulesSchema(staff_transfer_number="+15551234567")


async def test_duration_bands_must_increase():
    with pytest.raises(ValidationError):
        RulesSchema(durations=[
            {"max_party": 4, "minutes": 90, "churn_minutes": 10},
            {"max_party": 4, "minutes": 100, "churn_minutes": 10},
        ])


async def test_periods_for_weekly_override_and_closure():
    r = RulesSchema(
        service_periods=[_period(weekdays=[0])],
        date_overrides=[
            DateOverride(date=date(2030, 1, 8), periods=[]),
            DateOverride(date=date(2030, 1, 9), periods=[_period(name="Special")]),
        ],
        closures=[date(2030, 1, 14)],
    )
    assert [p.name for p in r.periods_for(date(2030, 1, 7))] == ["Dinner"]
    assert r.periods_for(date(2030, 1, 8)) == []
    assert [p.name for p in r.periods_for(date(2030, 1, 9))] == ["Special"]
    assert r.periods_for(date(2030, 1, 14)) == []
    assert r.periods_for(date(2030, 1, 10)) == []


async def test_duplicate_override_dates_rejected():
    with pytest.raises(ValidationError):
        RulesSchema(date_overrides=[
            DateOverride(date=date(2030, 1, 8), periods=[]),
            DateOverride(date=date(2030, 1, 8), periods=[]),
        ])


async def test_save_rules_bumps_version(db_session, seed_location):
    rules, version = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert version == 0 and rules == RulesSchema()
    assert await save_rules(db_session, COMPANY_ID, LOCATION_ID,
                            RulesSchema(cutoff_minutes=30)) == 1
    assert await save_rules(db_session, COMPANY_ID, LOCATION_ID,
                            RulesSchema(cutoff_minutes=45,
                                        service_periods=[_period()])) == 2
    rules, version = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert version == 2
    assert rules.cutoff_minutes == 45
    assert rules.service_periods[0].first_seating == time(17)


@pytest.mark.parametrize("field", [
    "max_concurrent_covers", "max_covers_per_slot", "max_self_book_party",
    "max_party_size",
])
async def test_cover_and_party_limits_must_be_positive(field):
    with pytest.raises(ValidationError):
        RulesSchema(**{field: 0})


async def test_section_cover_limits_must_be_positive():
    with pytest.raises(ValidationError):
        RulesSchema(section_cover_limits={"patio": 0})
    assert RulesSchema(section_cover_limits={"patio": 1}).section_cover_limits == {"patio": 1}
