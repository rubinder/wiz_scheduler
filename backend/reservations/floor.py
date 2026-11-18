"""Floor plan service: sections, tables and table combinations for a location.

Ids in the payload are never trusted: foreign-key checks bypass row-level
security, so every id is loaded scoped to (company_id, location_id) and an
id that does not match raises FloorInvalid("unknown_id").
"""

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.archive import archive_dining_table
from backend.reservations.models import (
    DiningTable,
    FloorSection,
    ReservationTable,
    TableCombination,
)


class FloorInvalid(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TableHasBookings(Exception):
    def __init__(self, table_ids: list[str]) -> None:
        super().__init__(f"tables have upcoming bookings: {table_ids}")
        self.table_ids = table_ids


class SectionIO(BaseModel):
    id: str | None = None
    key: str
    name: str
    sort_order: int = 0


class TableIO(BaseModel):
    id: str | None = None
    label: str
    section_key: str | None = None
    min_seats: int
    max_seats: int
    combinable: bool = False
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    rotation: float = 0.0


class CombinationIO(BaseModel):
    id: str | None = None
    name: str
    table_labels: list[str]


class CombinationOut(CombinationIO):
    min_seats: int = 0
    max_seats: int = 0


class FloorIn(BaseModel):
    sections: list[SectionIO] = []
    tables: list[TableIO] = []
    combinations: list[CombinationIO] = []


class FloorOut(BaseModel):
    sections: list[SectionIO] = []
    tables: list[TableIO] = []
    combinations: list[CombinationOut] = []


async def get_floor(db: AsyncSession, company_id: str, location_id: str) -> FloorOut:
    scope = (company_id, location_id)
    sections = (await db.execute(
        select(FloorSection).where(
            FloorSection.company_id == scope[0], FloorSection.location_id == scope[1]
        ).order_by(FloorSection.sort_order, FloorSection.name, FloorSection.id)
    )).scalars().all()
    tables = (await db.execute(
        select(DiningTable).where(
            DiningTable.company_id == scope[0], DiningTable.location_id == scope[1],
            DiningTable.archived_at.is_(None),
        ).order_by(DiningTable.label)
    )).scalars().all()
    combos = (await db.execute(
        select(TableCombination).where(
            TableCombination.company_id == scope[0],
            TableCombination.location_id == scope[1],
        ).order_by(TableCombination.name)
    )).scalars().all()
    label_by_id = {t.id: t.label for t in tables}
    # The section id doubles as the key on output.
    return FloorOut(
        sections=[SectionIO(id=s.id, key=s.id, name=s.name, sort_order=s.sort_order)
                  for s in sections],
        tables=[TableIO(
            id=t.id, label=t.label, section_key=t.section_id,
            min_seats=t.min_seats, max_seats=t.max_seats,
            combinable=t.combinable, x=t.x, y=t.y, w=t.w, h=t.h,
            rotation=t.rotation) for t in tables],
        combinations=[CombinationOut(
            id=c.id, name=c.name,
            table_labels=[label_by_id[i] for i in c.table_ids if i in label_by_id],
            min_seats=c.min_seats, max_seats=c.max_seats) for c in combos
            if all(i in label_by_id for i in c.table_ids)],
    )


def _validate(floor: FloorIn) -> None:
    labels = [t.label for t in floor.tables]
    if len(set(labels)) != len(labels):
        raise FloorInvalid("duplicate_label", "table labels must be unique")
    keys = [s.key for s in floor.sections]
    if len(set(keys)) != len(keys):
        raise FloorInvalid("duplicate_section_key", "section keys must be unique")
    for t in floor.tables:
        if t.min_seats < 1 or t.min_seats > t.max_seats:
            raise FloorInvalid(
                "bad_seats", f"table {t.label}: min_seats must be 1..max_seats")
        if t.section_key is not None and t.section_key not in keys:
            raise FloorInvalid(
                "unknown_section", f"table {t.label}: unknown section {t.section_key}")
    by_label = {t.label: t for t in floor.tables}
    for c in floor.combinations:
        if len(set(c.table_labels)) < 2:
            raise FloorInvalid(
                "bad_combination", f"combination {c.name} needs at least 2 tables")
        for lbl in c.table_labels:
            t = by_label.get(lbl)
            if t is None:
                raise FloorInvalid(
                    "bad_combination", f"combination {c.name}: unknown table {lbl}")
            if not t.combinable:
                raise FloorInvalid(
                    "bad_combination", f"combination {c.name}: {lbl} is not combinable")


async def _load_owned(db, model, ids: list[str], company_id: str, location_id: str,
                      extra=()) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(
        select(model).where(
            model.id.in_(ids), model.company_id == company_id,
            model.location_id == location_id, *extra)
    )).scalars().all()
    found = {r.id: r for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise FloorInvalid("unknown_id", f"unknown id(s): {', '.join(missing)}")
    return found


async def save_floor(
    db: AsyncSession, company_id: str, location_id: str, floor: FloorIn,
    now: datetime | None = None,
) -> FloorOut:
    """Replace the location's floor plan with the payload. Does not commit."""
    now = now or datetime.now(timezone.utc)
    _validate(floor)

    sec_rows = await _load_owned(
        db, FloorSection, [s.id for s in floor.sections if s.id],
        company_id, location_id)
    tbl_rows = await _load_owned(
        db, DiningTable, [t.id for t in floor.tables if t.id],
        company_id, location_id, extra=(DiningTable.archived_at.is_(None),))
    combo_rows = await _load_owned(
        db, TableCombination, [c.id for c in floor.combinations if c.id],
        company_id, location_id)

    existing_tables = (await db.execute(
        select(DiningTable).where(
            DiningTable.company_id == company_id,
            DiningTable.location_id == location_id,
            DiningTable.archived_at.is_(None))
    )).scalars().all()
    keep_ids = {t.id for t in floor.tables if t.id}
    to_archive = [t for t in existing_tables if t.id not in keep_ids]
    if to_archive:
        booked = (await db.execute(
            select(ReservationTable.table_id).where(
                ReservationTable.table_id.in_([t.id for t in to_archive]),
                ReservationTable.active.is_(True),
                func.upper(ReservationTable.during) > now,
            ).distinct()
        )).scalars().all()
        if booked:
            raise TableHasBookings(sorted(booked))

    # Sections.
    section_by_key: dict[str, FloorSection] = {}
    for s in floor.sections:
        row = sec_rows.get(s.id) if s.id else None
        if row is None:
            row = FloorSection(company_id=company_id, location_id=location_id,
                               name=s.name, sort_order=s.sort_order)
            db.add(row)
        else:
            row.name, row.sort_order = s.name, s.sort_order
        section_by_key[s.key] = row
    await db.flush()
    # Section ids referenced by tables may be an existing id used as key.
    # Existing sections not in the payload are deleted (tables SET NULL).
    kept_sections = {r.id for r in section_by_key.values()}
    for s in (await db.execute(
        select(FloorSection).where(
            FloorSection.company_id == company_id,
            FloorSection.location_id == location_id)
    )).scalars().all():
        if s.id not in kept_sections:
            await db.delete(s)

    # Archive omitted tables first so their labels are free to reuse.
    for t in to_archive:
        archive_dining_table(t, now=now)
    await db.flush()

    table_by_label: dict[str, DiningTable] = {}
    for t in floor.tables:
        row = tbl_rows.get(t.id) if t.id else None
        section = section_by_key[t.section_key] if t.section_key else None
        if row is None:
            row = DiningTable(company_id=company_id, location_id=location_id)
            db.add(row)
        row.label, row.section_id = t.label, section.id if section else None
        row.min_seats, row.max_seats = t.min_seats, t.max_seats
        row.combinable = t.combinable
        row.x, row.y, row.w, row.h, row.rotation = t.x, t.y, t.w, t.h, t.rotation
        table_by_label[t.label] = row
    await db.flush()

    # Combinations.
    kept_combos: set[str] = set()
    for c in floor.combinations:
        members = [table_by_label[lbl] for lbl in dict.fromkeys(c.table_labels)]
        row = combo_rows.get(c.id) if c.id else None
        if row is None:
            row = TableCombination(company_id=company_id, location_id=location_id)
            db.add(row)
        row.name = c.name
        row.table_ids = [m.id for m in members]
        row.min_seats = sum(m.min_seats for m in members)
        row.max_seats = sum(m.max_seats for m in members)
        await db.flush()
        kept_combos.add(row.id)
    for c in (await db.execute(
        select(TableCombination).where(
            TableCombination.company_id == company_id,
            TableCombination.location_id == location_id)
    )).scalars().all():
        if c.id not in kept_combos:
            await db.delete(c)
    await db.flush()
    return await get_floor(db, company_id, location_id)
