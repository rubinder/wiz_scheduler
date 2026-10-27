"""Rendering approved hours as CSV, and recording that it happened.

CSV is the one "provider" every payroll system accepts today: it needs no
partnership, and it validates that our hours are correct before any live write
can embarrass us. The exporter is a plain function, not a PayrollProvider
protocol — see "Future Adapter Seam" in the spec.
"""

import csv
import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Location, PayrollExport, TimeEntry, User
from backend.services.billing import get_ownership_group_id
from backend.services.time_entries import _as_utc

logger = logging.getLogger(__name__)

CSV_HEADER = [
    "employee_id", "employee_name", "pay_date",
    "location_id", "location_name", "role_id", "role_name",
    "start_time", "end_time", "paid_hours",
    "source", "checked_in_at", "lateness_minutes",
]

# The full-history export (#172) is every retained record, approved or not,
# so it says which. Appended, never inserted: the per-range file a payroll
# bureau already imports keeps CSV_HEADER byte for byte.
HISTORY_CSV_HEADER = [*CSV_HEADER, "approved"]

# Leading characters a spreadsheet application treats as the start of a
# formula. A name, location or role name that starts with one of these opens
# a formula-injection hole the moment a manager opens the file in Excel or
# Sheets — see CWE-1236.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(v: str) -> str:
    """Defuse leading-character CSV formula injection in free-text columns.

    Only applied to free text a user actually controls (employee_name,
    location_name, role_name) — never to ids, dates, numbers or `source`,
    which are never user-authored strings that reach a spreadsheet formula
    bar unescaped.
    """
    if v.startswith(_FORMULA_PREFIXES):
        return "'" + v
    return v


@dataclass(frozen=True)
class PayrollCsvRow:
    """One exported line, carrying domain values rather than pre-formatted
    text — the formatting rules live in render_csv so they are testable in one
    place. `paid_minutes` renders into the `paid_hours` column. `timezone` is
    the IANA zone `start_time`/`end_time`/`checked_in_at` are rendered into —
    the location's, per the controller ruling that a pay file should read in
    local wall-clock faces rather than whatever offset happened to be stored."""

    employee_id: str
    employee_name: str
    pay_date: date
    location_id: str
    location_name: str
    role_id: str
    role_name: str
    start_time: datetime
    end_time: datetime
    paid_minutes: int
    source: str
    checked_in_at: datetime | None
    lateness_minutes: int | None
    timezone: str
    # Rendered only by the full-history export (render_csv(history=True)).
    approved: bool = False


def _local_iso(dt: datetime, tz_name: str) -> str:
    """Same instant, rendered in the location's zone with the offset intact.

    _as_utc first: SQLite drops tzinfo on read, and .astimezone() on a naive
    value would interpret it in the HOST's zone — a CSV whose times depend on
    where the developer lives. Same guard list_time_entries applies.
    """
    return _as_utc(dt).astimezone(ZoneInfo(tz_name)).isoformat()


def _csv_fields(row: PayrollCsvRow, history: bool) -> list[str]:
    fields = [
        row.employee_id,
        _neutralize(row.employee_name),
        row.pay_date.isoformat(),
        row.location_id,
        _neutralize(row.location_name),
        row.role_id,
        _neutralize(row.role_name),
        _local_iso(row.start_time, row.timezone),
        _local_iso(row.end_time, row.timezone),
        f"{row.paid_minutes / 60:.2f}",
        row.source,
        _local_iso(row.checked_in_at, row.timezone) if row.checked_in_at else "",
        "" if row.lateness_minutes is None else str(row.lateness_minutes),
    ]
    if history:
        fields.append("yes" if row.approved else "no")
    return fields


def csv_preamble(history: bool = False) -> str:
    """The BOM and the header line — everything before the first row."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerow(
        HISTORY_CSV_HEADER if history else CSV_HEADER
    )
    return "\ufeff" + buf.getvalue()


def render_csv_rows(rows: Iterable[PayrollCsvRow], history: bool = False) -> str:
    """Rows only, no BOM or header, so a long export can be rendered a chunk
    at a time and the chunks concatenated."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    for row in rows:
        writer.writerow(_csv_fields(row, history))
    return buf.getvalue()


