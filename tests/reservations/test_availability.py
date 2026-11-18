from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects.postgresql import Range

from backend.reservations.availability import (
    Busy,
    ComboSpec,
    TableSpec,
    compute_slots,
    find_slots,
)
from backend.reservations.models import (
    BOOKED,
    CANCELLED,
    HELD,
    PENDING,
    SOURCE_STAFF,
    DiningTable,
    Reservation,
    ReservationTable,
)
from backend.reservations.rules import (
    DateOverride,
    DurationBand,
    RulesSchema,
    ServicePeriod,
    save_rules,
)
from tests.conftest import COMPANY_ID, LOCATION_ID

TZ = "America/New_York"
NY = ZoneInfo(TZ)
DAY = date(2026, 12, 2)  # a Wednesday
NOW = datetime(2026, 12, 1, 12, 0, tzinfo=NY)


def _at(hh: int, mm: int = 0, d: date = DAY) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=NY)


def _rules(first=time(17, 0), last=time(21, 0), interval=30, **kw) -> RulesSchema:
    kw.setdefault("cutoff_minutes", 0)
    return RulesSchema(
        service_periods=[ServicePeriod(
            name="dinner", weekdays=list(range(7)),
            first_seating=first, last_seating=last)],
        slot_interval_minutes=interval, **kw)


def _t(id_: str, lo: int, hi: int, section: str | None = None) -> TableSpec:
    return TableSpec(id=id_, label=id_, section_id=section, min_seats=lo, max_seats=hi)


def _busy(rid: str, table: str, start: datetime, dining_min: int, churn_min: int,
          party: int, section: str | None = None) -> Busy:
    dining_end = start + timedelta(minutes=dining_min)
    return Busy(reservation_id=rid, table_id=table, start=start,
                end=dining_end + timedelta(minutes=churn_min),
                dining_end=dining_end, party_size=party, section_id=section)


def _slots(rules, tables, party, combos=(), busy=(), d=DAY, now=NOW, **kw):
    start = datetime.combine(d, time(0), tzinfo=NY)
    return compute_slots(rules, TZ, list(tables), list(combos), list(busy), party,
                         start, start + timedelta(days=1), now, **kw)


def _starts(slots) -> list[tuple[int, int]]:
    return [(s.start.hour, s.start.minute) for s in slots]


def test_slots_follow_period_interval_and_duration():
    slots = _slots(_rules(), [_t("T1", 1, 4)], 2)
    assert len(slots) == 9
    assert _starts(slots)[0] == (17, 0) and _starts(slots)[-1] == (21, 0)
    assert slots[0].end - slots[0].start == timedelta(minutes=90)
    assert slots[0].table_ids == ("T1",)
    assert slots[0].start.tzinfo is not None
    assert slots[0].start.utcoffset() == timedelta(hours=-5)


def test_cutoff_and_horizon_trim_the_window():
    rules = _rules(cutoff_minutes=60, booking_horizon_days=10)
    now = _at(16, 30)
    assert _starts(_slots(rules, [_t("T1", 1, 4)], 2, now=now))[0] == (17, 30)
    far = DAY + timedelta(days=11)
    assert _slots(rules, [_t("T1", 1, 4)], 2, d=far, now=now) == []
    edge = DAY + timedelta(days=10)
    assert len(_slots(rules, [_t("T1", 1, 4)], 2, d=edge, now=now)) == 9


def test_staff_bookings_skip_the_cutoff_but_not_the_horizon():
    rules = _rules(cutoff_minutes=60, booking_horizon_days=10)
    now = _at(16, 30)
    slots = _slots(rules, [_t("T1", 1, 4)], 2, now=now, apply_cutoff=False)
    assert _starts(slots)[0] == (17, 0)
    far = DAY + timedelta(days=11)
    assert _slots(rules, [_t("T1", 1, 4)], 2, d=far, now=now, apply_cutoff=False) == []


