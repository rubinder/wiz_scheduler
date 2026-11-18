"""Request and response bodies for the voice bridge (#259 step 6): what the
Amazon Connect Lambda sends for each phone turn and what it speaks back.

Like the web chat's bodies, nothing internal goes out: no conversation,
reservation or location id."""

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from backend.config import settings
from backend.reservations.agent.core import Action, Expect

E164 = Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{1,14}$")]
Elicit = Literal["party_size", "phone_number", "confirmation_code", "date", "time"]


class VoiceTimings(BaseModel):
    """What the Lambda measured for this turn: speech recognition of the
    caller's words, speech synthesis of the previous reply, and transport."""

    asr_ms: int | None = Field(default=None, ge=0, le=600_000)
    tts_ms: int | None = Field(default=None, ge=0, le=600_000)
    # Network time between the Lambda and this endpoint.
    transport_ms: int | None = Field(default=None, ge=0, le=600_000)


class VoiceTurnIn(BaseModel):
    """One phone turn. Text, a confirmation action, or neither (the call's
    opening turn, or no input); never both."""

    # The Connect contact id: one conversation per call.
    contact_id: Annotated[str, StringConstraints(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
    dialed: E164
    caller: E164 | None = None  # withheld numbers arrive as null
    seq: int = Field(ge=0)
    text: Annotated[str, StringConstraints(
        strip_whitespace=True,
        max_length=settings.RESERVATION_AGENT_MAX_MESSAGE_CHARS)] | None = None
    action: Action | None = None
    timings: VoiceTimings = Field(default_factory=VoiceTimings)

    @field_validator("text")
    @classmethod
    def _clean_text(cls, v: str | None) -> str | None:
        if v is not None and any(
                unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in v):
            raise ValueError("text contains control characters")
        return v or None  # silence is no input

    @model_validator(mode="after")
    def _not_both(self) -> "VoiceTurnIn":
        if self.text is not None and self.action is not None:
            raise ValueError("send text or an action, not both")
        return self


class VoiceHints(BaseModel):
    """How the Lambda should listen next: a Lex slot to elicit, and whether
    the keypad is accepted."""

    elicit: Elicit | None = None
    dtmf: bool = False


class VoiceTurnOut(BaseModel):
    text: str  # plain text; the Lambda renders SSML
    expect: Expect
    hints: VoiceHints
    # A short spoken yes/no question when `expect` is a confirm_* action;
    # Lex's confirmation maps the answer to that action.
    confirm_prompt: str | None = None
    end_call: bool
    transfer_to: str | None = None  # E.164, the location's staff number
    reason: str | None = None


class VoiceScreenIn(BaseModel):
    """Asked before a call is answered (#259 step 7). The caller is taken as
    Connect sends it: "anonymous", empty or any non-E.164 value is a
    withheld number and is answered."""

    dialed: E164
    caller: Annotated[str, StringConstraints(max_length=64)] | None = None


class VoiceScreenOut(BaseModel):
    action: Literal["answer", "block"]
    reason: Literal["allow_list", "block_list", "ftc", "default", "withheld"]
