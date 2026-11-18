"""The voice channel's side of a turn (#259 step 6): what the caller hears.

`spoken_reply` turns the core's `TurnOutput` into the bridge's answer (the
shape of schemas.voice.VoiceTurnOut, as a dict, so it can be stored for a
retried turn): a handoff becomes a transfer to the location's staff number
(or a closing line when there is none), and a confirm_* expectation gets a
yes/no question built from the structured hold or proposal, never from the
model's text. Times are already the location's local time there."""

from datetime import date
from typing import Any

from backend.reservations.agent.core import TurnOutput

UNAVAILABLE_TEXT = "Sorry, this line isn't taking bookings right now. Goodbye."
INFLIGHT_TEXT = "One moment — could you say that again?"
ENDED_TEXT = "Thanks for calling. Goodbye."
NO_TRANSFER_TEXT = ("Sorry, there's no one available to take your call right now. "
                    "Please call back a little later. Goodbye.")
BUSY_TEXT = "Sorry, our booking line is busy right now. Please call back a little later."

_ELICIT = ("party_size", "phone_number", "confirmation_code")
# Digits only: confirmation codes have letters, so they are spoken.
_DTMF = ("party_size", "phone_number")


def busy_text(staff_number: str | None) -> str:
    """The spend cap's close, as the web's busy text but for a caller."""
    if staff_number:
        return (f"Sorry, our booking line is busy right now. Please call the "
                f"restaurant at {staff_number}. Goodbye.")
    return f"{BUSY_TEXT} Goodbye."


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def spoken_date(local_date: str) -> str:
    """"2026-11-21" -> "Friday, November 21st"."""
    d = date.fromisoformat(local_date)
    return f"{d:%A, %B} {_ordinal(d.day)}"


def spoken_time(local_time: str) -> str:
    """"19:00" -> "7 pm", "19:30" -> "7:30 pm", "12:00" -> "12 pm"."""
    hh, mm = (int(p) for p in local_time.split(":")[:2])
    hour = hh % 12 or 12
    meridiem = "am" if hh < 12 else "pm"
    return f"{hour} {meridiem}" if mm == 0 else f"{hour}:{mm:02d} {meridiem}"


def _when(slot: dict[str, Any]) -> str:
    return (f"{slot['party_size']} on {spoken_date(slot['local_date'])} "
            f"at {spoken_time(slot['local_time'])}")


def _slot(slot: dict[str, Any]) -> str:
    return f"a table for {_when(slot)}"


def confirm_prompt(out: TurnOutput) -> str | None:
    """The yes/no question for a confirm_* expectation, from the structured
    hold or proposal. None when there is nothing structured to ask about."""
    try:
        if out.expect == "confirm_hold" and out.hold:
            verb = "request" if out.hold.get("status") == "pending_approval" else "book"
            return f"Shall I {verb} {_slot(out.hold)}?"
        proposal = out.proposal or {}
        if out.expect == "confirm_modify" and proposal.get("proposed"):
            return (f"Shall I change your booking to "
                    f"{_slot(proposal['proposed'])}?")
        if out.expect == "confirm_cancel" and proposal.get("current"):
            return f"Shall I cancel your booking for {_when(proposal['current'])}?"
    except (KeyError, TypeError, ValueError):
        return None
    return None


def close(text: str, reason: str) -> dict[str, Any]:
    """A reply that ends the call, with no model call behind it."""
    return {"text": text, "expect": "text",
            "hints": {"elicit": None, "dtmf": False}, "confirm_prompt": None,
            "end_call": True, "transfer_to": None, "reason": reason}


def inflight() -> dict[str, Any]:
    """Another turn of this call (or too many at the location) is running:
    the caller repeats; the call stays up."""
    return {"text": INFLIGHT_TEXT, "expect": "text",
            "hints": {"elicit": None, "dtmf": False}, "confirm_prompt": None,
            "end_call": False, "transfer_to": None, "reason": "in_flight"}


def greeting(text: str) -> dict[str, Any]:
    return {"text": text, "expect": "text",
            "hints": {"elicit": None, "dtmf": False}, "confirm_prompt": None,
            "end_call": False, "transfer_to": None, "reason": None}


def spoken_reply(out: TurnOutput) -> dict[str, Any]:
    """The bridge's answer to one core turn."""
    if out.handoff is not None:
        reason = str(out.handoff.get("reason") or "handoff")
        phone = out.handoff.get("phone")
        if phone:
            return {"text": out.reply_text, "expect": "text",
                    "hints": {"elicit": None, "dtmf": False}, "confirm_prompt": None,
                    "end_call": False, "transfer_to": phone, "reason": reason}
        return close(NO_TRANSFER_TEXT, reason)
    expect = out.expect
    return {"text": out.reply_text, "expect": expect,
            "hints": {"elicit": expect if expect in _ELICIT else None,
                      "dtmf": expect in _DTMF},
            "confirm_prompt": confirm_prompt(out),
            "end_call": out.ended, "transfer_to": None,
            "reason": "ended" if out.ended else None}
