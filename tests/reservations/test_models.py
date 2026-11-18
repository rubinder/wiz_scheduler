from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from backend.reservations.models import (
    BOOKED,
    SOURCE_STAFF,
    DiningTable,
    Reservation,
    ReservationTable,
)
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BASE = datetime(2030, 1, 1, 19, 0, tzinfo=timezone.utc)


async def _setup(db, party_size: int = 2):
    table = DiningTable(
        company_id=COMPANY_ID, location_id=LOCATION_ID, label="T1",
        min_seats=1, max_seats=4,
    )
    db.add(table)
    await db.flush()
    return table


def _res(party_size: int = 2) -> Reservation:
    return Reservation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=party_size,
        start_at=BASE, end_at=BASE + timedelta(hours=1), status=BOOKED,
        source=SOURCE_STAFF,
    )


def _rt(res, table, start_min, end_min, active=True) -> ReservationTable:
    from sqlalchemy.dialects.postgresql import Range
    return ReservationTable(
        company_id=COMPANY_ID, reservation_id=res.id, table_id=table.id,
        during=Range(BASE + timedelta(minutes=start_min),
                     BASE + timedelta(minutes=end_min), bounds="[)"),
        active=active,
    )


async def _two(db):
    table = await _setup(db)
    r1, r2 = _res(), _res()
    db.add_all([r1, r2])
    await db.flush()
    return table, r1, r2


async def test_overlapping_active_rows_for_one_table_are_rejected(db_session, seed_location):
    table, r1, r2 = await _two(db_session)
    db_session.add(_rt(r1, table, 0, 90))
    await db_session.flush()
    db_session.add(_rt(r2, table, 60, 120))
    with pytest.raises(IntegrityError) as exc:
        await db_session.flush()
    assert "ex_reservation_tables_no_overlap" in str(exc.value.orig)


async def test_inactive_rows_may_overlap(db_session, seed_location):
    table, r1, r2 = await _two(db_session)
    db_session.add(_rt(r1, table, 0, 90))
    db_session.add(_rt(r2, table, 60, 120, active=False))
    await db_session.flush()


async def test_adjacent_ranges_do_not_overlap(db_session, seed_location):
    table, r1, r2 = await _two(db_session)
    db_session.add(_rt(r1, table, 0, 90))
    db_session.add(_rt(r2, table, 90, 180))
    await db_session.flush()


async def test_party_size_must_be_positive(db_session, seed_location):
    db_session.add(_res(party_size=0))
    with pytest.raises(IntegrityError):
        await db_session.flush()
