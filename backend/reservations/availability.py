"""The availability evaluator: what a location can book, and on which tables.

`compute_slots` is pure and holds every rule; `find_slots` only loads the
floor, the rules and the busy rows for one location and calls it. Agents, the
web booking page and staff screens all go through here, so there is one
definition of "bookable".

Two clocks per booking: dining time [start, dining_end) is when covers are in
the room (restaurant capacity, pacing, section limits), and occupied time
[start, end) adds the band's churn and is when the table cannot be reseated.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location
from backend.reservations.models import (
    ACTIVE_STATUSES,
    HELD,
    PENDING,
    DiningTable,
    Reservation,
    ReservationTable,
    TableCombination,
)
from backend.reservations.rules import RulesSchema, get_rules
from backend.services.archive import active


@dataclass(frozen=True)
class TableSpec:
    id: str
    label: str
    section_id: str | None
    min_seats: int
    max_seats: int


@dataclass(frozen=True)
class ComboSpec:
    id: str
    name: str
    table_ids: tuple[str, ...]
    min_seats: int
    max_seats: int


@dataclass(frozen=True)
class Busy:
    """One table held by one reservation.

    `end` includes the churn of that party's band (the table is unavailable
    until reset); `dining_end` is the reservation's end_at, when its covers
    leave the room.
    """

    reservation_id: str
    table_id: str
    start: datetime
    end: datetime
    dining_end: datetime
    party_size: int
    section_id: str | None


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    table_ids: tuple[str, ...]
    section_id: str | None


@dataclass(frozen=True)
class _Seating:
    table_ids: tuple[str, ...]
    sections: frozenset[str | None]

    @property
    def section_id(self) -> str | None:
        return next(iter(self.sections)) if len(self.sections) == 1 else None


def _candidates(
    rules: RulesSchema, zone: ZoneInfo, window_start: datetime, window_end: datetime
) -> list[datetime]:
    first: date = window_start.astimezone(zone).date()
    last: date = window_end.astimezone(zone).date()
    step = timedelta(minutes=rules.slot_interval_minutes)
    seen: dict[datetime, datetime] = {}
    d = first
    while d <= last:
        for period in rules.periods_for(d):
            wall = datetime.combine(d, period.first_seating)
            stop = datetime.combine(d, period.last_seating)
            while wall <= stop:
                cand = wall.replace(tzinfo=zone, fold=0)
                round_trip = cand.astimezone(timezone.utc).astimezone(zone)
                if round_trip.replace(tzinfo=None) == wall:  # skip spring-forward gaps
                    seen.setdefault(cand.astimezone(timezone.utc), cand)
                wall += step
        d += timedelta(days=1)
    return [seen[k] for k in sorted(seen)]


def _seatings(
    tables: list[TableSpec], combos: list[ComboSpec], party_size: int
) -> list[_Seating]:
    by_id = {t.id: t for t in tables}
    singles = sorted(
        (t for t in tables if t.min_seats <= party_size <= t.max_seats),
        key=lambda t: (t.max_seats, t.label),
    )
    fitting = sorted(
        (c for c in combos
         if c.min_seats <= party_size <= c.max_seats
         and c.table_ids and all(i in by_id for i in c.table_ids)),
        key=lambda c: (c.max_seats, c.name),
    )
    out = [_Seating((t.id,), frozenset({t.section_id})) for t in singles]
    out += [
        _Seating(tuple(c.table_ids), frozenset(by_id[i].section_id for i in c.table_ids))
        for c in fitting
    ]
    return out


def _peak_covers(dining: dict[str, tuple[datetime, datetime, int]],
                 start: datetime, end: datetime) -> int:
    """Peak seated covers of the given reservations at any instant in [start, end)."""
    overlapping = [(s, e, p) for s, e, p in dining.values() if s < end and e > start]
    points = {start} | {s for s, _, _ in overlapping if s > start}
    return max(
        (sum(p for s, e, p in overlapping if s <= t < e) for t in points), default=0
    )


def compute_slots(
    rules: RulesSchema,
    tz_name: str,
    tables: list[TableSpec],
    combos: list[ComboSpec],
    busy: list[Busy],
    party_size: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    *,
    apply_cutoff: bool = True,
) -> list[Slot]:
    """Bookable start times for one party, one Slot per time, ordered by start.

    `apply_cutoff=False` (staff bookings) skips `cutoff_minutes`; the booking
    horizon and "not in the past" still apply.
    """
    if party_size < 1 or party_size > rules.max_party_size:
        return []
    zone = ZoneInfo(tz_name)
    band = rules.band_for(party_size)
    dining_len = timedelta(minutes=band.minutes)
    occupied_len = timedelta(minutes=band.minutes + band.churn_minutes)
    earliest = now + timedelta(minutes=rules.cutoff_minutes) if apply_cutoff else now
    last_date = now.astimezone(zone).date() + timedelta(days=rules.booking_horizon_days)

    by_table: dict[str, list[Busy]] = {}
    dining: dict[str, tuple[datetime, datetime, int]] = {}
    by_section: dict[str | None, dict[str, tuple[datetime, datetime, int]]] = {}
    for b in busy:
        by_table.setdefault(b.table_id, []).append(b)
        entry = (b.start, b.dining_end, b.party_size)
        dining[b.reservation_id] = entry
        by_section.setdefault(b.section_id, {})[b.reservation_id] = entry
    seatings = _seatings(tables, combos, party_size)

    def table_free(table_id: str, start: datetime, until: datetime) -> bool:
        return all(not (b.start < until and b.end > start) for b in by_table.get(table_id, ()))

    def section_ok(seating: _Seating, start: datetime, end: datetime) -> bool:
        for section in seating.sections:
            limit = rules.section_cover_limits.get(section) if section else None
            if limit is None:
                continue
            seated = sum(p for s, e, p in by_section.get(section, {}).values()
                         if s < end and e > start)
            if seated + party_size > limit:
                return False
        return True

    slots: list[Slot] = []
    for start in _candidates(rules, zone, window_start, window_end):
        if not (window_start <= start < window_end) or start < earliest:
            continue
        if start.date() > last_date:
            continue
        end = start + dining_len
        if rules.max_concurrent_covers is not None:
            if _peak_covers(dining, start, end) + party_size > rules.max_concurrent_covers:
                continue
        if rules.max_covers_per_slot is not None:
            starting = sum(p for s, _, p in dining.values() if s == start)
            if starting + party_size > rules.max_covers_per_slot:
                continue
        until = start + occupied_len
        for seating in seatings:
            if all(table_free(t, start, until) for t in seating.table_ids) and section_ok(
                seating, start, end
            ):
                slots.append(Slot(start, end, seating.table_ids, seating.section_id))
                break
    return slots


async def find_slots(
    db: AsyncSession,
    company_id: str,
    location_id: str,
    party_size: int,
    window_start: datetime,
    window_end: datetime,
    now: datetime | None = None,
    exclude_reservation_id: str | None = None,
    *,
    apply_cutoff: bool = True,
) -> list[Slot]:
    """Load one location's floor, rules and busy tables, then `compute_slots`."""
    now = now or datetime.now(timezone.utc)
    location = (await db.execute(
        select(Location).where(Location.id == location_id, Location.company_id == company_id)
    )).scalar_one_or_none()
    if location is None:
        return []
    rules, _ = await get_rules(db, company_id, location_id)

    table_rows = (await db.execute(
        select(DiningTable).where(
            DiningTable.company_id == company_id,
            DiningTable.location_id == location_id,
            active(DiningTable),
        )
    )).scalars().all()
    tables = [TableSpec(t.id, t.label, t.section_id, t.min_seats, t.max_seats)
              for t in table_rows]
    combo_rows = (await db.execute(
        select(TableCombination).where(
            TableCombination.company_id == company_id,
            TableCombination.location_id == location_id,
        )
    )).scalars().all()
    combos = [ComboSpec(c.id, c.name, tuple(c.table_ids), c.min_seats, c.max_seats)
              for c in combo_rows]

    # Anything that could touch the window: the longest band plus churn is
    # well under a day, so a day of slack either side is enough.
    slack = timedelta(days=1)
    query = (
        select(Reservation, ReservationTable, DiningTable.section_id)
        .join(ReservationTable, ReservationTable.reservation_id == Reservation.id)
        .join(DiningTable, DiningTable.id == ReservationTable.table_id)
        .where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            ReservationTable.company_id == company_id,
            DiningTable.company_id == company_id,
            DiningTable.location_id == location_id,
            ReservationTable.active.is_(True),
            Reservation.status.in_(ACTIVE_STATUSES),
            or_(
                Reservation.status.not_in((HELD, PENDING)),
                and_(Reservation.held_until.is_not(None), Reservation.held_until > now),
            ),
            Reservation.start_at < window_end + slack,
            Reservation.end_at > window_start - slack,
        )
    )
    if exclude_reservation_id is not None:
        query = query.where(Reservation.id != exclude_reservation_id)
    busy = [
        Busy(res.id, rt.table_id, res.start_at, rt.during.upper, res.end_at,
             res.party_size, section_id)
        for res, rt, section_id in (await db.execute(query)).all()
    ]
    return compute_slots(
        rules, location.timezone, tables, combos, busy, party_size,
        window_start, window_end, now, apply_cutoff=apply_cutoff,
    )
