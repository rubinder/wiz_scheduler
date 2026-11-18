"""Archiving a location refuses while diners are still expected (decision 13)."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.reservations import booking
from backend.reservations.booking import DinerContact
from backend.reservations.models import SOURCE_WEB_AGENT
from backend.services.archive import LocationHasReservations, archive_location
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
DINER = DinerContact(name="Ada", phone="+12125550100")


def _at(hh: int) -> datetime:
    d = datetime.now(timezone.utc).astimezone(NY).date() + timedelta(days=7)
    return datetime.combine(d, time(hh), tzinfo=NY)


@pytest.mark.parametrize("kind", ["booked", "live_hold", "pending"])
async def test_upcoming_reservations_block_archiving(db_session, seeded_floor, kind):
    if kind == "booked":
        await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    elif kind == "live_hold":
        await booking.hold(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19),
                           source=SOURCE_WEB_AGENT)
    else:
        rules_party = 10
        from backend.reservations.models import TableCombination
        db_session.add(TableCombination(
            company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
            table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
        await db_session.flush()
        res = await booking.hold(db_session, COMPANY_ID, LOCATION_ID, rules_party,
                                 _at(19), source=SOURCE_WEB_AGENT)
        assert res.status == "pending_approval"
    loc = seeded_floor.location
    with pytest.raises(LocationHasReservations) as info:
        await archive_location(db_session, COMPANY_ID, loc)
    assert info.value.code == "location_has_reservations"
    assert loc.archived_at is None


async def test_lapsed_cancelled_and_past_reservations_do_not_block(db_session, seeded_floor):
    now = datetime.now(timezone.utc)
    await booking.hold(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19),
                       source=SOURCE_WEB_AGENT, now=now - timedelta(minutes=10))
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 3, _at(19), DINER)
    await booking.cancel(db_session, COMPANY_ID, res.id)
    # A party seated an hour ago has started: not upcoming.
    await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, 4,
                               now=now - timedelta(hours=1))
    loc = seeded_floor.location
    await archive_location(db_session, COMPANY_ID, loc)
    assert loc.archived_at is not None
