"""Read-only reservation listings for the manager API."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.models import DiningTable, Reservation, ReservationTable


async def table_labels(
    db: AsyncSession, company_id: str, reservation_ids: list[str]
) -> dict[str, list[str]]:
    """Labels of the tables each reservation holds or held, sorted."""
    if not reservation_ids:
        return {}
    rows = (await db.execute(
        select(ReservationTable.reservation_id, DiningTable.label)
        .join(DiningTable, DiningTable.id == ReservationTable.table_id)
        .where(
            ReservationTable.company_id == company_id,
            ReservationTable.reservation_id.in_(reservation_ids),
        )
    )).all()
    out: dict[str, list[str]] = {rid: [] for rid in reservation_ids}
    for rid, label in rows:
        out[rid].append(label)
    for labels in out.values():
        labels.sort()
    return out


async def list_between(
    db: AsyncSession, company_id: str, location_id: str,
    window_start: datetime, window_end: datetime,
) -> list[tuple[Reservation, list[str]]]:
    """Every reservation (all statuses) starting in [window_start, window_end)."""
    rows = (await db.execute(
        select(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.start_at >= window_start,
            Reservation.start_at < window_end,
        ).order_by(Reservation.start_at, Reservation.id)
    )).scalars().all()
    labels = await table_labels(db, company_id, [r.id for r in rows])
    return [(r, labels.get(r.id, [])) for r in rows]