def test_slots_before_now_are_never_offered_even_without_cutoff():
    slots = _slots(_rules(), [_t("T1", 1, 4)], 2, now=_at(18, 10), apply_cutoff=False)
    assert _starts(slots)[0] == (18, 30)


def test_closure_date_has_no_slots():
    assert _slots(_rules(closures=[DAY]), [_t("T1", 1, 4)], 2) == []


def test_churn_blocks_the_table_until_reset():
    rules = _rules(interval=15)
    busy = [_busy("r1", "T1", _at(18, 0), 90, 15, 2)]  # Busy.end 19:45
    starts = _starts(_slots(rules, [_t("T1", 1, 4)], 2, busy=busy))
    assert (19, 30) not in starts
    assert (19, 45) in starts
    # A new party 2 needs 90 + 10 churn = 100 min before 18:00.
    assert (16, 15) not in starts and (17, 0) not in starts


def test_restaurant_capacity_caps_seated_covers():
    rules = _rules(max_concurrent_covers=10)
    tables = [_t("T1", 1, 4), _t("T2", 1, 4), _t("T3", 1, 4)]
    busy = [_busy("r1", "T1", _at(18, 30), 105, 15, 4),
            _busy("r2", "T2", _at(19, 0), 105, 15, 4)]
    assert (19, 0) not in _starts(_slots(rules, tables, 4, busy=busy))
    assert (19, 0) in _starts(_slots(rules, tables, 2, busy=busy))


def test_capacity_ignores_churn_time():
    rules = _rules(max_concurrent_covers=6, interval=15)
    tables = [_t("T1", 1, 4), _t("T2", 1, 4)]
    # Dining 18:00-19:45, churn to 20:00: covers leave the room at 19:45.
    busy = [_busy("r1", "T1", _at(18, 0), 105, 15, 4)]
    starts = _starts(_slots(rules, tables, 4, busy=busy))
    assert (19, 30) not in starts
    assert (19, 45) in starts


def test_capacity_counts_a_combination_booking_once():
    rules = _rules(max_concurrent_covers=12)
    tables = [_t("T1", 4, 6), _t("T2", 4, 6), _t("T3", 1, 4)]
    busy = [_busy("r1", "T1", _at(19, 0), 150, 20, 9),
            _busy("r1", "T2", _at(19, 0), 150, 20, 9)]
    assert (19, 0) in _starts(_slots(rules, tables, 3, busy=busy))
    assert (19, 0) not in _starts(_slots(rules, tables, 4, busy=busy))


def test_pacing_caps_covers_starting_per_slot():
    rules = _rules(max_covers_per_slot=6, interval=15)
    tables = [_t("T1", 1, 4), _t("T2", 1, 4)]
    busy = [_busy("r1", "T1", _at(19, 0), 105, 15, 4)]
    starts = _starts(_slots(rules, tables, 4, busy=busy))
    assert (19, 0) not in starts
    assert (19, 15) in starts


def test_party_of_nine_uses_a_combination_when_capacity_allows():
    tables = [_t("T3", 4, 6), _t("T4", 4, 6), _t("T5", 1, 2)]
    combos = [ComboSpec(id="C1", name="T3+T4", table_ids=("T3", "T4"),
                        min_seats=8, max_seats=12)]
    slots = _slots(_rules(), tables, 9, combos=combos)
    assert slots and all(s.table_ids == ("T3", "T4") for s in slots)
    busy = [_busy("r1", "T5", _at(17, 0), 300, 0, 2)]
    refused = _slots(_rules(max_concurrent_covers=10), tables, 9, combos=combos,
                     busy=busy)
    assert (19, 0) not in _starts(refused)


def test_date_override_replaces_weekly_hours_and_empty_override_closes():
    lunch = ServicePeriod(name="lunch", first_seating=time(12, 0),
                          last_seating=time(14, 0))
    rules = _rules(date_overrides=[DateOverride(date=DAY, periods=[lunch])])
    starts = _starts(_slots(rules, [_t("T1", 1, 4)], 2))
    assert starts == [(12, 0), (12, 30), (13, 0), (13, 30), (14, 0)]
    closed = _rules(date_overrides=[DateOverride(date=DAY, periods=[])])
    assert _slots(closed, [_t("T1", 1, 4)], 2) == []


