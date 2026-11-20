"""The demo restaurant seed (#259 step 11): `python -m backend.seed_reservations`
turns the demo tenant's first location into a restaurant with a floor, rules,
a week of fictional bookings and a comped AI host."""

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from backend import seed
from backend import seed_reservations as sr
from backend.config import settings
from backend.main import app
from backend.models import Company, Location, OwnershipGroup, Region
from backend.reservations.agent.core import AgentCore
from backend.reservations.agent.models import AgentConversation
from backend.reservations.availability import find_slots
from backend.reservations.models import (
    DiningTable,
    FloorSection,
    Reservation,
    ReservationRules,
    ReservationTable,
    TableCombination,
)
from backend.reservations.rules import get_rules
from backend.reservations.subscription import ReservationSubscription, busy_text
from backend.routers.public_reservations import get_agent_core
from backend.services import billing_reservations as br
from tests.reservations.agent.fakes import ScriptedLlm

pytestmark = pytest.mark.asyncio

# 18:00 on a Tuesday in New York, in a week with no public holiday: lunch is
# over, the first dinner party is seated, the rest of the week is ahead.
NOW = datetime(2026, 12, 1, 23, 0, tzinfo=timezone.utc)
DEMO_SLUG = "acme-corp"


@pytest_asyncio.fixture
async def demo(db_session) -> Location:
    """The demo tenant as backend/seed.py leaves it, minus what this seed
    does not read."""
    db_session.add(OwnershipGroup(id=seed.OWNERSHIP_GROUP_ID, name="Acme Corp Group"))
    await db_session.flush()
    db_session.add(Company(id=seed.COMPANY_ID, ownership_group_id=seed.OWNERSHIP_GROUP_ID,
                           name="Acme Corp", slug=DEMO_SLUG))
    await db_session.flush()
    db_session.add(Region(id=seed.REGION_ID, company_id=seed.COMPANY_ID, name="East Coast"))
    await db_session.flush()
    loc = Location(id=seed.LOCATION_ID, company_id=seed.COMPANY_ID, region_id=seed.REGION_ID,
                   name="Downtown Store", timezone="America/New_York")
    db_session.add(loc)
    await db_session.commit()
    return loc


async def _ids(db_session) -> dict[str, list[str]]:
    db_session.expire_all()
    out: dict[str, list[str]] = {}
    for model in (FloorSection, DiningTable, TableCombination, ReservationRules,
                  Reservation, ReservationTable):
        out[model.__tablename__] = sorted((await db_session.execute(
            select(model.id).where(model.company_id == seed.COMPANY_ID))).scalars().all())
    out["reservation_subscriptions"] = sorted((await db_session.execute(
        select(ReservationSubscription.id).where(
            ReservationSubscription.ownership_group_id == seed.OWNERSHIP_GROUP_ID)
    )).scalars().all())
    return out


async def _seed(db_session, now: datetime = NOW) -> dict[str, int]:
    counts = await sr.seed_reservations(db_session, now=now)
    await db_session.commit()
    return counts


async def test_running_the_seed_twice_gives_the_same_rows(db_session, demo):
    first = await _seed(db_session)
    ids = await _ids(db_session)
    second = await _seed(db_session)
    assert second == first
    assert await _ids(db_session) == ids
    assert first["tables"] == 14 and first["combinations"] == 1
    assert 35 <= first["reservations"] <= 45
    assert len(ids["dining_tables"]) == 14
    assert len(ids["reservations"]) == first["reservations"]
    assert all(i.startswith("rsv") and len(i) == 8 for k, v in ids.items()
               if k != "reservation_tables" for i in v)


async def test_a_later_rerun_does_not_fail_on_bookings_now_past(db_session, demo):
    await _seed(db_session)
    before = set((await _ids(db_session))["reservations"])
    await _seed(db_session, NOW + timedelta(days=2, hours=5))
    after = set((await _ids(db_session))["reservations"])
    assert before <= after and len(after) > len(before)


async def test_floor_has_two_tops_to_one_eight_top_and_a_combinable_pair(db_session, demo):
    await _seed(db_session)
    tables = (await db_session.execute(select(DiningTable).where(
        DiningTable.location_id == seed.LOCATION_ID))).scalars().all()
    assert min(t.max_seats for t in tables) == 2
    assert [t.max_seats for t in tables].count(8) == 1
    combo = (await db_session.execute(select(TableCombination).where(
        TableCombination.location_id == seed.LOCATION_ID))).scalar_one()
    members = [t for t in tables if t.id in combo.table_ids]
    assert len(members) == 2 and all(t.combinable for t in members)


