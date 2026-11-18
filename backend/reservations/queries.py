"""Read-only reservation listings for the manager API."""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.models import (
    BOOKED, EXPIRED, HELD, PENDING, SEATED, DiningTable, Reservation,
    ReservationTable,
)


async def count_upcoming(
    db: AsyncSession, company_id: str, location_id: str, now: datetime
) -> int:
    """Reservations diners still expect to honour: booked, seated, or a live
    hold/pending (held_until NULL fails closed), starting at or after `now`."""
    return (await db.execute(
        select(func.count()).select_from(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.start_at >= now,
            or_(
                Reservation.status.in_((BOOKED, SEATED)),
                Reservation.status.in_((HELD, PENDING)) & or_(
                    Reservation.held_until.is_(None), Reservation.held_until > now),
            ),
        )
    )).scalar_one()


def effective_status(res: Reservation, now: datetime) -> str:
    """The status a reader should see: a held or pending row whose hold has
    lapsed is `expired` even before a write sweeps it. Never writes."""
    if (res.status in (HELD, PENDING) and res.held_until is not None
            and res.held_until <= now):
        return EXPIRED
    return res.status


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
    window_start: datetime, window_end: datetime, now: datetime | None = None,
) -> list[tuple[Reservation, list[str], str]]:
    """Every reservation (all statuses) starting in [window_start, window_end),
    with its table labels and `effective_status`. Read-only: lapsed holds are
    reported as expired, not swept."""
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(
        select(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.start_at >= window_start,
            Reservation.start_at < window_end,
        ).order_by(Reservation.start_at, Reservation.id)
    )).scalars().all()
    labels = await table_labels(db, company_id, [r.id for r in rows])
    return [(r, labels.get(r.id, []), effective_status(r, now)) for r in rows]


async def list_pending_approvals(
    db: AsyncSession, company_id: str, location_id: str, now: datetime | None = None,
) -> list[tuple[Reservation, list[str], str]]:
    """The location's approval queue across every day: pending requests whose
    hold is still live (held_until NULL or in the future) and that have not
    started, soonest first, with table labels and `effective_status`."""
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(
        select(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.status == PENDING,
            Reservation.start_at >= now,
            or_(Reservation.held_until.is_(None), Reservation.held_until > now),
        ).order_by(Reservation.start_at, Reservation.id)
    )).scalars().all()
    labels = await table_labels(db, company_id, [r.id for r in rows])
    return [(r, labels.get(r.id, []), effective_status(r, now)) for r in rows]
