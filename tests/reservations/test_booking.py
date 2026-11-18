import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.reservations import booking
from backend.reservations.booking import (
    DinerContact,
    CodeExhausted,
    HoldExpired,
    InvalidPartySize,
    InvalidTransition,
    PartyTooLarge,
    ReservationNotFound,
    SlotUnavailable,
)
from backend.reservations.availability import Slot
from backend.reservations.codes import ALPHABET, new_code
from backend.reservations.models import (
    BOOKED,
    CANCELLED,
    COMPLETED,
    DECLINED,
    EXPIRED,
    HELD,
    PENDING,
    SEATED,
    SOURCE_WEB_AGENT,
    Reservation,
    ReservationTable,
    TableCombination,
)
from tests.conftest import COMPANY_ID, LOCATION_ID, get_test_session_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NY = ZoneInfo("America/New_York")
DINER = DinerContact(name="Ada", phone="+12125550100", email="ada@example.com")
# Party 3 fits only T2 (T1 seats 2, T3 needs 4, the T1+T2 combination needs 5).
ONLY_T2 = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _at(hh: int, mm: int = 0, now: datetime | None = None) -> datetime:
    d = (now or _now()).astimezone(NY).date() + timedelta(days=7)
    return datetime.combine(d, time(hh, mm), tzinfo=NY)


async def _tables(db, reservation_id: str) -> list[ReservationTable]:
    return list((await db.execute(
        select(ReservationTable).where(ReservationTable.reservation_id == reservation_id)
    )).scalars().all())


async def _hold(db, party: int, start: datetime, now: datetime | None = None) -> Reservation:
    return await booking.hold(db, COMPANY_ID, LOCATION_ID, party, start,
                              source=SOURCE_WEB_AGENT, now=now)


async def test_new_code_uses_the_unambiguous_alphabet():
    codes = {new_code() for _ in range(200)}
    assert all(len(c) == 6 and set(c) <= set(ALPHABET) for c in codes)
    assert len(codes) > 190


async def test_diner_contact_requires_e164():
    with pytest.raises(ValueError):
        DinerContact(name="Ada", phone="212-555-0100")


async def test_hold_then_confirm_books_with_code(db_session, seeded_floor):
    now = _now()
    res = await _hold(db_session, 2, _at(19), now)
    assert res.status == HELD
    assert res.held_until == now + timedelta(minutes=5)
    res = await booking.confirm(db_session, COMPANY_ID, res.id, DINER, now=now)
    assert res.status == BOOKED
    assert res.held_until is None
    assert len(res.confirmation_code) == 6
    assert (res.diner_name, res.diner_phone) == ("Ada", "+12125550100")
    rows = await _tables(db_session, res.id)
    assert [r.table_id for r in rows] == [seeded_floor.t1]
    assert all(r.active for r in rows)
    # during = [start, end + churn): party 2 is 90 min dining + 10 churn.
    assert rows[0].during.lower == _at(19)
    assert rows[0].during.upper == _at(19) + timedelta(minutes=100)
    assert res.end_at == _at(19) + timedelta(minutes=90)


async def test_confirm_after_hold_timeout_raises_hold_expired(db_session, seeded_floor):
    now = _now()
    res = await _hold(db_session, 2, _at(19), now)
    with pytest.raises(HoldExpired):
        await booking.confirm(db_session, COMPANY_ID, res.id, DINER,
                              now=now + timedelta(minutes=6))


