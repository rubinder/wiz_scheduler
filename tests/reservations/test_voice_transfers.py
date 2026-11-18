"""Toll-fraud bounds on the AI host's staff transfer (#259 step 6): which
numbers it may dial, and the per-location daily transfer counter."""

import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from backend.reservations import voice_transfers
from backend.reservations.voice_number_models import VoiceTransferCount
from tests.conftest import COMPANY_ID, LOCATION_ID, get_test_session_factory

@pytest.mark.parametrize("number", [
    "+12125550111",  # New York
    "+14165550100",  # Toronto
    "+16045550100",  # Vancouver
    "+18005550100",  # toll-free
])
def test_us_and_canada_numbers_are_allowed(number):
    assert voice_transfers.nanp_problem(number) is None


@pytest.mark.parametrize("number", [
    "+18765550100",  # Jamaica
    "+18095550100",  # Dominican Republic
    "+18295550100",  # Dominican Republic
    "+12425550100",  # Bahamas
    "+14415550100",  # Bermuda
    "+17875550100",  # Puerto Rico: not sure it is domestic-rated, so blocked
    "+16715550100",  # Guam
    "+19005550100",  # premium rate
    "+19765550100",  # premium rate
    "+12129765555",  # 976 exchange
    "+14115550100",  # N11 is not an area code
    "+11125550100",  # NPA must start 2-9
    "+12925550100",  # middle digit 9 is reserved
    "+12121234567",  # exchange must start 2-9
    "+1212555010",   # too short
    "+121255501000",  # too long
    "+442071234567",  # not NANP
])
def test_other_numbers_are_refused(number):
    assert voice_transfers.nanp_problem(number) is not None


@pytest.mark.asyncio
async def test_reserve_stops_at_the_cap(db_session, seed_location):
    day = date(2026, 11, 18)
    assert await voice_transfers.reserve(db_session, COMPANY_ID, LOCATION_ID, day, 2)
    assert await voice_transfers.reserve(db_session, COMPANY_ID, LOCATION_ID, day, 2)
    assert not await voice_transfers.reserve(db_session, COMPANY_ID, LOCATION_ID, day, 2)
    # Another day starts again.
    assert await voice_transfers.reserve(db_session, COMPANY_ID, LOCATION_ID,
                                         date(2026, 11, 19), 2)
    await db_session.commit()
    rows = (await db_session.execute(
        select(VoiceTransferCount.local_date, VoiceTransferCount.count)
        .order_by(VoiceTransferCount.local_date))).all()
    assert [tuple(r) for r in rows] == [(day, 2), (date(2026, 11, 19), 1)]


@pytest.mark.asyncio
async def test_concurrent_reservations_at_the_cap_let_one_through(db_session, seed_location):
    """Cap 1, two requests at once: the second waits on the first's row and,
    once it commits, finds the counter full."""
    day = date(2026, 11, 18)
    factory = get_test_session_factory()
    async with factory() as first, factory() as second:
        assert await voice_transfers.reserve(first, COMPANY_ID, LOCATION_ID, day, 1)
        racing = asyncio.create_task(
            voice_transfers.reserve(second, COMPANY_ID, LOCATION_ID, day, 1))
        await asyncio.sleep(0.3)
        assert not racing.done()  # blocked on the first's uncommitted row
        await first.commit()
        assert await racing is False
        await second.commit()
