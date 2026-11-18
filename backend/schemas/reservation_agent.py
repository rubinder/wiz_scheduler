"""Request and response bodies for the public AI host chat (#259).

Responses carry no internal ids: the conversation token is the diner's only
handle, and a hold is described by its local date, time, party size and
status (never its reservation id)."""

import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel, SerializerFunctionWrapHandler, StringConstraints, field_validator,
    model_serializer, model_validator,
)

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
    # True when the conversation was closed at its start (the location's
    # spend cap): the widget disables input.
    ended: bool = False


class TurnIn(BaseModel):
    """Exactly one of free text or a button action."""

    # Stripped before the length check, so whitespace-only text is a 422.
    text: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1,
        max_length=settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS)] | None = None
    action: Action | None = None

    @field_validator("text")
    @classmethod
    def _no_control_chars(cls, v: str | None) -> str | None:
        """Line breaks and tabs are text; any other control character
        (NUL included) is refused."""
        if v is not None and any(
                unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in v):
            raise ValueError("text contains control characters")
        return v

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
    confirmation_code: str | None = None  # only once booked

    @model_serializer(mode="wrap")
    def _drop_empty_code(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if data.get("confirmation_code") is None:
            data.pop("confirmation_code", None)
        return data


class HandoffOut(BaseModel):
    reason: str
    phone: str | None = None


class SlotSummaryOut(BaseModel):
    """A booking's date, time (location-local) and party size."""
    local_date: str
    local_time: str
    party_size: int


class ProposalOut(BaseModel):
    """What the confirm_modify / confirm_cancel button applies, built from the
    verified booking and the staged request (never from the model's text)."""
    kind: Literal["modify", "cancel"]
    current: SlotSummaryOut
    proposed: SlotSummaryOut | None  # None for a cancel


class TurnOut(BaseModel):
    reply_text: str
    expect: Expect
    hold: HoldOut | None
    ended: bool
    handoff: HandoffOut | None
    proposal: ProposalOut | None = None

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
            proposal=ProposalOut.model_validate(out.proposal) if out.proposal else None,
        )
