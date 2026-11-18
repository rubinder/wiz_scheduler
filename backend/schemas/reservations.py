"""Request and response bodies for the manager reservations API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.models import Reservation
from backend.reservations.rules import RulesSchema


class RulesOut(BaseModel):
    rules: RulesSchema
    version: int


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    table_ids: list[str]


class DinerIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None  # staff bookings may omit it (decision 17)
    email: str | None = None


class BookingIn(BaseModel):
    party_size: int = Field(ge=1)
    start: AwareDatetime
    diner: DinerIn
    notes: str | None = None


class ReservationPatch(BaseModel):
    party_size: int | None = Field(default=None, ge=1)
    start: AwareDatetime | None = None
    notes: str | None = None


class WalkInIn(BaseModel):
    party_size: int = Field(ge=1)
    name: str | None = None
    phone: str | None = None
    notes: str | None = None


class StatusIn(BaseModel):
    status: Literal["seated", "completed", "no_show", "cancelled"]


class ReservationOut(BaseModel):
    id: str
    location_id: str
    party_size: int
    start: datetime
    end: datetime
    status: str
    tables: list[str]
    diner_name: str | None
    diner_phone: str | None
    diner_email: str | None
    notes: str | None
    source: str
    confirmation_code: str | None
    held_until: datetime | None

    @classmethod
    def of(cls, res: Reservation, tables: list[str],
           status: str | None = None) -> "ReservationOut":
        """`status` overrides the stored one (a lapsed hold reads `expired`)."""
        return cls(
            id=res.id, location_id=res.location_id, party_size=res.party_size,
            start=res.start_at, end=res.end_at, status=status or res.status,
            tables=tables,
            diner_name=res.diner_name, diner_phone=res.diner_phone,
            diner_email=res.diner_email, notes=res.notes, source=res.source,
            confirmation_code=res.confirmation_code, held_until=res.held_until,
        )


class ConversationOut(BaseModel):
    """One agent conversation in the manager list. Never carries source_key
    or public_token. cost_usd is a decimal string (6 places)."""
    id: str
    channel: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    turn_count: int
    cost_usd: str
    reservation_id: str | None

    @classmethod
    def of(cls, c: AgentConversation) -> "ConversationOut":
        return cls(
            id=c.id, channel=c.channel, started_at=c.started_at,
            ended_at=c.ended_at, outcome=c.outcome, turn_count=c.turn_count,
            cost_usd=str(c.cost_usd), reservation_id=c.reservation_id,
        )


class AgentEventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]
    latency_ms: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: str
    created_at: datetime

    @classmethod
    def of(cls, e: AgentEvent) -> "AgentEventOut":
        return cls(
            seq=e.seq, type=e.type, payload=e.payload, latency_ms=e.latency_ms,
            input_tokens=e.input_tokens, output_tokens=e.output_tokens,
            cache_read_tokens=e.cache_read_tokens,
            cache_write_tokens=e.cache_write_tokens,
            cost_usd=str(e.cost_usd), created_at=e.created_at,
        )


class ConversationDetailOut(ConversationOut):
    location_id: str
    status: str
    events: list[AgentEventOut]

    @classmethod
    def with_events(cls, c: AgentConversation,
                    events: list[AgentEvent]) -> "ConversationDetailOut":
        return cls(
            **ConversationOut.of(c).model_dump(), location_id=c.location_id,
            status=c.status, events=[AgentEventOut.of(e) for e in events],
        )