async def test_large_party_goes_pending_and_holds_table(db_session, seeded_floor):
    # A combination big enough for 10, so a party of 10 has somewhere to sit.
    db_session.add(TableCombination(
        company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
        table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
    await db_session.flush()
    now = _now()
    res = await _hold(db_session, 10, _at(19), now)
    assert res.status == PENDING
    assert res.held_until == now + timedelta(hours=24)
    assert sorted(r.table_id for r in await _tables(db_session, res.id)) == sorted(
        [seeded_floor.t2, seeded_floor.t3])
    res = await booking.confirm(db_session, COMPANY_ID, res.id, DINER, now=now)
    assert res.status == PENDING and len(res.confirmation_code) == 6
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, ONLY_T2, _at(19), now)
    res = await booking.approve(db_session, COMPANY_ID, res.id, now=now)
    assert res.status == BOOKED and res.held_until is None


async def test_decline_frees_the_tables(db_session, seeded_floor):
    db_session.add(TableCombination(
        company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
        table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
    await db_session.flush()
    now = _now()
    res = await _hold(db_session, 10, _at(19), now)
    res = await booking.decline(db_session, COMPANY_ID, res.id, now=now)
    assert res.status == DECLINED
    assert not any(r.active for r in await _tables(db_session, res.id))
    assert (await _hold(db_session, ONLY_T2, _at(19), now)).status == HELD


async def test_staff_books_a_large_party_directly(db_session, seeded_floor):
    db_session.add(TableCombination(
        company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
        table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
    await db_session.flush()
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 10, _at(19),
                                   DINER, notes="birthday")
    assert res.status == BOOKED and res.source == "staff" and res.notes == "birthday"
    assert res.held_until is None and res.confirmation_code


async def test_staff_booking_ignores_the_cutoff(db_session, seeded_floor):
    now = _at(18, 30)
    start = _at(19)  # 30 min away, inside the 60 min cutoff
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, 2, start, now)
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, start,
                                   DINER, now=now)
    assert res.status == BOOKED


async def test_party_above_max_raises(db_session, seeded_floor):
    with pytest.raises(PartyTooLarge):
        await _hold(db_session, 21, _at(19))


async def test_hold_off_slot_grid_is_unavailable(db_session, seeded_floor):
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, 2, _at(19, 7))
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, 2, _at(23))


async def test_expired_hold_does_not_block_new_hold(db_session, seeded_floor):
    now = _now()
    first = await _hold(db_session, ONLY_T2, _at(19), now)
    with pytest.raises(SlotUnavailable):
        await _hold(db_session, ONLY_T2, _at(19), now)
    later = now + timedelta(minutes=6)
    second = await _hold(db_session, ONLY_T2, _at(19), later)
    assert second.status == HELD
    await db_session.refresh(first)
    assert first.status == EXPIRED
    assert not any(r.active for r in await _tables(db_session, first.id))


async def test_concurrent_holds_for_last_table_one_wins(seeded_floor):
    factory = get_test_session_factory()
    start, now = _at(19), _now()

    async def attempt() -> str:
        async with factory() as db:
            try:
                res = await _hold(db, ONLY_T2, start, now)
                await db.commit()
                return res.status
            except SlotUnavailable:
                await db.rollback()
                return "unavailable"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == [HELD, "unavailable"]


async def test_modify_moves_tables(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    res = await booking.modify(db_session, COMPANY_ID, res.id, party_size=4,
                               start=_at(20), notes="window")
    assert (res.party_size, res.start_at, res.notes) == (4, _at(20), "window")
    rows = await _tables(db_session, res.id)
    assert [r.table_id for r in rows] == [seeded_floor.t2] and rows[0].active
    # 19:00 on T1 is free again.
    assert (await _hold(db_session, 2, _at(19))).status == HELD


async def test_modify_to_unavailable_time_keeps_original(db_session, seeded_floor):
    blocker = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                       _at(20), DINER)
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                   _at(17), DINER)
    with pytest.raises(SlotUnavailable):
        await booking.modify(db_session, COMPANY_ID, res.id, start=_at(20))
    await db_session.refresh(res)
    assert res.start_at == _at(17) and res.status == BOOKED
    rows = await _tables(db_session, res.id)
    assert [r.table_id for r in rows] == [seeded_floor.t2] and rows[0].active
    assert rows[0].during.lower == _at(17)
    assert blocker.status == BOOKED