async def test_rules_for_the_demo(db_session, demo):
    await _seed(db_session)
    rules, _ = await get_rules(db_session, seed.COMPANY_ID, seed.LOCATION_ID)
    assert {p.name for p in rules.service_periods} == {"Lunch", "Dinner"}
    assert rules.max_covers_per_slot is not None
    assert {b.churn_minutes for b in rules.durations} == {15}
    assert rules.daily_spend_cap_usd == 3
    assert rules.provider == "wizscheduler"
    # Christmas Day is the next public holiday after Dec 1: closed all day.
    closed = [o for o in rules.date_overrides if o.date == date(2026, 12, 25)]
    assert len(closed) == 1 and closed[0].periods == []


async def test_the_closure_is_the_next_public_holiday(db_session, demo):
    await _seed(db_session, datetime(2026, 11, 19, 15, tzinfo=timezone.utc))
    rules, _ = await get_rules(db_session, seed.COMPANY_ID, seed.LOCATION_ID)
    assert [o.date for o in rules.date_overrides] == [date(2026, 11, 26)]  # Thanksgiving
    # No booking is seeded on a day the restaurant is closed.
    zone = ZoneInfo("America/New_York")
    starts = (await db_session.execute(select(Reservation.start_at).where(
        Reservation.location_id == seed.LOCATION_ID))).scalars().all()
    assert starts and all(s.astimezone(zone).date() != date(2026, 11, 26) for s in starts)


async def test_bookings_cover_seven_local_days_in_mixed_statuses(db_session, demo):
    await _seed(db_session)
    zone = ZoneInfo(demo.timezone)
    today = NOW.astimezone(zone).date()
    rows = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seed.LOCATION_ID))).scalars().all()
    days = {r.start_at.astimezone(zone).date() for r in rows}
    assert days == {today + timedelta(days=i) for i in range(7)}
    statuses = {r.status for r in rows}
    assert {"booked", "completed", "cancelled", "pending_approval"} <= statuses
    # Pending requests stay pending until their time, not a day after the seed.
    for r in rows:
        if r.status == "pending_approval":
            assert r.held_until == r.start_at and r.confirmation_code


async def test_every_diner_is_fictional(db_session, demo):
    await _seed(db_session)
    rows = (await db_session.execute(select(Reservation).where(
        Reservation.location_id == seed.LOCATION_ID))).scalars().all()
    assert rows
    for r in rows:
        assert re.fullmatch(r"\+1\d{3}55501\d\d", r.diner_phone), r.diner_phone
        assert r.diner_email.endswith("@example.com"), r.diner_email


async def test_find_slots_has_dinner_tomorrow(db_session, demo):
    await _seed(db_session)
    zone = ZoneInfo(demo.timezone)
    tomorrow = NOW.astimezone(zone).date() + timedelta(days=1)
    slots = await find_slots(
        db_session, seed.COMPANY_ID, seed.LOCATION_ID, 2,
        datetime.combine(tomorrow, time(17), tzinfo=zone),
        datetime.combine(tomorrow, time(22), tzinfo=zone), now=NOW)
    assert slots


async def test_location_gets_a_restaurant_name_unless_it_has_one(db_session, demo):
    await _seed(db_session)
    db_session.expire_all()
    assert (await db_session.get(Location, seed.LOCATION_ID)).name == sr.RESTAURANT_NAME
    (await db_session.get(Location, seed.LOCATION_ID)).name = "Chez Visitor"
    await db_session.commit()
    await _seed(db_session)
    db_session.expire_all()
    assert (await db_session.get(Location, seed.LOCATION_ID)).name == "Chez Visitor"


async def test_the_demo_group_is_comped_without_stripe_ids(db_session, demo):
    await _seed(db_session)
    sub = (await db_session.execute(select(ReservationSubscription).where(
        ReservationSubscription.ownership_group_id == seed.OWNERSHIP_GROUP_ID))).scalar_one()
    assert sub.status == "comped" and sub.stripe_subscription_id is None


async def test_a_live_stripe_subscription_is_never_overwritten(db_session, demo):
    db_session.add(ReservationSubscription(
        ownership_group_id=seed.OWNERSHIP_GROUP_ID, stripe_subscription_id="sub_real",
        status="active"))
    await db_session.commit()
    await _seed(db_session)
    db_session.expire_all()
    sub = (await db_session.execute(select(ReservationSubscription).where(
        ReservationSubscription.ownership_group_id == seed.OWNERSHIP_GROUP_ID))).scalar_one()
    assert (sub.status, sub.stripe_subscription_id) == ("active", "sub_real")


