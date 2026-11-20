"""Demo restaurant seed for reservations and the AI host (#259 step 11).

Usage (from the repo root, after `python -m backend.seed`):
    python -m backend.seed_reservations

Turns the demo tenant's first location (seed.LOCATION_ID) into a restaurant:
a floor plan, booking rules with lunch and dinner, a closure on the next
public holiday, a week of fictional bookings, and a comped AI host
subscription that Stripe never sees. It writes only the demo company's rows.

Idempotent. Every row it owns has a deterministic `rsv...` id, so a rerun
finds and keeps what an earlier run made. Floor, rules and bookings go
through the reservations services (floor.save_floor, rules.save_rules,
booking.*), so the seeded data passes the same validation as real data and
`find_slots` stays the only evaluator of what is free. Bookings are keyed on
the location's local date, so a rerun on a later day adds the new days and
leaves the earlier bookings alone, even those now in the past. A booking
whose slot has meanwhile been taken is skipped, not fatal. Older seeded
bookings keep the status they were seeded with after their time passes.

Diners are invented: phones are in the 555-0100..0199 fiction range and
emails are @example.com.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend import seed
from backend.config import settings
from backend.database import async_session_factory, engine
from backend.models import Company, Location
from backend.reservations import booking, demo_data
from backend.reservations.demo_data import (  # noqa: F401  (re-exported)
    BOOKING_PLAN, DINERS, DINNER, LUNCH, NOTES, reservation_id,
)
from backend.reservations.floor import (
    CombinationIO,
    FloorIn,
    FloorInvalid,
    SectionIO,
    TableHasBookings,
    TableIO,
    get_floor,
    save_floor,
)
from backend.reservations.models import (
    COMPLETED,
    NO_SHOW,
    SEATED,
    SOURCE_STAFF,
    SOURCE_WEB_AGENT,
    DiningTable,
    FloorSection,
    Reservation,
    ReservationRules,
    TableCombination,
)
from backend.reservations.rules import (
    DateOverride,
    DurationBand,
    Persona,
    RulesSchema,
    ServicePeriod,
    get_rules,
    save_rules,
)
from backend.reservations.subscription_models import COMPED, ReservationSubscription

COMPANY_ID = seed.COMPANY_ID
LOCATION_ID = seed.LOCATION_ID

# A made-up name: no restaurant of ours, or anyone's we know of.
RESTAURANT_NAME = "Fernhollow Kitchen"
# The name backend/seed.py gives the location. Only that placeholder is
# replaced; a name somebody chose is kept.
SEED_PLACEHOLDER_NAME = "Downtown Store"

SUBSCRIPTION_ID = "rsvsub01"
RULES_ID = "rsvrul01"
BOOKING_DAYS = 7

# (id, key, name)
SECTIONS = [("rsvsec01", "dining", "Dining Room"), ("rsvsec02", "patio", "Patio")]
# (id, label, section key, min seats, max seats, combinable, x, y)
TABLES = [
    ("rsvtbl01", "T1", "dining", 1, 2, False, 0.0, 0.0),
    ("rsvtbl02", "T2", "dining", 1, 2, False, 1.5, 0.0),
    ("rsvtbl03", "T3", "dining", 1, 2, False, 3.0, 0.0),
    ("rsvtbl04", "T4", "dining", 1, 2, False, 4.5, 0.0),
    ("rsvtbl05", "T5", "dining", 2, 4, False, 0.0, 2.0),
    ("rsvtbl06", "T6", "dining", 2, 4, False, 1.5, 2.0),
    ("rsvtbl07", "T7", "dining", 2, 4, False, 3.0, 2.0),
    ("rsvtbl08", "T8", "dining", 2, 4, True, 4.5, 2.0),
    ("rsvtbl09", "T9", "dining", 2, 4, True, 6.0, 2.0),
    ("rsvtbl10", "T10", "dining", 4, 6, False, 0.0, 4.0),
    ("rsvtbl11", "T11", "dining", 4, 6, False, 2.0, 4.0),
    ("rsvtbl12", "T12", "dining", 6, 8, False, 4.5, 4.0),
    ("rsvtbl13", "P1", "patio", 1, 2, False, 0.0, 6.5),
    ("rsvtbl14", "P2", "patio", 2, 4, False, 1.5, 6.5),
]
# The combinable pair: two 4-tops pushed together seat up to 8.
COMBINATION = ("rsvcmb01", "T8+T9", ["T8", "T9"])

MAX_SELF_BOOK_PARTY = 6

# BOOKING_PLAN, DINERS and NOTES live in backend/reservations/demo_data.py,
# which the demo purge reads too (decision 94).


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (0 = Monday) of the month; n = -1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _us_holidays(year: int) -> list[tuple[date, str]]:
    return [
        (date(year, 1, 1), "New Year's Day"),
        (_nth_weekday(year, 5, 0, -1), "Memorial Day"),
        (date(year, 7, 4), "Independence Day"),
        (_nth_weekday(year, 9, 0, 1), "Labor Day"),
        (_nth_weekday(year, 11, 3, 4), "Thanksgiving"),
        (date(year, 12, 25), "Christmas Day"),
    ]


def next_public_holiday(today: date) -> tuple[date, str]:
    """The first US public holiday on or after `today` (the demo is in New York)."""
    return min(h for y in (today.year, today.year + 1) for h in _us_holidays(y)
               if h[0] >= today)


def demo_rules(today: date) -> RulesSchema:
    holiday, name = next_public_holiday(today)
    return RulesSchema(
        service_periods=[
            # Closed for lunch on Mondays.
            ServicePeriod(name=LUNCH, weekdays=[1, 2, 3, 4, 5, 6],
                          first_seating=time(11, 30), last_seating=time(14, 0)),
            ServicePeriod(name=DINNER, weekdays=list(range(7)),
                          first_seating=time(17, 0), last_seating=time(21, 30)),
        ],
        slot_interval_minutes=15,
        durations=[
            DurationBand(max_party=2, minutes=75, churn_minutes=15),
            DurationBand(max_party=4, minutes=90, churn_minutes=15),
            DurationBand(max_party=6, minutes=105, churn_minutes=15),
            DurationBand(max_party=99, minutes=120, churn_minutes=15),
        ],
        # Pacing: at most 16 covers sit down in any 15-minute slot.
        max_covers_per_slot=16,
        max_concurrent_covers=56,
        date_overrides=[DateOverride(date=holiday, periods=[], note=f"Closed for {name}")],
        max_self_book_party=MAX_SELF_BOOK_PARTY,
        max_party_size=8,
        booking_horizon_days=60,
        cutoff_minutes=60,
        persona=Persona(
            greeting=f"Thanks for reaching {RESTAURANT_NAME}! I'm the automated "
                     "reservations assistant: I can book, change or cancel a table "
                     "for you.",
            facts=[
                f"{RESTAURANT_NAME} is a fictional neighborhood restaurant serving "
                "seasonal New American dishes.",
                "Parties of 7 or 8 are requests the team confirms; for 9 or more, "
                "please call the restaurant.",
                "The patio opens from May to September, weather permitting.",
                "The entrance and restrooms are step-free; high chairs are available.",
                "Street parking, and a public garage one block away.",
            ],
        ),
        daily_spend_cap_usd=3,
        provider="wizscheduler",
    )


async def _location(db: AsyncSession) -> Location:
    loc = (await db.execute(select(Location).where(
        Location.id == LOCATION_ID, Location.company_id == COMPANY_ID,
        Location.archived_at.is_(None)))).scalar_one_or_none()
    if loc is None:
        raise RuntimeError(f"demo location {LOCATION_ID} is missing or archived: "
                           "run `python -m backend.seed` first")
    return loc


async def _seed_floor(db: AsyncSession, now: datetime) -> None:
    # save_floor accepts only ids that already exist (it never trusts a
    # payload id), so the rows are first inserted under their fixed ids,
    # then saved through it: validated, combination seats derived, and any
    # table a visitor added since archived.
    for sec_id, _, name in SECTIONS:
        await db.execute(pg_insert(FloorSection).values(
            id=sec_id, company_id=COMPANY_ID, location_id=LOCATION_ID, name=name,
        ).on_conflict_do_nothing())
    for tbl_id, label, _, lo, hi, comb, x, y in TABLES:
        await db.execute(pg_insert(DiningTable).values(
            id=tbl_id, company_id=COMPANY_ID, location_id=LOCATION_ID, label=label,
            min_seats=lo, max_seats=hi, combinable=comb, x=x, y=y,
        ).on_conflict_do_nothing())
    by_label = {t[1]: t[0] for t in TABLES}
    combo_id, combo_name, combo_labels = COMBINATION
    await db.execute(pg_insert(TableCombination).values(
        id=combo_id, company_id=COMPANY_ID, location_id=LOCATION_ID, name=combo_name,
        table_ids=[by_label[lbl] for lbl in combo_labels], min_seats=1, max_seats=1,
    ).on_conflict_do_nothing())
    # A seeded table a visitor archived cannot come back under its id (only
    # services/archive.py touches archived_at, and it has no table restore).
    # The active table with its label stands in for it, if there is one (the
    # replacement an earlier run made, or the visitor's); otherwise it is
    # saved as a new table, once.
    archived = set((await db.execute(select(DiningTable.id).where(
        DiningTable.company_id == COMPANY_ID, DiningTable.location_id == LOCATION_ID,
        DiningTable.id.in_(list(by_label.values())),
        DiningTable.archived_at.is_not(None)))).scalars().all())
    floor = await get_floor(db, COMPANY_ID, LOCATION_ID)
    active_by_label = {t.label: t.id for t in floor.tables}
    seeded_ids = {
        tid: active_by_label.get(label) if tid in archived else tid
        for tid, label, *_ in TABLES
    }
    section_id_by_key = {key: sid for sid, key, _ in SECTIONS}
    ours_sections = set(section_id_by_key.values())
    ours_labels = {t[1] for t in TABLES}
    # What a visitor added is kept as it is, so the save never archives a
    # table (save_floor archives every table left out, and refuses one with
    # an upcoming booking).
    visitor_sections = [s for s in floor.sections if s.id not in ours_sections]
    visitor_tables = [t for t in floor.tables if t.label not in ours_labels]
    visitor_combos = [c for c in floor.combinations if c.id != combo_id]
    payload = FloorIn(
        sections=[SectionIO(id=sid, key=sid, name=name, sort_order=i)
                  for i, (sid, _, name) in enumerate(SECTIONS)] + visitor_sections,
        tables=[TableIO(id=seeded_ids[tid], label=label,
                        section_key=section_id_by_key[key],
                        min_seats=lo, max_seats=hi, combinable=comb, x=x, y=y)
                for tid, label, key, lo, hi, comb, x, y in TABLES] + visitor_tables,
        combinations=[CombinationIO(id=combo_id, name=combo_name, table_labels=combo_labels)]
        + [CombinationIO(id=c.id, name=c.name, table_labels=c.table_labels)
           for c in visitor_combos],
    )
    try:
        async with db.begin_nested():
            await save_floor(db, COMPANY_ID, LOCATION_ID, payload, now=now)
    except (FloorInvalid, TableHasBookings) as e:
        # Never fatal for the demo: the floor stays as the visitor left it.
        print(f"  floor left as it is: {e}")


async def _seed_rules(db: AsyncSession, today: date, now: datetime) -> None:
    desired = demo_rules(today)
    payload = desired.model_dump(mode="json")
    await db.execute(pg_insert(ReservationRules).values(
        id=RULES_ID, company_id=COMPANY_ID, location_id=LOCATION_ID, rules=payload,
        version=1, updated_at=now,
    ).on_conflict_do_nothing())
    current, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    if current.model_dump(mode="json") != payload:
        await save_rules(db, COMPANY_ID, LOCATION_ID, desired)


def _diner(day: date, index: int) -> booking.DinerContact:
    name, phone, email = demo_data.diner(day, index)
    return booking.DinerContact(name=name, phone=phone, email=email)


def _plan(rules: RulesSchema, today: date) -> list[tuple[date, int, datetime, int]]:
    """(local day, plan index, local start, party) for the bookings of the
    next BOOKING_DAYS local days, on the periods each day is open."""
    out = []
    for offset in range(BOOKING_DAYS):
        day = today + timedelta(days=offset)
        open_periods = {p.name for p in rules.periods_for(day)}
        for index, (period, hour, minute, party) in enumerate(BOOKING_PLAN):
            if (index + day.weekday()) % 3 == 0 or period not in open_periods:
                continue
            out.append((day, index, datetime.combine(day, time(hour, minute)), party))
    return out


async def _book(db: AsyncSession, res_id: str, day: date, index: int, start: datetime,
                party: int, rules: RulesSchema, now: datetime) -> None:
    """One booking, through booking.py. Its status follows the clock: done
    (completed, or now and then a no-show) once it has ended, seated while it
    is on, and otherwise booked, a request awaiting approval (a party above
    the self-book limit), or now and then cancelled."""
    diner = _diner(day, index)
    notes = demo_data.notes(day, index)
    end = start + timedelta(minutes=rules.duration_for(party))
    key = day.toordinal() + index
    if (party > rules.max_self_book_party
            and start >= now + timedelta(minutes=rules.cutoff_minutes)):
        res = await booking.hold(db, COMPANY_ID, LOCATION_ID, party, start,
                                 source=SOURCE_WEB_AGENT, diner=diner, now=now,
                                 reservation_id=res_id)
        res.notes = notes
        await booking.confirm(db, COMPANY_ID, res.id, now=now)
        # Direct write: confirm holds a request for pending_hold_hours (a day),
        # after which the hold sweep expires it. A demo request should still be
        # waiting for the manager when a visitor looks, so it is held until its
        # own time, the latest confirm ever allows.
        res.held_until = res.start_at
        # Direct write: staff entered, not an AI host booking (no conversation
        # is behind it, and analytics counts web_agent rows as the host's).
        # It is held as web_agent above only because hold never makes a staff row
        # a request.
        res.source = SOURCE_STAFF
        await db.flush()
        return
    # A past booking is made on a clock an hour before its start: find_slots
    # never offers a time already gone, whoever books it.
    clock = min(now, start - timedelta(hours=1))
    res = await booking.book_staff(db, COMPANY_ID, LOCATION_ID, party, start, diner,
                                   notes=notes, now=clock, reservation_id=res_id)
    if end <= now:
        if key % 5 == 0:
            await booking.set_status(db, COMPANY_ID, res.id, NO_SHOW, now=clock)
        else:
            await booking.set_status(db, COMPANY_ID, res.id, SEATED, now=clock)
            await booking.set_status(db, COMPANY_ID, res.id, COMPLETED, now=clock)
    elif start <= now:
        await booking.set_status(db, COMPANY_ID, res.id, SEATED, now=clock)
    elif key % 5 == 2:
        await booking.cancel(db, COMPANY_ID, res.id, now=clock)


async def _seed_bookings(db: AsyncSession, rules: RulesSchema, zone: ZoneInfo,
                         today: date, now: datetime) -> int:
    plan = _plan(rules, today)
    ids = [reservation_id(day, index) for day, index, _, _ in plan]
    existing = set((await db.execute(select(Reservation.id).where(
        Reservation.company_id == COMPANY_ID, Reservation.id.in_(ids)))).scalars().all())
    # Rows an earlier seed made as web_agent requests move to the staff
    # source (see _book). Only the seed's own rsv ids, never a visitor's.
    await db.execute(update(Reservation).where(
        Reservation.company_id == COMPANY_ID, Reservation.location_id == LOCATION_ID,
        Reservation.id.like("rsv%"), Reservation.source == SOURCE_WEB_AGENT,
        Reservation.conversation_id.is_(None)).values(source=SOURCE_STAFF))
    for res_id, (day, index, wall, party) in zip(ids, plan):
        if res_id in existing:
            # Restore the seed's diner details on a row the demo purge
            # scrubbed, or a visitor edited (decision 94).
            name, phone, email = demo_data.diner(day, index)
            await db.execute(update(Reservation).where(
                Reservation.company_id == COMPANY_ID, Reservation.id == res_id,
                or_(Reservation.diner_name.is_distinct_from(name),
                    Reservation.diner_phone.is_distinct_from(phone),
                    Reservation.diner_email.is_distinct_from(email),
                    Reservation.notes.is_distinct_from(demo_data.notes(day, index))),
            ).values(diner_name=name, diner_phone=phone, diner_email=email,
                     notes=demo_data.notes(day, index)))
            continue
        start = wall.replace(tzinfo=zone)
        try:
            async with db.begin_nested():
                await _book(db, res_id, day, index, start, party, rules, now)
            existing.add(res_id)
        except booking.ReservationError as e:
            print(f"  skipped {res_id} ({start.isoformat()}, party {party}): {e.code}")
    return len(existing)


async def _seed_subscription(db: AsyncSession) -> int:
    og_id = (await db.execute(select(Company.ownership_group_id).where(
        Company.id == COMPANY_ID))).scalar_one_or_none()
    if og_id is None:
        print("  demo company has no ownership group: no AI host subscription")
        return 0
    await db.execute(pg_insert(ReservationSubscription).values(
        id=SUBSCRIPTION_ID, ownership_group_id=og_id, status=COMPED,
        cancel_at_period_end=False,
    ).on_conflict_do_nothing())
    row = (await db.execute(select(ReservationSubscription).where(
        ReservationSubscription.ownership_group_id == og_id))).scalar_one()
    if row.stripe_subscription_id is not None:
        # Fail closed: a real Stripe subscription is never overwritten.
        print(f"  demo group has Stripe subscription {row.stripe_subscription_id} "
              f"({row.status}): left as it is")
    elif row.status != COMPED:
        row.status = COMPED
        row.current_period_start = row.current_period_end = None
        row.cancel_at_period_end = False
        await db.flush()
    return 1


async def seed_reservations(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Seed the demo restaurant; returns the count of each kind of row it
    upserted. Flushes; the caller commits. `now` defaults to the clock (UTC)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    loc = await _location(db)
    if loc.name == SEED_PLACEHOLDER_NAME:
        loc.name = RESTAURANT_NAME
        await db.flush()
    zone = ZoneInfo(loc.timezone)
    today = now.astimezone(zone).date()  # the location's day, from UTC now
    await _seed_floor(db, now)
    await _seed_rules(db, today, now)
    rules, _ = await get_rules(db, COMPANY_ID, LOCATION_ID)
    return {
        "sections": len(SECTIONS),
        "tables": len(TABLES),
        "combinations": 1,
        "rules": 1,
        "reservations": await _seed_bookings(db, rules, zone, today, now),
        "subscriptions": await _seed_subscription(db),
    }


async def main() -> None:
    if settings.ENV == "production" and not settings.DEMO_SEED_PASSWORD:
        raise SystemExit(
            "Refusing to seed: ENV=production but DEMO_SEED_PASSWORD is unset. "
            "The demo tenant must be seeded with it first (python -m backend.seed); "
            "set it from AWS Secrets Manager (wizscheduler/prod/DEMO_SEED_PASSWORD) "
            "and re-run."
        )
    async with async_session_factory() as db:
        counts = await seed_reservations(db)
        await db.commit()
    await engine.dispose()
    print("Reservations demo seeded: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    asyncio.run(main())