def render_csv(rows: Iterable[PayrollCsvRow], *, history: bool = False) -> str:
    """Pure. RFC 4180 line endings and a UTF-8 BOM.

    The BOM is not decoration: we ship in 19 locales including Arabic,
    Bengali, Tamil and Telugu, employee names are entered in those scripts,
    and Excel on Windows renders a BOM-less UTF-8 CSV as mojibake — which a
    payroll clerk will read as our bug.

    Hours rather than minutes because that is what every payroll import
    template takes. checked_in_at and lateness_minutes are EMPTY, not "0", for
    an attested row: we did not observe an on-time arrival, we observed
    nothing. Free-text columns are run through _neutralize to defuse CSV
    formula injection.

    history=True is the full-history export (#172): HISTORY_CSV_HEADER and a
    trailing approved yes/no column. history=False is byte-identical to the
    per-range export as it was before #172.
    """
    return csv_preamble(history) + render_csv_rows(rows, history)


async def export_approved(
    db: AsyncSession,
    company_id: str,
    user: User,
    range_start: date,
    range_end: date,
    location_id: str | None = None,
    include_exported: bool = False,
) -> tuple[str, PayrollExport]:
    """Select, render, stamp and log — in one commit.

    One commit so the audit row and the stamps cannot disagree.

    exported_at and payroll_export_id are stamped only where they are NULL.
    include_exported=true exists for re-downloading a file a manager lost; it
    never re-stamps, because deleting an audit log must never un-export a pay
    period and a re-download is not a second export.
    """
    query = (
        select(
            TimeEntry, TimeEntry.employee_name, TimeEntry.location_name,
            TimeEntry.location_timezone,
        )
        .where(
            TimeEntry.company_id == company_id,
            TimeEntry.pay_date >= range_start,
            TimeEntry.pay_date <= range_end,
            TimeEntry.approved_at.isnot(None),
        )
        .order_by(TimeEntry.pay_date, TimeEntry.employee_name)
        # Locks the selected rows (PostgreSQL; SQLite ignores it) so a double
        # click can't have two concurrent exports both see exported_at IS NULL
        # for the same entry and both believe they are the one that stamped it.
        #
        # of=TimeEntry keeps the lock on the rows being stamped only.
        .with_for_update(of=TimeEntry)
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    if not include_exported:
        query = query.where(TimeEntry.exported_at.is_(None))

    found = (await db.execute(query)).all()
    if not found:
        skipped = await _count_already_exported(
            db, company_id, range_start, range_end, location_id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "nothing_to_export",
                "message": (
                    f"No approved, unexported hours in this range. "
                    f"{skipped} approved entr"
                    f"{'y was' if skipped == 1 else 'ies were'} already "
                    f"exported."
                ),
                "already_exported": skipped,
            },
        )

    now = datetime.now(timezone.utc)
    location_name: str | None = None
    if location_id:
        location_name = (await db.execute(
            select(Location.name).where(
                Location.id == location_id, Location.company_id == company_id
            )
        )).scalar_one_or_none()
    export = PayrollExport(
        company_id=company_id,
        # None for the OG-less dev company — hence the nullable column.
        ownership_group_id=await get_ownership_group_id(db, company_id),
        exported_by_user_id=user.id,
        format="csv",
        location_id=location_id,
        location_name=location_name,
        range_start=range_start,
        range_end=range_end,
        entry_count=len(found),
        paid_minutes_total=sum(entry.paid_minutes for entry, _, _, _ in found),
    )
    db.add(export)
    await db.flush()

    csv_rows: list[PayrollCsvRow] = []
    for entry, employee_name, location_name, location_timezone in found:
        csv_rows.append(PayrollCsvRow(
            employee_id=entry.employee_id,
            employee_name=employee_name,
            pay_date=entry.pay_date,
            location_id=entry.location_id,
            location_name=location_name,
            role_id=entry.role_id,
            role_name=entry.role_name,
            start_time=entry.start_time,
            end_time=entry.end_time,
            paid_minutes=entry.paid_minutes,
            source=entry.source,
            checked_in_at=entry.checked_in_at,
            lateness_minutes=entry.lateness_minutes,
            timezone=location_timezone,
        ))
        if entry.exported_at is None:
            entry.exported_at = now
            entry.payroll_export_id = export.id

    content = render_csv(csv_rows)
    await db.commit()
    # No db.refresh(export) here: the session factory is expire_on_commit=False
    # (backend/database.py), so export's attributes are already populated from
    # the flush above and nothing downstream reads a server-generated column
    # that changed at commit time.
    logger.info(
        "payroll.export company_id=%s export_id=%s entries=%d minutes=%d",
        company_id, export.id, export.entry_count, export.paid_minutes_total,
    )
    return content, export


