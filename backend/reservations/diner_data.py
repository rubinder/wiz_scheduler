"""One diner's data at one location (#259): find it for an access request,
erase it on request. A diner is identified by phone (E.164).

The diner's data is every reservation at the location with that
`diner_phone` (all statuses) and every agent conversation linked to one of
them (booked, held or verified, or named by the reservation's own
`conversation_id`, which survives a declined, expired or replaced hold) or
started from that caller number, plus
the agent's booking-lookup failures for that phone. Erasing clears the
reservations' contact fields (the rows stay for covers analytics), deletes
the conversations (their events cascade) and the lookup failures.

Bookings the AI host made in the mock external system (#259 step 9) are
diner data too: that location's `mock_external_bookings` with that phone.
Export lists them (labelled external); erase deletes them, with the stored
idempotent responses that repeat them. A conversation's booking refs belong
to its provider (`agent_conversations.provider`), so a WizScheduler
reservation id links only WizScheduler conversations and a mock booking id
only mock ones: a ref is never matched against the other provider's ids.

Erasure by confirmation code (`erase_by_code`) is the narrower request: one
reservation, found by its code at the location in WizScheduler's or the
mock's inventory, with the same scrub. A code both inventories hold here is
refused (`AmbiguousCode`, 409): the manager erases by phone instead. While a
linked conversation is still mid-turn, the mock booking is scrubbed but kept
(its id and code), so erasing again by the same code still finds it and its
conversation; it is deleted once none is in flight. It clears that
reservation's contact fields (or deletes that mock booking) and deletes the
conversations linked to it: by the provider-scoped refs, matched against
conv.provider's inventory only, and by the reservation's own
`conversation_id`. It does not touch the diner's other reservations, the
conversations matched only by caller number, the lookup failures or the
number-list entries: those belong to the phone number, and phone erasure
removes them. Codes are matched as the agent's lookup matches them
(surrounding whitespace dropped, upper-cased).

Number-list entries for that number (#259 step 7) are diner data too. The
request is per location and so is the manager's scope: export lists this
location's entries and the company-wide ones (location_id NULL), and erase
deletes this location's entries only. Company-wide entries are never erased
here (only an admin may change them, from the number lists page); erase
reports how many remain as `company_rules_remaining`. Other locations'
entries are neither listed nor erased. screened_calls keep only a keyed
hash of the caller and are not looked up by number.

Erasing takes the location lock first, like every reservations write
(locations before reservations), so a concurrent hold or booking for the
same diner waits for it and cannot slip a row in between the snapshot and
the scrub. A conversation with a turn in flight (turn_started_at within
recording.TURN_STALE_SECONDS) is not deleted under that turn, which would
then fail writing its events: it is closed instead, so it takes no further
turns, its stored voice reply (`last_reply`, which can repeat the diner's
details) is cleared and `last_seq` set to recording.ERASED_SEQ, so the
running turn neither stores its reply nor records its words (it still
records its cost), and it is counted in `conversations_closed_in_flight`. Running the erase
again once the turn has ended deletes it.

Lock order: a running turn locks its conversation row (record_event) and
then, in a tool, waits for the location lock. The erase holds the location
lock, so it must never wait on a conversation row: it locks the rows it
deletes or closes with FOR UPDATE SKIP LOCKED, and a row a turn holds is
left as it is and counted in `conversations_still_in_flight` (erase again
later).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, false, func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent.models import (
    AgentConversation,
    AgentEvent,
    ReservationLookupFailure,
)
from backend.reservations import booking, number_rules
from backend.reservations.agent.recording import ERASED_SEQ, TURN_STALE_SECONDS
from backend.reservations.booking import E164, InvalidPhone
from backend.reservations.models import Reservation
from backend.reservations.providers import mock_external
from backend.reservations.providers.base import ProviderBooking
from backend.reservations.queries import conversation_events
from backend.reservations.screening_models import NumberRule


class AmbiguousCode(booking.ReservationError):
    """A WizScheduler reservation and a mock booking here share the code."""
    code = "ambiguous_code"

    def __init__(self) -> None:
        super().__init__("A reservation and an external booking here share this code: "
                         "erase by the diner's phone number instead.")


@dataclass(frozen=True)
class DinerData:
    reservations: list[Reservation]
    conversations: list[AgentConversation]
    events: dict[str, list[AgentEvent]]
    number_rules: list[number_rules.RuleView]
    external_bookings: list[ProviderBooking]


def _require_phone(phone: str) -> None:
    """Raises booking.InvalidPhone unless `phone` is E.164."""
    if not E164.match(phone):
        raise InvalidPhone("phone must be E.164, e.g. +12125550100")


async def _reservations(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> tuple[list[str], list[str]]:
    """The diner's reservation ids here, and the conversations they came from."""
    rows = (await db.execute(
        select(Reservation.id, Reservation.conversation_id).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.diner_phone == phone,
        )
    )).all()
    return ([r.id for r in rows],
            sorted({r.conversation_id for r in rows if r.conversation_id}))