def test_smallest_fitting_table_is_chosen_then_combination():
    tables = [_t("T3", 4, 6), _t("T2", 2, 4), _t("T1", 2, 2)]
    combos = [ComboSpec(id="C1", name="T1+T2", table_ids=("T1", "T2"),
                        min_seats=4, max_seats=6)]
    rules = _rules(first=time(19, 0), last=time(19, 0))
    assert _slots(rules, tables, 4, combos=combos)[0].table_ids == ("T2",)
    busy = [_busy("r1", "T2", _at(19, 0), 90, 10, 2)]
    assert _slots(rules, tables, 4, combos=combos, busy=busy)[0].table_ids == ("T3",)
    t3_busy = busy + [_busy("r2", "T3", _at(19, 0), 90, 10, 2)]
    # T2 is busy, so the T1+T2 combination cannot be used either.
    assert _slots(rules, tables, 4, combos=combos, busy=t3_busy) == []
    only_t3 = [_busy("r2", "T3", _at(19, 0), 90, 10, 2)]
    t2_t3 = only_t3 + [_busy("r3", "T2", _at(18, 0), 30, 0, 2)]
    assert _slots(rules, tables, 4, combos=combos, busy=t2_t3)[0].table_ids == ("T2",)
    alt = [_t("T3", 4, 6), _t("T2", 2, 3), _t("T1", 2, 2)]
    assert _slots(rules, alt, 4, combos=combos, busy=only_t3)[0].table_ids == ("T1", "T2")


def test_party_above_max_party_size_gets_nothing():
    rules = _rules(max_party_size=10)
    assert _slots(rules, [_t("T1", 1, 20)], 11) == []


def test_party_size_zero_gets_nothing():
    assert _slots(_rules(), [_t("T1", 1, 4)], 0) == []


def test_spring_forward_skips_nonexistent_times():
    d = date(2027, 3, 14)
    rules = _rules(first=time(1, 0), last=time(3, 30), interval=30)
    slots = _slots(rules, [_t("T1", 1, 4)], 2, d=d, now=datetime(2027, 3, 10, tzinfo=NY))
    assert _starts(slots) == [(1, 0), (1, 30), (3, 0), (3, 30)]


def test_fall_back_yields_each_local_time_once():
    d = date(2026, 11, 1)
    rules = _rules(first=time(0, 30), last=time(2, 30), interval=30)
    slots = _slots(rules, [_t("T1", 1, 4)], 2, d=d, now=datetime(2026, 10, 30, tzinfo=NY))
    assert _starts(slots) == [(0, 30), (1, 0), (1, 30), (2, 0), (2, 30)]
    assert len({s.start for s in slots}) == len(slots)


def test_overlapping_periods_dedupe_by_instant():
    rules = RulesSchema(
        service_periods=[
            ServicePeriod(name="a", weekdays=list(range(7)),
                          first_seating=time(17, 0), last_seating=time(18, 0)),
            ServicePeriod(name="b", weekdays=list(range(7)),
                          first_seating=time(17, 30), last_seating=time(18, 30))],
        slot_interval_minutes=30, cutoff_minutes=0)
    assert _starts(_slots(rules, [_t("T1", 1, 4)], 2)) == [
        (17, 0), (17, 30), (18, 0), (18, 30)]


def test_section_cover_limit_caps_concurrent_covers():
    rules = _rules(section_cover_limits={"patio": 6},
                   first=time(19, 0), last=time(19, 0))
    tables = [_t("P1", 2, 4, "patio"), _t("P2", 2, 4, "patio"), _t("M1", 2, 6, "main")]
    busy = [_busy("r1", "P1", _at(19, 0), 105, 15, 4, "patio")]
    slots = _slots(rules, tables, 4, busy=busy)
    assert slots[0].table_ids == ("M1",) and slots[0].section_id == "main"
    no_main = [t for t in tables if t.id != "M1"]
    assert _slots(rules, no_main, 4, busy=busy) == []
    assert _slots(rules, no_main, 2, busy=busy)[0].table_ids == ("P2",)