async def _count_already_exported(
    db: AsyncSession,
    company_id: str,
    range_start: date,
    range_end: date,
    location_id: str | None,
) -> int:
    """So "nothing happened" is never mysterious."""
    query = select(func.count(TimeEntry.id)).where(
        TimeEntry.company_id == company_id,
        TimeEntry.pay_date >= range_start,
        TimeEntry.pay_date <= range_end,
        TimeEntry.approved_at.isnot(None),
        TimeEntry.exported_at.isnot(None),
    )
    if location_id:
        query = query.where(TimeEntry.location_id == location_id)
    return (await db.execute(query)).scalar_one()


# Rows rendered per chunk of the full-history export. Each chunk is encoded
# as soon as it is rendered, so the response never holds the ORM rows, the
# text and the bytes of the whole file at once.
_HISTORY_CHUNK_ROWS = 2000


async def export_history(
    db: AsyncSession, company_id: str, user: User
) -> tuple[list[bytes], PayrollExport]:
    """Every retained pay record for one company, as CSV chunks (#172).

    All locations, approved or not, exported or not: this is the "take your
    records with you" file, offered before the seven-year sweep and to a
    canceled group inside its grace period. It is a read: nothing on the
    entries is stamped — exported_at means "sent to payroll", and a history
    download is not that. One PayrollExport row records that it happened,
    with location_id NULL and the range spanning the oldest to newest record.

    Scoped to the caller's company only, like the per-range export, even
    where the ownership group owns several companies.

    Columns, not entities, read through a server-side cursor in partitions:
    seven years can be ~360k rows, and the rows are rendered and encoded as
    they arrive rather than materialised as ORM objects. Peak memory is the
    encoded file (roughly 250 bytes a row, so ~90 MB at that size) — bounded,
    and released as the StreamingResponse drains it.
    """
    query = (
        select(
            TimeEntry.employee_id, TimeEntry.employee_name, TimeEntry.pay_date,
            TimeEntry.location_id, TimeEntry.location_name, TimeEntry.role_id,
            TimeEntry.role_name, TimeEntry.start_time, TimeEntry.end_time,
            TimeEntry.paid_minutes, TimeEntry.source, TimeEntry.checked_in_at,
            TimeEntry.lateness_minutes, TimeEntry.location_timezone,
            TimeEntry.approved_at,
        )
        .where(TimeEntry.company_id == company_id)
        .order_by(TimeEntry.pay_date, TimeEntry.start_time, TimeEntry.id)
        .execution_options(yield_per=_HISTORY_CHUNK_ROWS)
    )

    chunks: list[bytes] = [csv_preamble(history=True).encode("utf-8")]
    count = 0
    minutes = 0
    first: date | None = None
    last: date | None = None
    result = await db.stream(query)
    async for partition in result.partitions(_HISTORY_CHUNK_ROWS):
        rows = [
            PayrollCsvRow(
                employee_id=r.employee_id, employee_name=r.employee_name,
                pay_date=r.pay_date, location_id=r.location_id,
                location_name=r.location_name, role_id=r.role_id,
                role_name=r.role_name, start_time=r.start_time,
                end_time=r.end_time, paid_minutes=r.paid_minutes,
                source=r.source, checked_in_at=r.checked_in_at,
                lateness_minutes=r.lateness_minutes,
                timezone=r.location_timezone,
                approved=r.approved_at is not None,
            )
            for r in partition
        ]
        if first is None:
            first = rows[0].pay_date
        last = rows[-1].pay_date
        count += len(rows)
        minutes += sum(r.paid_minutes for r in rows)
        chunks.append(render_csv_rows(rows, history=True).encode("utf-8"))

    if count == 0 or first is None or last is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "nothing_to_export",
                "message": "There are no pay records to download.",
            },
        )

    # A full-history export: every retained record, all locations, so no
    # location and a range from the oldest pay date to the newest.
    export = PayrollExport(
        company_id=company_id,
        ownership_group_id=await get_ownership_group_id(db, company_id),
        exported_by_user_id=user.id,
        format="csv",
        location_id=None,
        location_name=None,
        range_start=first,
        range_end=last,
        entry_count=count,
        paid_minutes_total=minutes,
    )
    db.add(export)
    await db.commit()
    logger.info(
        "payroll.export_history company_id=%s export_id=%s entries=%d",
        company_id, export.id, count,
    )
    return chunks, export