def _refs_in(ids: list[str]):
    return or_(
        AgentConversation.reservation_id.in_(ids),
        AgentConversation.held_reservation_id.in_(ids),
        AgentConversation.verified_reservation_id.in_(ids),
        AgentConversation.pending_cancel_id.in_(ids),
    )


def _conversation_filter(
    company_id: str, location_id: str, phone: str | None, reservation_ids: list[str],
    conversation_ids: list[str], mock_ids: list[str],
):
    """A conversation's refs are matched against its own provider's ids only.
    `phone` None (erase by code) matches no conversation by caller number."""
    linked = [AgentConversation.caller_number == phone] if phone is not None else []
    if reservation_ids:
        linked.append(and_(AgentConversation.provider == "wizscheduler",
                           _refs_in(reservation_ids)))
    if mock_ids:
        linked.append(and_(AgentConversation.provider == mock_external.NAME,
                           _refs_in(mock_ids)))
    if conversation_ids:
        linked.append(AgentConversation.id.in_(conversation_ids))
    if not linked:
        linked.append(false())
    return (
        AgentConversation.company_id == company_id,
        AgentConversation.location_id == location_id,
        or_(*linked),
    )


async def find_diner_data(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> DinerData:
    _require_phone(phone)
    now = datetime.now(timezone.utc)
    external = await mock_external.bookings_for_phone(
        db, company_id, location_id, phone, now)
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
            company_id, location_id, phone, [r.id for r in reservations],
            sorted({r.conversation_id for r in reservations if r.conversation_id}),
            [b.ref for b in external]))
        .order_by(AgentConversation.started_at, AgentConversation.id)
    )).scalars().all())
    events = await conversation_events(db, company_id, [c.id for c in conversations])
    rules = list((await db.execute(
        select(NumberRule).where(
            NumberRule.company_id == company_id, NumberRule.e164 == phone,
            or_(NumberRule.location_id == location_id, NumberRule.location_id.is_(None)))
        .order_by(NumberRule.created_at, NumberRule.id)
    )).scalars().all())
    views = await number_rules.views(db, company_id, rules, now)
    return DinerData(reservations, conversations, events, views, external)


def _in_flight():
    """A turn is running: the same test recording.claim_turn uses, on the
    database clock."""
    return and_(
        AgentConversation.turn_started_at.isnot(None),
        AgentConversation.turn_started_at
        >= func.now() - text(f"interval '{TURN_STALE_SECONDS} seconds'"),
    )


async def _lock(db: AsyncSession, company_id: str, location_id: str,
                now: datetime) -> None:
    """The location lock first; an archived location still serializes. Then
    the mock's own location lock, so no mock hold for the diner slips in
    either (a mock turn takes it after its conversation row, and the erase
    never waits on a conversation row)."""
    if not await booking.begin_location_write(db, company_id, location_id, now):
        if not await booking.lock_location_including_archived(
                db, company_id, location_id):
            raise booking.ReservationNotFound()
    await mock_external.lock_location(db, location_id)


