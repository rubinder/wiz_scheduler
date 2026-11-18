"""Request and response bodies for the manager reservations API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

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
    phone: str
    email: str | None = None


class BookingIn(BaseModel):
    party_size: int = Field(ge=1)
    start: datetime
    diner: DinerIn
    notes: str | None = None


class ReservationPatch(BaseModel):
    party_size: int | None = Field(default=None, ge=1)
    start: datetime | None = None
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
    def of(cls, res: Reservation, tables: list[str]) -> "ReservationOut":
        return cls(
            id=res.id, location_id=res.location_id, party_size=res.party_size,
            start=res.start_at, end=res.end_at, status=res.status, tables=tables,
            diner_name=res.diner_name, diner_phone=res.diner_phone,
            diner_email=res.diner_email, notes=res.notes, source=res.source,
            confirmation_code=res.confirmation_code, held_until=res.held_until,
        )
