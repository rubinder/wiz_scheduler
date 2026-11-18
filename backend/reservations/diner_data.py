"""One diner's data at one location (#259): find it for an access request,
erase it on request. A diner is identified by phone (E.164).

The diner's data is every reservation at the location with that
`diner_phone` (all statuses) and every agent conversation linked to one of
them (booked, held or verified) or started from that caller number, plus
the agent's booking-lookup failures for that phone. Erasing clears the
reservations' contact fields (the rows stay for covers analytics), deletes
the conversations (their events cascade) and the lookup failures.
"""

from dataclasses import dataclass

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent.models import (
    AgentConversation,
    AgentEvent,
    ReservationLookupFailure,
)
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


async def erase_diner_data(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> dict[str, int]:
    """Erase one diner's data at one location. Flushes; the caller commits.
    Conversations are found before the reservations lose their phone."""
    _require_phone(phone)
    reservation_ids = await _reservation_ids(db, company_id, location_id, phone)
    conversations = await db.execute(
        delete(AgentConversation).where(
            *_conversation_filter(company_id, location_id, phone, reservation_ids))
    )
    scrubbed = 0
    if reservation_ids:
        scrubbed = (await db.execute(
            update(Reservation)
            .where(Reservation.company_id == company_id,
                   Reservation.id.in_(reservation_ids))
            .values(diner_name=None, diner_phone=None, diner_email=None,
                    notes=None)
        )).rowcount
    lookups = await db.execute(
        delete(ReservationLookupFailure).where(
            ReservationLookupFailure.company_id == company_id,
            ReservationLookupFailure.location_id == location_id,
            ReservationLookupFailure.phone == phone,
        )
    )
    await db.flush()
    return {
        "reservations_scrubbed": scrubbed,
        "conversations_deleted": conversations.rowcount,
        "lookup_failures_deleted": lookups.rowcount,
    }
