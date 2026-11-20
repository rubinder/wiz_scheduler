from datetime import date, time

import pytest
from pydantic import ValidationError

from backend.reservations.rules import (
    DateOverride,
    Persona,
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


# -- automated-assistant disclosure (#259 launch task 5) -----------------------


async def test_a_custom_greeting_must_disclose_the_automated_host():
    with pytest.raises(ValidationError, match="automated"):
        Persona(greeting="Ciao! Welcome to Luigi's.")
    with pytest.raises(ValidationError, match="automated"):
        RulesSchema.model_validate({"persona": {"greeting": "Hello there"}})
    # "Assistant" alone does not say the host is a machine: a person can be one.
    with pytest.raises(ValidationError, match="automated"):
        Persona(greeting="Luigi's reservations assistant here.")
    for ok in ("", "Hi, you've reached Luigi's AUTOMATED host.",
               "Luigi's automated reservations assistant here."):
        assert Persona(greeting=ok).greeting == ok


async def test_the_default_greetings_say_automated():
    from backend.reservations.agent.gate import default_greeting
    assert "automated" in default_greeting("Luigi's").lower()


async def test_stored_rules_with_an_old_greeting_still_read(db_session, seed_location):
    """Validation is on write only: rules stored before the disclosure rule
    keep reading (the greeting falls back to the default when rendered)."""
    from backend.reservations.models import ReservationRules
    payload = RulesSchema().model_dump(mode="json")
    payload["persona"]["greeting"] = "Ciao! Welcome to Luigi's."
    db_session.add(ReservationRules(company_id=COMPANY_ID, location_id=LOCATION_ID,
                                    rules=payload, version=3))
    await db_session.flush()
    rules, version = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    assert version == 3 and rules.persona.greeting == "Ciao! Welcome to Luigi's."


async def test_the_prompt_never_carries_a_greeting_without_the_disclosure():
    from backend.reservations.agent.prompt import build_system_prompt
    from backend.reservations.rules import STORED
    for stored in ("Ciao! Welcome to Luigi's.", "Ciao! Luigi's assistant here."):
        old = RulesSchema.model_validate({"persona": {"greeting": stored}}, context=STORED)
        assert "Luigi" not in build_system_prompt("Fixture", "America/New_York", old, "web")
    ok = RulesSchema(persona={"greeting": "Luigi's automated host here."})
    assert "Luigi's automated host here." in build_system_prompt(
        "Fixture", "America/New_York", ok, "voice")