def test_window_bounds_are_half_open():
    rules = _rules()
    out = compute_slots(rules, TZ, [_t("T1", 1, 4)], [], [], 2,
                        _at(18, 0), _at(19, 0), NOW)
    assert _starts(out) == [(18, 0), (18, 30)]


# --- find_slots (database) -------------------------------------------------

async def _db_setup(db):
    now = datetime.now(timezone.utc)
    d = now.astimezone(NY).date() + timedelta(days=3)
    await save_rules(db, COMPANY_ID, LOCATION_ID, RulesSchema(
        service_periods=[ServicePeriod(
            name="dinner", weekdays=list(range(7)),
            first_seating=time(17, 0), last_seating=time(21, 0))],
        slot_interval_minutes=30,
        durations=[DurationBand(max_party=99, minutes=90, churn_minutes=10)]))
    table = DiningTable(company_id=COMPANY_ID, location_id=LOCATION_ID,
                        label="T1", min_seats=1, max_seats=4)
    db.add(table)
    await db.flush()
    return now, d, table


async def _book(db, table, start: datetime, status: str, held_until=None) -> Reservation:
    res = Reservation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=2,
        start_at=start, end_at=start + timedelta(minutes=90), status=status,
        held_until=held_until, source=SOURCE_STAFF)
    db.add(res)
    await db.flush()
    db.add(ReservationTable(
        company_id=COMPANY_ID, reservation_id=res.id, table_id=table.id,
        during=Range(start, start + timedelta(minutes=100), bounds="[)")))
    await db.flush()
    return res


def _day_window(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0), tzinfo=NY)
    return start, start + timedelta(days=1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_slots_ignores_expired_holds_and_cancelled(db_session, seed_location):
    now, d, table = await _db_setup(db_session)
    await _book(db_session, table, datetime.combine(d, time(17, 0), tzinfo=NY), BOOKED)
    await _book(db_session, table, datetime.combine(d, time(19, 0), tzinfo=NY), HELD,
                held_until=now - timedelta(minutes=1))
    await _book(db_session, table, datetime.combine(d, time(21, 0), tzinfo=NY), CANCELLED)
    ws, we = _day_window(d)
    starts = _starts(await find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, ws, we, now=now))
    assert (17, 0) not in starts and (18, 0) not in starts
    assert (19, 0) in starts and (21, 0) in starts


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_slots_honours_a_live_pending_hold(db_session, seed_location):
    now, d, table = await _db_setup(db_session)
    await _book(db_session, table, datetime.combine(d, time(19, 0), tzinfo=NY), PENDING,
                held_until=now + timedelta(hours=1))
    ws, we = _day_window(d)
    starts = _starts(await find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, ws, we, now=now))
    assert (19, 0) not in starts and (20, 30) not in starts and (21, 0) in starts


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_slots_excludes_given_reservation(db_session, seed_location):
    now, d, table = await _db_setup(db_session)
    res = await _book(db_session, table, datetime.combine(d, time(19, 0), tzinfo=NY), BOOKED)
    ws, we = _day_window(d)
    before = _starts(await find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, ws, we, now=now))
    assert (19, 0) not in before
    after = _starts(await find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, ws, we, now=now,
                                     exclude_reservation_id=res.id))
    assert (19, 0) in after


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_slots_ignores_archived_tables_and_other_tenants(db_session, seed_location):
    now, d, table = await _db_setup(db_session)
    table.archived_at = now
    await db_session.flush()
    ws, we = _day_window(d)
    assert await find_slots(db_session, COMPANY_ID, LOCATION_ID, 2, ws, we, now=now) == []
    assert await find_slots(db_session, "zzzzzzzz", LOCATION_ID, 2, ws, we, now=now) == []