async def _erase(
    db: AsyncSession, company_id: str, location_id: str, phone: str | None,
    reservation_ids: list[str], conversation_ids: list[str], mock_ids: list[str],
    *, keep_mock_in_flight: bool = False,
) -> dict[str, int]:
    """The scrub both requests share, over a snapshot taken under the locks:
    the conversations matched (deleted, or closed mid-turn), the
    reservations' contact fields and the mock bookings. With
    `keep_mock_in_flight` (erase by code), a conversation left in flight
    keeps the mock bookings as scrubbed rows rather than deleting them, so a
    retry by the same code finds them."""
    matched = _conversation_filter(company_id, location_id, phone, reservation_ids,
                                   conversation_ids, mock_ids)
    total = (await db.execute(
        select(func.count()).select_from(AgentConversation).where(*matched)
    )).scalar_one()
    # Delete the idle conversations. Rows are locked SKIP LOCKED (never
    # waited on, see the lock order above) and the in-flight test runs once
    # the lock is held, so a turn claimed meanwhile is skipped rather than
    # deleted under it.
    idle = list((await db.execute(
        select(AgentConversation.id).where(*matched, not_(_in_flight()))
        .with_for_update(skip_locked=True)
    )).scalars().all())
    deleted = 0
    if idle:
        deleted = (await db.execute(
            delete(AgentConversation).where(AgentConversation.company_id == company_id,
                                            AgentConversation.id.in_(idle))
            .execution_options(synchronize_session=False)
        )).rowcount
    # What is left of the match has a turn in flight: close what is not
    # locked; a row a turn holds is skipped and counted as still in flight.
    busy = list((await db.execute(
        select(AgentConversation.id).where(*matched)
        .with_for_update(skip_locked=True)
    )).scalars().all())
    closed = 0
    if busy:
        closed = (await db.execute(
            update(AgentConversation)
            .where(AgentConversation.company_id == company_id,
                   AgentConversation.id.in_(busy))
            .values(status="closed", last_reply=None, last_seq=ERASED_SEQ,
                    ended_at=func.coalesce(AgentConversation.ended_at, func.now()))
            .execution_options(synchronize_session=False)
        )).rowcount
    # Scrub the snapshot, all statuses.
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
    if keep_mock_in_flight and total > deleted:
        await mock_external.scrub_bookings(db, company_id, mock_ids)
        external_deleted = 0
    else:
        external_deleted = await mock_external.erase_bookings(db, company_id, mock_ids)
    return {
        "reservations_scrubbed": scrubbed,
        "external_bookings_deleted": external_deleted,
        "conversations_deleted": deleted,
        "conversations_closed_in_flight": closed,
        "conversations_still_in_flight": max(0, total - deleted - closed),
    }


async def erase_diner_data(
    db: AsyncSession, company_id: str, location_id: str, phone: str
) -> dict[str, int]:
    """Erase one diner's data at one location. Flushes; the caller commits
    (which releases the location lock)."""
    _require_phone(phone)
    now = datetime.now(timezone.utc)
    await _lock(db, company_id, location_id, now)
    # Snapshot under the locks, before the reservations lose their phone.
    reservation_ids, conversation_ids = await _reservations(
        db, company_id, location_id, phone)
    mock_ids = [b.ref for b in await mock_external.bookings_for_phone(
        db, company_id, location_id, phone, now)]
    counts = await _erase(db, company_id, location_id, phone, reservation_ids,
                          conversation_ids, mock_ids)
    lookups = (await db.execute(
        delete(ReservationLookupFailure).where(
            ReservationLookupFailure.company_id == company_id,
            ReservationLookupFailure.location_id == location_id,
            ReservationLookupFailure.phone == phone,
        )
    )).rowcount
    rules = (await db.execute(
        delete(NumberRule).where(NumberRule.company_id == company_id,
                                 NumberRule.location_id == location_id,
                                 NumberRule.e164 == phone)
    )).rowcount
    company_rules = (await db.execute(
        select(func.count()).select_from(NumberRule).where(
            NumberRule.company_id == company_id, NumberRule.location_id.is_(None),
            NumberRule.e164 == phone)
    )).scalar_one()
    await db.flush()
    return {
        **counts,
        "lookup_failures_deleted": lookups,
        "number_rules_deleted": rules,
        "company_rules_remaining": company_rules,
    }


async def erase_by_code(
    db: AsyncSession, company_id: str, location_id: str, code: str
) -> dict[str, int]:
    """Erase the one reservation with this confirmation code at this
    location, and its conversations. Raises booking.ReservationNotFound when
    no reservation or mock booking here has the code, AmbiguousCode when
    both do. Flushes; the caller commits (which releases the location lock)."""
    code = booking.normalize_code(code)
    now = datetime.now(timezone.utc)
    await _lock(db, company_id, location_id, now)
    rows = (await db.execute(
        select(Reservation.id, Reservation.conversation_id).where(
            Reservation.company_id == company_id,
            Reservation.location_id == location_id,
            Reservation.confirmation_code == code,
        )
    )).all()
    mock_ids = await mock_external.refs_for_code(db, company_id, location_id, code)
    if not rows and not mock_ids:
        raise booking.ReservationNotFound()
    if rows and mock_ids:
        raise AmbiguousCode()
    counts = await _erase(
        db, company_id, location_id, None, [r.id for r in rows],
        sorted({r.conversation_id for r in rows if r.conversation_id}), mock_ids,
        keep_mock_in_flight=True)
    await db.flush()
    return counts