async def test_status_transitions(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    res = await booking.set_status(db_session, COMPANY_ID, res.id, SEATED)
    assert res.status == SEATED
    res = await booking.set_status(db_session, COMPANY_ID, res.id, COMPLETED)
    assert res.status == COMPLETED
    assert not any(r.active for r in await _tables(db_session, res.id))
    with pytest.raises(InvalidTransition):
        await booking.set_status(db_session, COMPANY_ID, res.id, SEATED)


async def test_cancel_frees_the_table(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                   _at(19), DINER)
    res = await booking.cancel(db_session, COMPANY_ID, res.id)
    assert res.status == CANCELLED
    assert not any(r.active for r in await _tables(db_session, res.id))
    assert (await _hold(db_session, ONLY_T2, _at(19))).status == HELD
    with pytest.raises(InvalidTransition):
        await booking.cancel(db_session, COMPANY_ID, res.id)


async def test_lookup_needs_code_and_phone(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    code = res.confirmation_code
    assert await booking.lookup(db_session, COMPANY_ID, LOCATION_ID, code,
                                "+12125550199") is None
    found = await booking.lookup(db_session, COMPANY_ID, LOCATION_ID, code.lower(),
                                 DINER.phone)
    assert found is not None and found.id == res.id
    assert await booking.lookup(db_session, "zzzzzzzz", LOCATION_ID, code,
                                DINER.phone) is None


async def test_other_company_cannot_touch_a_reservation(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    for call in (
        booking.cancel(db_session, "zzzzzzzz", res.id),
        booking.approve(db_session, "zzzzzzzz", res.id),
        booking.set_status(db_session, "zzzzzzzz", res.id, SEATED),
        booking.confirm(db_session, "zzzzzzzz", res.id, DINER),
        booking.modify(db_session, "zzzzzzzz", res.id, notes="x"),
    ):
        with pytest.raises(ReservationNotFound):
            await call


async def test_confirm_retries_a_colliding_code(db_session, seeded_floor, monkeypatch):
    first = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    codes = iter([first.confirmation_code, "ZZZZZ2"])
    monkeypatch.setattr(booking, "new_code", lambda: next(codes))
    second = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(20), DINER)
    assert second.confirmation_code == "ZZZZZ2"
    assert second.status == BOOKED and second.diner_name == "Ada"


async def test_writers_for_one_location_wait_on_the_location_lock(seeded_floor):
    factory = get_test_session_factory()
    now = _now()
    async with factory() as a, factory() as b:
        await _hold(a, ONLY_T2, _at(19), now)  # uncommitted: a holds the lock
        # A different table, so only the location lock can make b wait.
        b_task = asyncio.create_task(_hold(b, 2, _at(17), now))
        done, _ = await asyncio.wait({b_task}, timeout=1.0)
        assert not done
        await a.commit()
        res = await asyncio.wait_for(b_task, timeout=10)
        assert res.status == HELD
        await b.commit()


async def test_modify_backstop_conflict_keeps_original(db_session, seeded_floor, monkeypatch):
    blocker = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                       _at(20), DINER)
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                   _at(17), DINER)

    # find_slots wrongly offers T2 at 20:00, so only the exclusion constraint stops it.
    async def lying_find_slots(*args, **kwargs):
        return [Slot(_at(20), _at(20) + timedelta(minutes=105), (seeded_floor.t2,),
                     seeded_floor.section_id)]

    monkeypatch.setattr(booking, "find_slots", lying_find_slots)
    with pytest.raises(SlotUnavailable):
        await booking.modify(db_session, COMPANY_ID, res.id, start=_at(20), notes="x")
    monkeypatch.undo()
    assert res.start_at == _at(17) and res.notes is None
    rows = await _tables(db_session, res.id)
    assert [r.table_id for r in rows] == [seeded_floor.t2] and rows[0].active
    assert rows[0].during.lower == _at(17)
    # The session is still usable without a rollback.
    await db_session.refresh(res)
    assert res.start_at == _at(17)
    assert (await _hold(db_session, 2, _at(18))).status == HELD
    assert blocker.status == BOOKED


async def test_in_session_table_rows_see_cancel(db_session, seeded_floor):
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    rows = await _tables(db_session, res.id)  # strong references, no re-query below
    assert rows and all(r.active for r in rows)
    await booking.cancel(db_session, COMPANY_ID, res.id)
    assert all(r.active is False for r in rows)


async def test_in_session_rows_see_the_expiry_sweep(db_session, seeded_floor):
    now = _now()
    first = await _hold(db_session, ONLY_T2, _at(19), now)
    rows = await _tables(db_session, first.id)
    await _hold(db_session, 2, _at(17), now + timedelta(minutes=6))
    assert first.status == EXPIRED
    assert all(r.active is False for r in rows)


async def test_party_size_below_one_is_rejected(db_session, seeded_floor):
    with pytest.raises(InvalidPartySize):
        await _hold(db_session, 0, _at(19))
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    with pytest.raises(InvalidPartySize):
        await booking.modify(db_session, COMPANY_ID, res.id, party_size=0)
    assert InvalidPartySize.code == "invalid_party_size"


