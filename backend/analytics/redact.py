"""What may reach the analytics lake (#259 step 10).

The lake holds no diner names, phone numbers or emails. Name, email and
notes columns are dropped by extract; phone columns become the HMAC source
key there. This module covers the two places diner data can hide inside a
value: free text (user and assistant turns, action replies, handoff
reasons) and tool payloads.

- `text` masks phone-like digit runs, emails and confirmation codes. It
  cannot recognise a name typed into free text; nothing can, reliably.
- `payload` keeps an allowlist of keys and nothing else.
"""

import re
from typing import Any

# Same alphabet as backend/reservations/codes.py (no 0, 1, I, O). Kept as a
# literal rather than imported: analytics reads the reservations tables but
# depends on none of their code.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# Seven or more digits, with up to three spaces, dashes, dots or parentheses
# between any two of them, an optional leading + or (. An ISO date
# (2026-11-20, as in a start_iso the model quotes) is not a phone number.
_PHONE = re.compile(r"(?<![\d+(])(?!\d{4}-\d{2}-\d{2}(?!\d))\+?\(?\d(?:[\s.\-()]{0,3}\d){6,}\)?")
_CODE_CHARS = CODE_ALPHABET + CODE_ALPHABET.lower()
_CODE = re.compile(
    rf"(?<![A-Za-z0-9])(?=[{_CODE_CHARS}]*\d)[{_CODE_CHARS}]{{{CODE_LENGTH}}}(?![A-Za-z0-9])")

# Tool payload keys allowed into the lake. `name` is the tool's name and is
# only ever set from the `tool` argument: inside tool inputs and results a
# `name` key is the diner's (or the location's) name.
ALLOWED_KEYS = ("name", "party_size", "start", "end", "outcome", "code_present", "error")
# Source keys renamed onto an allowed key (the first one present wins).
_RENAMED = {"start": ("start", "start_iso", "earliest"), "end": ("end", "end_iso", "latest")}
_CODE_KEYS = ("code_present", "confirmation_code", "code")


def text(s: str | None) -> str | None:
    """`s` with emails, phone-like digit runs and confirmation codes masked
    as [email], [phone] and [code]."""
    if not s:
        return s
    s = _EMAIL.sub("[email]", s)
    s = _PHONE.sub("[phone]", s)
    return _CODE.sub("[code]", s)


def _scalar(v: Any) -> Any:
    if isinstance(v, str):
        return text(v)
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    return None  # nested values never pass


def _has_code(d: dict[str, Any]) -> bool:
    for key in _CODE_KEYS:
        if d.get(key):
            return True
    # A booking summary nested under `current` (modify_booking's result).
    return any(isinstance(v, dict) and _has_code(v) for v in d.values())


def payload(d: dict[str, Any], *, tool: str | None = None) -> dict[str, Any]:
    """The allowlisted view of one tool input or result: `name` (= `tool`),
    party_size, start, end, outcome, error (strings redacted), and
    `code_present`, whether a confirmation code was given or returned.
    Every other key, and every nested value, is dropped."""
    d = d if isinstance(d, dict) else {}
    out: dict[str, Any] = {}
    if tool is not None:
        out["name"] = tool
    for key in ("party_size", "start", "end", "outcome", "error"):
        for src in _RENAMED.get(key, (key,)):
            if src in d and _scalar(d[src]) is not None:
                out[key] = _scalar(d[src])
                break
    out["code_present"] = _has_code(d)
    return out
