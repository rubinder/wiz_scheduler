"""Call screening (#259 step 7): number rules, the FTC lookup and decide."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location
from backend.reservations import screening
from backend.reservations.screening_models import NumberRule, SpamNumber
from tests.conftest import COMPANY_ID, LOCATION_ID, REGION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
CALLER = "+12125550142"
OTHER_LOCATION_ID = "otherloc"


@pytest_asyncio.fixture
async def locations(db_session: AsyncSession, seed_location: Location) -> None:
    db_session.add(Location(id=OTHER_LOCATION_ID, company_id=COMPANY_ID,
                            region_id=REGION_ID, name="Uptown",
                            timezone="America/New_York"))
    await db_session.commit()


async def _rule(db: AsyncSession, action: str, *, location_id: str | None = LOCATION_ID,
                e164: str = CALLER, expires_at: datetime | None = None) -> None:
    db.add(NumberRule(company_id=COMPANY_ID, location_id=location_id, e164=e164,
                      action=action, expires_at=expires_at))
    await db.commit()


async def _spam(db: AsyncSession, days_ago: int, reports: int, e164: str = CALLER) -> None:
    db.add(SpamNumber(e164=e164, report_date=(NOW - timedelta(days=days_ago)).date(),
                      reports=reports))
    await db.commit()


async def _decide(db: AsyncSession, caller: str | None = CALLER,
                  location_id: str = LOCATION_ID) -> tuple[str, str]:
    return await screening.decide(db, COMPANY_ID, location_id, caller, NOW)


# -- precedence ---------------------------------------------------------------

async def test_no_rules_answers_by_default(db_session, locations):
    assert await _decide(db_session) == ("answer", "default")


async def test_location_allow_beats_location_block(db_session, locations):
    # Two permanent entries for one scope cannot exist (uq_number_rules_permanent);
    # an expiring one beside a permanent one can.
    await _rule(db_session, "block")
    await _rule(db_session, "allow", expires_at=NOW + timedelta(days=1))
    assert await _decide(db_session) == ("answer", "allow_list")


async def test_company_allow_beats_location_block(db_session, locations):
    await _rule(db_session, "block")
    await _rule(db_session, "allow", location_id=None)
    assert await _decide(db_session) == ("answer", "allow_list")


async def test_location_allow_beats_company_block(db_session, locations):
    await _rule(db_session, "block", location_id=None)
    await _rule(db_session, "allow")
    assert await _decide(db_session) == ("answer", "allow_list")


async def test_location_block(db_session, locations):
    await _rule(db_session, "block")
    assert await _decide(db_session) == ("block", "block_list")


async def test_company_block(db_session, locations):
    await _rule(db_session, "block", location_id=None)
    assert await _decide(db_session) == ("block", "block_list")
    assert await _decide(db_session, location_id=OTHER_LOCATION_ID) == (
        "block", "block_list")


async def test_another_locations_rules_do_not_apply(db_session, locations):
    await _rule(db_session, "block", location_id=OTHER_LOCATION_ID)
    assert await _decide(db_session) == ("answer", "default")
    await _rule(db_session, "allow", location_id=OTHER_LOCATION_ID,
                expires_at=NOW + timedelta(days=1))
    await _spam(db_session, 1, 5)
    assert await _decide(db_session) == ("block", "ftc")


async def test_block_beats_ftc_and_allow_beats_ftc(db_session, locations):
    await _spam(db_session, 1, 5)
    assert await _decide(db_session) == ("block", "ftc")
    await _rule(db_session, "block")
    assert await _decide(db_session) == ("block", "block_list")
    await _rule(db_session, "allow", location_id=None)
    assert await _decide(db_session) == ("answer", "allow_list")


async def test_a_different_number_is_unaffected(db_session, locations):
    await _rule(db_session, "block", e164="+12125550100")
    await _spam(db_session, 1, 5, e164="+12125550100")
    assert await _decide(db_session) == ("answer", "default")


async def test_rules_are_per_company(db_session, locations):
    # decide filters by company explicitly, not only by the tenant context.
    assert await screening.decide(db_session, "othercom", LOCATION_ID, CALLER, NOW) == (
        "answer", "default")


# -- expiry ---------------------------------------------------------------------

async def test_expired_entries_are_ignored(db_session, locations):
    await _rule(db_session, "allow", location_id=None, expires_at=NOW - timedelta(seconds=1))
    await _rule(db_session, "block", expires_at=NOW)  # not greater than now: expired
    assert await _decide(db_session) == ("answer", "default")
    await _rule(db_session, "block", location_id=None, expires_at=NOW + timedelta(hours=1))
    assert await _decide(db_session) == ("block", "block_list")


# -- withheld -----------------------------------------------------------------

@pytest.mark.parametrize("caller", [None, "", "anonymous", "Anonymous", "12125550142",
                                    "+0123", "+1 212 555 0142"])
async def test_withheld_or_invalid_caller_is_answered(db_session, locations, caller):
    await _rule(db_session, "block", location_id=None, e164=caller or "x")
    assert await _decide(db_session, caller=caller) == ("answer", "withheld")


# -- FTC ------------------------------------------------------------------------

async def test_ftc_threshold(db_session, locations):
    assert settings.SPAM_MIN_REPORTS == 3 and settings.SPAM_WINDOW_DAYS == 90
    await _spam(db_session, 1, 2)
    assert not await screening.is_ftc_blocked(db_session, CALLER, NOW)
    await _spam(db_session, 30, 1)  # summed across days in the window
    assert await screening.is_ftc_blocked(db_session, CALLER, NOW)


async def test_ftc_reports_outside_the_window_do_not_count(db_session, locations):
    await _spam(db_session, 1, 2)
    await _spam(db_session, settings.SPAM_WINDOW_DAYS, 10)  # 91 dates incl. today
    assert not await screening.is_ftc_blocked(db_session, CALLER, NOW)
    await _spam(db_session, settings.SPAM_WINDOW_DAYS - 1, 1)  # the window's first day
    assert await screening.is_ftc_blocked(db_session, CALLER, NOW)


async def test_ftc_threshold_is_a_setting(db_session, locations, monkeypatch):
    await _spam(db_session, 1, 2)
    monkeypatch.setattr(settings, "SPAM_MIN_REPORTS", 2)
    assert await screening.is_ftc_blocked(db_session, CALLER, NOW)
    monkeypatch.setattr(settings, "SPAM_WINDOW_DAYS", 0)
    assert not await screening.is_ftc_blocked(db_session, CALLER, NOW)
