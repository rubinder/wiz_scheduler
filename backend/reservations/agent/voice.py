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
RATE_LIMITED_TEXT = ("Sorry, we're having trouble with this call. Please call back in "
                     "a minute. Goodbye.")
NOT_CAUGHT_TEXT = "Sorry, I didn't catch that — could you say it again?"
# Which question each confirmation action answers. decline_hold is the "no"
# to any of them (the core's decline drops a hold, a staged change or a
# staged cancel alike), so it is valid under every confirm_* expectation.
_ACTION_EXPECTS = {"confirm_hold": ("confirm_hold",),
                   "confirm_cancel": ("confirm_cancel",),
                   "confirm_modify": ("confirm_modify",),
                   "decline_hold": ("confirm_hold", "confirm_cancel",
                                    "confirm_modify")}
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


def _ask(question: str, statement: str, style: str) -> str:
    """`question` is the default wording; "statement" is the same offer as a
    sentence ending in a yes/no instruction."""
    if style == "statement":
        return f"{statement} \u2014 say yes to confirm or no to change it."
    return question


def confirm_prompt(out: TurnOutput, style: str = "question") -> str | None:
    """The yes/no prompt for a confirm_* expectation, from the structured
    hold or proposal. None when there is nothing structured to ask about."""
    try:
        if out.expect == "confirm_hold" and out.hold:
            verb = "request" if out.hold.get("status") == "pending_approval" else "book"
            return _ask(f"Shall I {verb} {_slot(out.hold)}?",
                        f"I can {verb} {_slot(out.hold)}", style)
        proposal = out.proposal or {}
        if out.expect == "confirm_modify" and proposal.get("proposed"):
            slot = _slot(proposal["proposed"])
            return _ask(f"Shall I change your booking to {slot}?",
                        f"I can change your booking to {slot}", style)
        if out.expect == "confirm_cancel" and proposal.get("current"):
            when = _when(proposal["current"])
            return _ask(f"Shall I cancel your booking for {when}?",
                        f"I can cancel your booking for {when}", style)
    except (KeyError, TypeError, ValueError):
        return None
    return None


def action_matches(action: str, last_reply: dict[str, Any] | None) -> bool:
    """A confirmation action answers the question the caller was last asked
    (the stored reply's `expect`), or it is refused."""
    expect = (last_reply or {}).get("expect")
    return expect in _ACTION_EXPECTS.get(action, ())


def not_caught(last_reply: dict[str, Any] | None) -> dict[str, Any]:
    """An action that answers no open question: ask again, with the last
    question (expect, hints, confirm prompt) still up."""
    last = last_reply or {}
    return {"text": NOT_CAUGHT_TEXT, "expect": last.get("expect", "text"),
            "hints": last.get("hints") or {"elicit": None, "dtmf": False},
            "confirm_prompt": last.get("confirm_prompt"),
            "end_call": False, "transfer_to": None, "reason": "action_mismatch"}


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


def spoken_reply(out: TurnOutput, confirm_style: str = "question") -> dict[str, Any]:
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
            "confirm_prompt": confirm_prompt(out, confirm_style),
            "end_call": out.ended, "transfer_to": None,
            "reason": "ended" if out.ended else None}
