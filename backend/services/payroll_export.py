"""Rendering approved hours as CSV, and recording that it happened.

CSV is the one "provider" every payroll system accepts today: it needs no
partnership, and it validates that our hours are correct before any live write
can embarrass us. The exporter is a plain function, not a PayrollProvider
protocol — see "Future Adapter Seam" in the spec.
"""

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Location, PayrollExport, TimeEntry, User
from backend.services.billing import get_ownership_group_id

logger = logging.getLogger(__name__)

CSV_HEADER = [
    "employee_id", "employee_name", "pay_date",
    "location_id", "location_name", "role_id", "role_name",
    "start_time", "end_time", "paid_hours",
    "source", "checked_in_at", "lateness_minutes",
]


@dataclass(frozen=True)
class PayrollCsvRow:
    """One exported line, carrying domain values rather than pre-formatted
    text — the formatting rules live in render_csv so they are testable in one
    place. `paid_minutes` renders into the `paid_hours` column."""

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


def render_csv(rows: list[PayrollCsvRow]) -> str:
    """Pure. RFC 4180 line endings and a UTF-8 BOM.

    The BOM is not decoration: we ship in 19 locales including Arabic,
    Bengali, Tamil and Telugu, employee names are entered in those scripts,
    and Excel on Windows renders a BOM-less UTF-8 CSV as mojibake — which a
    payroll clerk will read as our bug.

    Hours rather than minutes because that is what every payroll import
    template takes. checked_in_at and lateness_minutes are EMPTY, not "0", for
    an attested row: we did not observe an on-time arrival, we observed
    nothing.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([
            row.employee_id,
            row.employee_name,
            row.pay_date.isoformat(),
            row.location_id,
            row.location_name,
            row.role_id,
            row.role_name,
            row.start_time.isoformat(),
            row.end_time.isoformat(),
            f"{row.paid_minutes / 60:.2f}",
            row.source,
            row.checked_in_at.isoformat() if row.checked_in_at else "",
            "" if row.lateness_minutes is None else str(row.lateness_minutes),
        ])
    return "﻿" + buf.getvalue()


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
        select(TimeEntry, Employee.full_name, Location.name)
        .join(Employee, Employee.id == TimeEntry.employee_id)
        .join(Location, Location.id == TimeEntry.location_id)
        .where(
            TimeEntry.company_id == company_id,
            TimeEntry.pay_date >= range_start,
            TimeEntry.pay_date <= range_end,
            TimeEntry.approved_at.isnot(None),
        )
        .order_by(TimeEntry.pay_date, Employee.full_name)
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
    export = PayrollExport(
        company_id=company_id,
        # None for the OG-less dev company — hence the nullable column.
        ownership_group_id=await get_ownership_group_id(db, company_id),
        exported_by_user_id=user.id,
        format="csv",
        location_id=location_id,
        range_start=range_start,
        range_end=range_end,
        entry_count=len(found),
        paid_minutes_total=sum(entry.paid_minutes for entry, _, _ in found),
    )
    db.add(export)
    await db.flush()

    csv_rows: list[PayrollCsvRow] = []
    for entry, employee_name, location_name in found:
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
        ))
        if entry.exported_at is None:
            entry.exported_at = now
            entry.payroll_export_id = export.id

    content = render_csv(csv_rows)
    await db.commit()
    await db.refresh(export)
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
