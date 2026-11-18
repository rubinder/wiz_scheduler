from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from backend.reservations.floor import (
    FloorInvalid,
    FloorIn,
    TableHasBookings,
    get_floor,
    save_floor,
)
from backend.reservations.models import (
    BOOKED,
    SOURCE_STAFF,
    DiningTable,
    Reservation,
    ReservationTable,
)
from backend.models import Company, Location, Region
from tests.conftest import COMPANY_ID, LOCATION_ID

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _floor(tables=None, combos=None) -> FloorIn:
    t = lambda label, lo, hi, comb, sec="a": dict(  # noqa: E731
        id=None, label=label, section_key=sec, min_seats=lo, max_seats=hi,
        combinable=comb, x=1, y=2, w=3, h=4, rotation=0)
    return FloorIn.model_validate(dict(
        sections=[dict(id=None, key="a", name="Main", sort_order=0),
                  dict(id=None, key="b", name="Patio", sort_order=1)],
        tables=tables if tables is not None else [
            t("T1", 2, 2, True), t("T2", 2, 4, True), t("T3", 4, 6, False, "b")],
        combinations=combos if combos is not None else [
            dict(id=None, name="T1+T2", table_labels=["T1", "T2"])],
    ))


async def test_save_floor_creates_sections_tables_and_combination(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    assert [s.name for s in out.sections] == ["Main", "Patio"]
    assert len(out.tables) == 3
    t3 = next(t for t in out.tables if t.label == "T3")
    assert t3.section_key == next(s.key for s in out.sections if s.name == "Patio")
    combo = out.combinations[0]
    # min = largest member minimum (decision 14), max = sum of maxima.
    assert (combo.min_seats, combo.max_seats) == (2, 6)
    assert sorted(combo.table_labels) == ["T1", "T2"]
    again = await get_floor(db_session, COMPANY_ID, LOCATION_ID)
    assert again.model_dump() == out.model_dump()


async def test_omitted_table_is_archived_not_deleted(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    keep = [t.model_dump() for t in out.tables if t.label != "T3"]
    await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(dict(
        sections=[s.model_dump() for s in out.sections], tables=keep,
        combinations=[c.model_dump() for c in out.combinations])))
    rows = (await db_session.execute(
        select(DiningTable).where(DiningTable.label == "T3"))).scalars().all()
    assert len(rows) == 1 and rows[0].archived_at is not None
    assert len((await get_floor(db_session, COMPANY_ID, LOCATION_ID)).tables) == 2


async def test_archiving_a_table_with_future_booking_raises(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    t3 = next(t for t in out.tables if t.label == "T3")
    now = datetime.now(timezone.utc)
    res = Reservation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=4,
        start_at=now + timedelta(hours=20), end_at=now + timedelta(hours=22),
        status=BOOKED, source=SOURCE_STAFF)
    db_session.add(res)
    await db_session.flush()
    db_session.add(ReservationTable(
        company_id=COMPANY_ID, reservation_id=res.id, table_id=t3.id,
        during=Range(now + timedelta(hours=20), now + timedelta(hours=23), bounds="[)")))
    await db_session.flush()
    keep = [t.model_dump() for t in out.tables if t.label != "T3"]
    with pytest.raises(TableHasBookings) as ei:
        await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(dict(
            sections=[s.model_dump() for s in out.sections], tables=keep,
            combinations=[c.model_dump() for c in out.combinations])), now=now)
    assert ei.value.table_ids == [t3.id]


async def test_duplicate_label_and_bad_combination_raise_floor_invalid(db_session, seed_location):
    base = _floor().model_dump()
    dup = FloorIn.model_validate({**base, "tables": base["tables"] + [dict(base["tables"][0])]})
    with pytest.raises(FloorInvalid):
        await save_floor(db_session, COMPANY_ID, LOCATION_ID, dup)
    bad = _floor(tables=[
        dict(id=None, label="T1", section_key=None, min_seats=5, max_seats=2,
             combinable=True, x=0, y=0, w=1, h=1, rotation=0)], combos=[])
    with pytest.raises(FloorInvalid):
        await save_floor(db_session, COMPANY_ID, LOCATION_ID, bad)
    for names in (["T1", "T3"], ["T1", "ZZ"], ["T1"]):
        with pytest.raises(FloorInvalid):
            await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor(
                combos=[dict(id=None, name="x", table_labels=names)]))


async def test_foreign_ids_raise_unknown_id_and_change_nothing(db_session, seed_location):
    from backend.reservations.models import FloorSection, TableCombination

    db_session.add(Company(id="othrcomp", name="Other", slug="other1"))
    await db_session.flush()
    db_session.add(Region(id="othrregn", company_id="othrcomp", name="R"))
    await db_session.flush()
    db_session.add(Location(id="othrloc1", company_id="othrcomp",
                            region_id="othrregn", name="L", timezone="UTC"))
    await db_session.flush()
    sec = FloorSection(company_id="othrcomp", location_id="othrloc1", name="Theirs")
    tbl = DiningTable(company_id="othrcomp", location_id="othrloc1", label="X1",
                      min_seats=1, max_seats=2, combinable=True)
    tbl2 = DiningTable(company_id="othrcomp", location_id="othrloc1", label="X2",
                       min_seats=1, max_seats=2, combinable=True)
    db_session.add_all([sec, tbl, tbl2])
    await db_session.flush()
    combo = TableCombination(company_id="othrcomp", location_id="othrloc1",
                             name="c", table_ids=[tbl.id, tbl2.id],
                             min_seats=2, max_seats=4)
    db_session.add(combo)
    await db_session.flush()

    payloads = []
    p = _floor().model_dump(); p["sections"][0]["id"] = sec.id; payloads.append(p)
    p = _floor().model_dump(); p["tables"][0]["id"] = tbl.id; payloads.append(p)
    p = _floor().model_dump(); p["combinations"][0]["id"] = combo.id; payloads.append(p)
    for p in payloads:
        with pytest.raises(FloorInvalid) as ei:
            await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(p))
        assert ei.value.code == "unknown_id"
    floor = await get_floor(db_session, COMPANY_ID, LOCATION_ID)
    assert floor.tables == [] and floor.sections == [] and floor.combinations == []
    await db_session.refresh(tbl)
    assert tbl.label == "X1" and tbl.archived_at is None


