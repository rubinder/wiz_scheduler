"""The booking provider protocol (#259 step 9): what the AI host books
through, so the same agent can book WizScheduler's own tables or an external
reservation system.

The protocol is the agent's booking operations only. Staff and manager
routes are WizScheduler-native and call `booking.*` directly.

Values are provider-neutral dataclasses. A `ref` is the provider's own id
for a booking; for WizScheduler it is the reservation id, so the refs kept
on a conversation (`held_reservation_id`, `pending_cancel_id`, ...) stay
plain ids.

Errors: providers raise `ProviderError` subclasses, each with a `.code`
the agent returns to the model. They are WizScheduler's booking errors,
re-exported, so the agent's error replies are the same for every provider.
`Transient` is a retryable failure (an external system that did not answer).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.booking import (
    HoldExpired,
    InvalidTransition,
    PartyTooLarge,
    PhoneRequired,
    ReservationError,
    SlotUnavailable,
)
from backend.reservations.booking import ReservationNotFound as NotFound
from backend.reservations.models import BOOKED, CANCELLED, HELD, PENDING
from backend.reservations.rules import RulesSchema

ProviderError = ReservationError

__all__ = [
    "BOOKED", "CANCELLED", "HELD", "PENDING",
    "BookingProvider", "HoldExpired", "InvalidTransition", "NotFound",
    "PartyTooLarge", "PhoneRequired", "ProviderBooking", "ProviderContext",
    "ProviderError", "ProviderSlot", "SlotUnavailable", "Transient",
]


class Transient(ProviderError):
    """A retryable failure: the provider could not be reached or timed out."""

    code = "provider_unavailable"


@dataclass(frozen=True)
class ProviderContext:
    """Who is booking where, and when. `timezone` is the location's zone
    key; `rules` are its reservation rules; `now` is UTC."""

    company_id: str
    location_id: str
    timezone: str
    rules: RulesSchema
    now: datetime


@dataclass(frozen=True)
class ProviderSlot:
    start: datetime
    end: datetime
    party_size: int
    ref: str | None  # the provider's id for the slot, if it has one


@dataclass(frozen=True)
class ProviderBooking:
    """One booking. `status` uses the reservation vocabulary (`held`,
    `pending`, `booked`, `cancelled`, ...); `code` is None until the diner
    confirms. `held_until` is when an unconfirmed hold lapses."""

    ref: str
    code: str | None
    start: datetime
    end: datetime
    party_size: int
    status: str
    diner_name: str | None
    diner_phone: str | None
    external: bool
    held_until: datetime | None = None


class BookingProvider(Protocol):
    """The six agent booking verbs. Each flushes only; callers commit."""

    name: str

    async def find_slots(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        window_start: datetime, window_end: datetime, *,
        exclude_ref: str | None = None,
    ) -> list[ProviderSlot]:
        """Bookable starts in [window_start, window_end), with the booking
        cutoff applied. `exclude_ref` is a booking being moved, which must
        not block its own new time."""
        ...

    async def hold(
        self, db: AsyncSession, ctx: ProviderContext, party_size: int,
        start: datetime, *, name: str, phone: str, channel: str,
        conversation_id: str,
    ) -> ProviderBooking:
        """Hold exactly `start` for the diner until they confirm."""
        ...

    async def book(self, db: AsyncSession, ctx: ProviderContext,
                   ref: str) -> ProviderBooking:
        """Confirm a hold (or submit a pending request)."""
        ...

    async def modify(
        self, db: AsyncSession, ctx: ProviderContext, ref: str, *,
        party_size: int | None, start: datetime | None,
    ) -> ProviderBooking:
        """Change party size and/or time; None leaves a field unchanged.
        Applies the diner's limits, never staff's."""
        ...

    async def cancel(self, db: AsyncSession, ctx: ProviderContext,
                     ref: str) -> ProviderBooking:
        """Cancel a hold, a pending request or a booking."""
        ...

    async def lookup(
        self, db: AsyncSession, ctx: ProviderContext, *,
        code: str | None = None, phone: str | None = None, ref: str | None = None,
    ) -> ProviderBooking | None:
        """By confirmation code and the exact phone it was made with (the
        diner's verification), or by `ref` (re-reading a booking the
        conversation already holds or verified). Read-only; None if absent."""
        ...
