"""Request and response bodies for the public AI host chat (#259).

Responses carry no internal ids: the conversation token is the diner's only
handle, and a hold is described by its local date, time, party size and
status (never its reservation id)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from backend.config import settings
from backend.reservations.agent.core import Action, Expect, TurnOutput


class LocationInfoOut(BaseModel):
    location_name: str
    timezone: str
    greeting: str


class ConversationOut(BaseModel):
    conversation_token: str
    reply_text: str
    expect: Expect


class TurnIn(BaseModel):
    """Exactly one of free text or a button action."""

    text: str | None = Field(
        default=None, min_length=1,
        max_length=settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS)
    action: Action | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "TurnIn":
        if (self.text is None) == (self.action is None):
            raise ValueError("send exactly one of text or action")
        return self


class HoldOut(BaseModel):
    party_size: int
    local_date: str
    local_time: str
    start_iso: str
    status: str
    held_until: str | None = None
    confirmation_code: str | None = None


class HandoffOut(BaseModel):
    reason: str
    phone: str | None = None


class TurnOut(BaseModel):
    reply_text: str
    expect: Expect
    hold: HoldOut | None
    ended: bool
    handoff: HandoffOut | None

    @classmethod
    def from_turn(cls, out: TurnOutput) -> "TurnOut":
        hold: dict[str, Any] | None = out.hold
        handoff: dict[str, Any] | None = out.handoff
        return cls(
            reply_text=out.reply_text, expect=out.expect, ended=out.ended,
            # Whitelisted fields only: the core's hold_id is internal.
            hold=HoldOut.model_validate(
                {k: hold.get(k) for k in HoldOut.model_fields}) if hold else None,
            handoff=HandoffOut(reason=str(handoff.get("reason", "")),
                               phone=handoff.get("phone")) if handoff else None,
        )
