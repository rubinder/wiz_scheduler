"""Booking providers (#259 step 9): the AI host books through a
`BookingProvider` (base.py). A conversation's provider is fixed when it
starts (`agent_conversations.provider`, from the location's rules), so
switching provider never moves a conversation or its bookings."""

from backend.reservations.agent.models import DEFAULT_PROVIDER, AgentConversation
from backend.reservations.providers.base import (
    BOOKED,
    CANCELLED,
    HELD,
    PENDING,
    BookingProvider,
    HoldExpired,
    InvalidTransition,
    NotFound,
    PartyTooLarge,
    PhoneRequired,
    ProviderBooking,
    ProviderContext,
    ProviderError,
    ProviderSlot,
    SlotUnavailable,
    Transient,
)
from backend.reservations.providers.mock_external import MockExternalProvider
from backend.reservations.providers.wizscheduler import WizSchedulerProvider

__all__ = [
    "BOOKED", "CANCELLED", "HELD", "PENDING",
    "BookingProvider", "HoldExpired", "InvalidTransition", "NotFound",
    "PartyTooLarge", "PhoneRequired", "ProviderBooking", "ProviderContext",
    "ProviderError", "ProviderSlot", "SlotUnavailable", "Transient",
    "provider_for", "provider_named",
]

_PROVIDERS: dict[str, BookingProvider] = {
    "wizscheduler": WizSchedulerProvider(),
    "mock_external": MockExternalProvider(),
}


def provider_named(name: str) -> BookingProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown booking provider: {name!r}") from None


def provider_for(conv: AgentConversation) -> BookingProvider:
    """The provider the conversation started with. An unflushed row has no
    provider yet: it gets the default, as the column would."""
    return provider_named(conv.provider or DEFAULT_PROVIDER)