async def test_code_exhaustion_raises_a_reservation_error(db_session, seeded_floor,
                                                          monkeypatch):
    first = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    monkeypatch.setattr(booking, "new_code", lambda: first.confirmation_code)
    with pytest.raises(CodeExhausted) as info:
        await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(20), DINER)
    assert info.value.code == "code_exhausted"


# --- final review fixes -----------------------------------------------------

async def test_walk_in_is_seated_now_on_a_fitting_table(db_session, seeded_floor):
    now = _at(18, 7) + timedelta(seconds=42)  # off the slot grid
    res = await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2,
                                     diner_name="Bo", notes="bar side", now=now)
    assert res.status == SEATED and res.source == "staff"
    assert res.diner_phone is None and res.diner_name == "Bo"
    assert res.held_until is None and res.notes == "bar side"
    start = _at(18, 7)
    assert res.start_at == start
    assert res.end_at == start + timedelta(minutes=105)
    rows = await _tables(db_session, res.id)
    assert [r.table_id for r in rows] == [seeded_floor.t2] and rows[0].active
    # during includes churn: party 3 is 105 dining + 15 churn.
    assert rows[0].during.upper == start + timedelta(minutes=120)
    with pytest.raises(SlotUnavailable):
        await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2, now=now)


async def test_walk_in_cannot_take_a_table_booked_soon(db_session, seeded_floor):
    now = _at(18, 7)
    await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2, _at(19),
                             DINER, now=now)
    with pytest.raises(SlotUnavailable):
        await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, ONLY_T2, now=now)
    # A party of 2 still fits T1.
    res = await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, 2, now=now)
    assert [r.table_id for r in await _tables(db_session, res.id)] == [seeded_floor.t1]


async def test_walk_in_validates_party_and_phone(db_session, seeded_floor):
    with pytest.raises(InvalidPartySize):
        await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, 0)
    with pytest.raises(PartyTooLarge):
        await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, 21)
    with pytest.raises(booking.InvalidPhone):
        await booking.seat_walk_in(db_session, COMPANY_ID, LOCATION_ID, 2,
                                   diner_phone="555")


async def test_pending_hold_lapses_at_the_booking_time(db_session, seeded_floor):
    db_session.add(TableCombination(
        company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
        table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
    await db_session.flush()
    now = _at(17)
    res = await _hold(db_session, 10, _at(19), now)
    assert res.status == PENDING
    assert res.held_until == _at(19)  # not now + 24 h
    # A row whose hold outlives its start (e.g. written before this fix)
    # still cannot be approved once the booking time has passed.
    res.held_until = _at(19) + timedelta(hours=20)
    await db_session.flush()
    with pytest.raises(HoldExpired):
        await booking.approve(db_session, COMPANY_ID, res.id, now=_at(19, 1))


async def test_only_staff_may_grow_a_booked_party_past_self_book(db_session, seeded_floor):
    db_session.add(TableCombination(
        company_id=COMPANY_ID, location_id=LOCATION_ID, name="T2+T3",
        table_ids=[seeded_floor.t2, seeded_floor.t3], min_seats=7, max_seats=10))
    await db_session.flush()
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19), DINER)
    with pytest.raises(PartyTooLarge):
        await booking.modify(db_session, COMPANY_ID, res.id, party_size=9)
    # Skipping the cutoff alone does not make the caller staff.
    with pytest.raises(PartyTooLarge):
        await booking.modify(db_session, COMPANY_ID, res.id, party_size=9,
                             apply_cutoff=False)
    res = await booking.modify(db_session, COMPANY_ID, res.id, party_size=9,
                               apply_cutoff=False, by_staff=True)
    assert res.party_size == 9


async def test_staff_booking_needs_no_phone_but_agents_do(db_session, seeded_floor):
    no_phone = DinerContact(name="Walk-up")
    res = await booking.book_staff(db_session, COMPANY_ID, LOCATION_ID, 2, _at(19),
                                   no_phone)
    assert res.status == BOOKED and res.diner_phone is None
    held = await _hold(db_session, ONLY_T2, _at(19))
    with pytest.raises(booking.PhoneRequired):
        await booking.confirm(db_session, COMPANY_ID, held.id, no_phone)
    with pytest.raises(ValueError):
        DinerContact(name="Ada", phone="555")
