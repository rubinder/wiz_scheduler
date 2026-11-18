"""One diner's data at one location (#259): find it for an access request,
erase it on request. A diner is identified by phone (E.164).

The diner's data is every reservation at the location with that
`diner_phone` (all statuses) and every agent conversation linked to one of
them (booked, held or verified) or started from that caller number, plus
the agent's booking-lookup failures for that phone. Erasing clears the
reservations' contact fields (the rows stay for covers analytics), deletes
the conversations (their events cascade) and the lookup failures.

Erasing takes the location lock first, like every reservations write
(locations before reservations), so a concurrent hold or booking for the
same diner waits for it and cannot slip a row in between the snapshot and
the scrub. A conversation with a turn in flight (turn_started_at within
recording.TURN_STALE_SECONDS) is not deleted under that turn, which would
then fail writing its events: it is closed instead, so it takes no further
turns, and counted in `conversations_closed_in_flight`. Running the erase
again once the turn has ended deletes it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent.models import (
    AgentConversation,
    AgentEvent,
    ReservationLookupFailure,
)
from backend.reservations import booking
from backend.reservations.agent.recording import TURN_STALE_SECONDS
from backend.reservations.booking import E164, InvalidPhone
from backend.reservations.models import Reservation
from backend.reservations.queries import conversation_events


@dataclass(frozen=True)
class DinerData:
    reservations: list[Reservation]
    conversations: list[AgentConversation]
    events: dict[str, list[AgentEvent]]


def _require_phone(phone: str) -> None:
    """Raises booking.InvalidPhone unless `phone` is E.164."""
    if not E164.match(phone):
        raise InvalidPhone("phone must be E.164, e.g. +12125550100")


async def _reservation_ids(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> list[str]:
    return list((await db.execute(
        select(Reservation.id).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.diner_phone == phone,
        )
    )).scalars().all())


def _conversation_filter(
    company_id: str, location_id: str, phone: str, reservation_ids: list[str]
):
    linked = [AgentConversation.caller_number == phone]
    if reservation_ids:
        linked += [
            AgentConversation.reservation_id.in_(reservation_ids),
            AgentConversation.held_reservation_id.in_(reservation_ids),
            AgentConversation.verified_reservation_id.in_(reservation_ids),
        ]
    return (
        AgentConversation.company_id == company_id,
        AgentConversation.location_id == location_id,
        or_(*linked),
    )


async def find_diner_data(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> DinerData:
    _require_phone(phone)
    reservations = list((await db.execute(
        select(Reservation).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.diner_phone == phone,
        ).order_by(Reservation.start_at, Reservation.id)
    )).scalars().all())
    conversations = list((await db.execute(
        select(AgentConversation)
        .where(*_conversation_filter(
            company_id, location_id, phone, [r.id for r in reservations]))
        .order_by(AgentConversation.started_at, AgentConversation.id)
    )).scalars().all())
    events = await conversation_events(db, company_id, [c.id for c in conversations])
    return DinerData(reservations, conversations, events)


def _in_flight():
    """A turn is running: the same test recording.claim_turn uses, on the
    database clock."""
    return and_(
        AgentConversation.turn_started_at.isnot(None),
        AgentConversation.turn_started_at
        >= func.now() - text(f"interval '{TURN_STALE_SECONDS} seconds'"),
    )


async def erase_diner_data(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> dict[str, int]:
    """Erase one diner's data at one location. Flushes; the caller commits
    (which releases the location lock)."""
    _require_phone(phone)
    now = datetime.now(timezone.utc)
    # 1. Location lock first; an archived location still serializes.
    if not await booking.begin_location_write(db, company_id, location_id, now):
        if not await booking.lock_location_including_archived(
                db, company_id, location_id):
            raise booking.ReservationNotFound()
    # 2. Snapshot under the lock, before the reservations lose their phone.
    reservation_ids = await _reservation_ids(db, company_id, location_id, phone)
    matched = _conversation_filter(company_id, location_id, phone, reservation_ids)
    # 3. Delete the idle conversations. The in-flight test is in the DELETE
    #    itself, so a turn claimed while we wait on its row lock is re-checked
    #    and skipped rather than deleted under it.
    deleted = (await db.execute(
        delete(AgentConversation).where(*matched, not_(_in_flight()))
        .execution_options(synchronize_session=False)
    )).rowcount
    # 4. What is left of the match has a turn in flight: close it.
    closed = (await db.execute(
        update(AgentConversation).where(*matched)
        .values(status="closed",
                ended_at=func.coalesce(AgentConversation.ended_at, func.now()))
        .execution_options(synchronize_session=False)
    )).rowcount
    # 5. Scrub the snapshot, all statuses.
    scrubbed = 0
    if reservation_ids:
        scrubbed = (await db.execute(
            update(Reservation)
            .where(Reservation.company_id == company_id,
                   Reservation.id.in_(reservation_ids))
            .values(diner_name=None, diner_phone=None, diner_email=None,
                    notes=None)
            .execution_options(synchronize_session=False)
        )).rowcount
    lookups = (await db.execute(
        delete(ReservationLookupFailure).where(
            ReservationLookupFailure.company_id == company_id,
            ReservationLookupFailure.location_id == location_id,
            ReservationLookupFailure.phone == phone,
        )
    )).rowcount
    await db.flush()
    return {
        "reservations_scrubbed": scrubbed,
        "conversations_deleted": deleted,
        "conversations_closed_in_flight": closed,
        "lookup_failures_deleted": lookups,
    }