async def test_refuses_in_production_without_the_demo_password(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "DEMO_SEED_PASSWORD", "")

    def no_db():
        pytest.fail("the seed opened a database session")
    monkeypatch.setattr(sr, "async_session_factory", no_db)
    with pytest.raises(SystemExit, match="DEMO_SEED_PASSWORD"):
        await sr.main()


async def test_at_the_three_dollar_cap_a_web_turn_gets_the_busy_text(
        client, db_session, demo, monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)
    llm = ScriptedLlm()
    app.dependency_overrides[get_agent_core] = lambda: AgentCore(llm)
    await sr.seed_reservations(db_session)  # real clock: the cap counts today's spend
    await db_session.commit()
    base = f"/api/v1/public/reservations/{DEMO_SLUG}/{seed.LOCATION_ID}"
    r = await client.post(f"{base}/conversations")
    assert r.status_code == 201 and r.json()["ended"] is False
    token = r.json()["conversation_token"]
    db_session.add(AgentConversation(
        company_id=seed.COMPANY_ID, location_id=seed.LOCATION_ID, channel="web",
        status="closed", outcome="booked", started_at=datetime.now(timezone.utc),
        cost_usd=Decimal("3.00")))
    await db_session.commit()
    r = await client.post(f"{base}/conversations/{token}/turns", json={"text": "hi"})
    assert r.status_code == 200
    expected = await busy_text(db_session, seed.COMPANY_ID, seed.LOCATION_ID)
    assert r.json()["reply_text"] == expected and r.json()["ended"] is True
    assert llm.requests == []


async def test_group_deletion_removes_the_seeded_rows(db_session, demo, monkeypatch):
    from backend.services.billing import delete_og_and_dependents

    def no_stripe():
        pytest.fail("Stripe was called while deleting a comped group")
    monkeypatch.setattr(br, "_client", no_stripe)
    await _seed(db_session)
    og = await db_session.get(OwnershipGroup, seed.OWNERSHIP_GROUP_ID)
    await delete_og_and_dependents(db_session, og)
    await db_session.commit()
    for model in (FloorSection, DiningTable, TableCombination, ReservationRules,
                  Reservation, ReservationTable):
        n = (await db_session.execute(select(func.count()).select_from(model).where(
            model.company_id == seed.COMPANY_ID))).scalar_one()
        assert n == 0, model.__tablename__
    assert (await db_session.execute(select(func.count()).select_from(
        ReservationSubscription))).scalar_one() == 0


async def test_the_seed_survives_an_archived_table_and_a_visitor_table_with_a_booking(
        db_session, demo):
    from backend.reservations import booking
    from backend.services.archive import archive_dining_table

    await _seed(db_session)
    # A visitor archives seeded T3 and adds a table of their own with a booking.
    t3 = await db_session.get(DiningTable, "rsvtbl03")
    archive_dining_table(t3, now=NOW)
    visitor = DiningTable(company_id=seed.COMPANY_ID, location_id=seed.LOCATION_ID,
                          section_id="rsvsec01", label="V1", min_seats=9, max_seats=10)
    db_session.add(visitor)
    await db_session.commit()
    rules, _ = await get_rules(db_session, seed.COMPANY_ID, seed.LOCATION_ID)
    zone = ZoneInfo(demo.timezone)
    day = NOW.astimezone(zone).date() + timedelta(days=3)
    await db_session.execute(
        update(ReservationRules)
        .where(ReservationRules.location_id == seed.LOCATION_ID)
        .values(rules={**rules.model_dump(mode="json"), "max_party_size": 10}))
    await db_session.commit()
    await booking.book_staff(db_session, seed.COMPANY_ID, seed.LOCATION_ID, 10,
                             datetime.combine(day, time(19), tzinfo=zone),
                             booking.DinerContact(name="Visitor Party"), now=NOW)
    await db_session.commit()

    labels: list[list[str]] = []
    for _ in range(3):
        await _seed(db_session)
        db_session.expire_all()
        active = (await db_session.execute(select(DiningTable).where(
            DiningTable.location_id == seed.LOCATION_ID,
            DiningTable.archived_at.is_(None)))).scalars().all()
        labels.append(sorted(t.label for t in active))
        assert visitor.id in {t.id for t in active}
    # T3 comes back once, as one active table, and the runs agree.
    assert labels[0] == labels[1] == labels[2]
    assert labels[0].count("T3") == 1
    total_t3 = (await db_session.execute(select(func.count()).select_from(DiningTable).where(
        DiningTable.location_id == seed.LOCATION_ID, DiningTable.label == "T3"))).scalar_one()
    assert total_t3 == 2  # the archived seeded row and its one replacement