async def test_duplicate_ids_in_payload_raise_duplicate_id(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    for field in ("sections", "tables", "combinations"):
        p = out.model_dump()
        p[field] = p[field] + [dict(p[field][0])]
        if field == "tables":
            p[field][-1]["label"] = "UNIQUE"
        with pytest.raises(FloorInvalid) as ei:
            await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(p))
        assert ei.value.code == "duplicate_id"


async def test_update_delete_section_and_reuse_archived_label(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    main = next(s for s in out.sections if s.name == "Main")
    patio = next(s for s in out.sections if s.name == "Patio")
    t1, t2, t3 = (next(t for t in out.tables if t.label == n) for n in ("T1", "T2", "T3"))
    assert t3.section_key == patio.key
    payload = dict(
        sections=[dict(main.model_dump(), name="Dining Room", sort_order=5)],  # Patio omitted
        tables=[
            dict(t1.model_dump(), label="T1x", w=9.0, section_key=main.key),
            dict(t2.model_dump(), section_key=main.key),
            # T3 omitted -> archived; new table reuses its label in the same save
            dict(id=None, label="T3", section_key=None, min_seats=2, max_seats=2,
                 combinable=False, x=0, y=0, w=1, h=1, rotation=0),
        ],
        combinations=[],
    )
    res = await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(payload))
    assert [(s.id, s.name, s.sort_order) for s in res.sections] == [(main.id, "Dining Room", 5)]
    by_label = {t.label: t for t in res.tables}
    assert set(by_label) == {"T1x", "T2", "T3"}
    assert by_label["T1x"].id == t1.id and by_label["T1x"].w == 9.0
    assert by_label["T3"].id != t3.id and by_label["T3"].section_key is None
    old_t3 = await db_session.get(DiningTable, t3.id)
    await db_session.refresh(old_t3)
    assert old_t3.archived_at is not None and old_t3.section_id is None
    assert res.combinations == []


async def test_swapping_labels_raises_floor_invalid(db_session, seed_location):
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor(combos=[]))
    t1, t2 = (next(t for t in out.tables if t.label == n) for n in ("T1", "T2"))
    p = out.model_dump()
    for t in p["tables"]:
        if t["id"] == t1.id:
            t["label"] = "T2"
        elif t["id"] == t2.id:
            t["label"] = "T1"
    with pytest.raises(FloorInvalid) as ei:
        await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(p))
    assert ei.value.code == "duplicate_label"


async def test_lapsed_hold_does_not_block_archiving_its_table(db_session, seed_location):
    from backend.reservations.models import EXPIRED, HELD, SOURCE_WEB_AGENT
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor())
    t3 = next(t for t in out.tables if t.label == "T3")
    now = datetime.now(timezone.utc)
    res = Reservation(
        company_id=COMPANY_ID, location_id=LOCATION_ID, party_size=4,
        start_at=now + timedelta(hours=20), end_at=now + timedelta(hours=22),
        status=HELD, held_until=now - timedelta(minutes=1), source=SOURCE_WEB_AGENT)
    db_session.add(res)
    await db_session.flush()
    db_session.add(ReservationTable(
        company_id=COMPANY_ID, reservation_id=res.id, table_id=t3.id,
        during=Range(now + timedelta(hours=20), now + timedelta(hours=23), bounds="[)")))
    await db_session.flush()
    keep = [t.model_dump() for t in out.tables if t.label != "T3"]
    await save_floor(db_session, COMPANY_ID, LOCATION_ID, FloorIn.model_validate(dict(
        sections=[s.model_dump() for s in out.sections], tables=keep,
        combinations=[c.model_dump() for c in out.combinations])), now=now)
    await db_session.refresh(res)
    assert res.status == EXPIRED


async def test_combination_minimum_is_its_largest_member_minimum(db_session, seed_location):
    from datetime import time
    from backend.reservations.availability import find_slots
    from backend.reservations.rules import RulesSchema, ServicePeriod, save_rules
    t = lambda label: dict(  # noqa: E731
        id=None, label=label, section_key="a", min_seats=2, max_seats=2, combinable=True)
    out = await save_floor(db_session, COMPANY_ID, LOCATION_ID, _floor(
        tables=[t("A1"), t("A2")],
        combos=[dict(id=None, name="A1+A2", table_labels=["A1", "A2"])]))
    combo = out.combinations[0]
    assert (combo.min_seats, combo.max_seats) == (2, 4)
    await save_rules(db_session, COMPANY_ID, LOCATION_ID, RulesSchema(
        service_periods=[ServicePeriod(name="d", weekdays=list(range(7)),
                                       first_seating=time(19), last_seating=time(19))]))
    day = datetime.now(timezone.utc) + timedelta(days=7)
    slots = await find_slots(db_session, COMPANY_ID, LOCATION_ID, 3,
                             day - timedelta(days=1), day + timedelta(days=1))
    assert slots and len(slots[0].table_ids) == 2
