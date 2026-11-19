"""What may reach the analytics lake (#259 step 10).

The lake is anonymous. It holds no diner names, phone numbers, emails or
confirmation codes, no free text the diner or the model wrote (turns land
as character counts only), and no pseudonymous diner key either:
extract drops every name, email, notes, phone and caller-key column.

What is left that can carry a stray identifier are short machine strings:
error codes, tool payload values and action labels. This module is the
backstop for those.

- `text` masks phone-like digit runs, emails and confirmation codes (the
  day's known codes exactly, then a generic pattern).
- `payload` keeps an allowlist of keys and nothing else.
"""

import re
from collections.abc import Iterable
from typing import Any

# Same alphabet as backend/reservations/codes.py (no 0, 1, I, O). Kept as a
# literal rather than imported: analytics reads the reservations tables but
# depends on none of their code.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
PHONE_MIN_DIGITS = 7

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# A run of digits and separators (spaces, dots, dashes, parentheses, +). A
# run with 7 or more digits in it is a phone number, however it is spaced.
# ISO dates (2026-11-20, as in a start_iso or an eval failure's
# "party 2 2026-11-20 19:00") are cut out first and the pieces around them
# judged on their own.
_DIGIT_RUN = re.compile(r"[\d(+][\d\s.\-()+]*\d\)?")
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)")
_CC = f"[{CODE_ALPHABET}{CODE_ALPHABET.lower()}]"
_B, _E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
# Any case with at least one digit; or fully uppercase; or any case right
# after "code" / "confirmation" ("code is abcdef", "confirmation: QWERTY").
_CODE_WITH_DIGIT = re.compile(rf"{_B}(?={_CC}*\d){_CC}{{{CODE_LENGTH}}}{_E}")
_CODE_UPPER = re.compile(rf"{_B}[{CODE_ALPHABET}]{{{CODE_LENGTH}}}{_E}")
_CODE_AFTER_WORD = re.compile(
    rf"(?i)({_B}(?:confirmation|code)(?:\s+(?:code|number|is|was|it's))*[\s:#=]*)"
    rf"{_CC}{{{CODE_LENGTH}}}{_E}")

# Tool payload keys allowed into the lake. `name` is the tool's name and is
# only ever set from the `tool` argument: inside tool inputs and results a
# `name` key is the diner's (or the location's) name.
ALLOWED_KEYS = ("name", "party_size", "start", "end", "outcome", "code_present", "error")
# Source keys renamed onto an allowed key (the first one present wins).
_RENAMED = {"start": ("start", "start_iso", "earliest"), "end": ("end", "end_iso", "latest")}
_CODE_KEYS = ("code_present", "confirmation_code", "code")


def _mask_piece(piece: str) -> str:
    if sum(ch.isdigit() for ch in piece) < PHONE_MIN_DIGITS:
        return piece
    lead = piece[:len(piece) - len(piece.lstrip())]
    trail = piece[len(piece.rstrip()):]
    return f"{lead}[phone]{trail}"


def _phone(m: re.Match[str]) -> str:
    run = m.group(0)
    if sum(ch.isdigit() for ch in run) < PHONE_MIN_DIGITS:
        return run
    out, last = [], 0
    for d in _ISO_DATE.finditer(run):
        out += [_mask_piece(run[last:d.start()]), d.group(0)]
        last = d.end()
    out.append(_mask_piece(run[last:]))
    return "".join(out)


def _known(codes: Iterable[str]) -> re.Pattern[str] | None:
    alts = sorted({re.escape(c) for c in codes if c}, key=len, reverse=True)
    if not alts:
        return None
    return re.compile(rf"(?i){_B}(?:{'|'.join(alts)}){_E}")


def text(s: str | None, known_codes: Iterable[str] = ()) -> str | None:
    """`s` with emails, phone-like digit runs and confirmation codes masked
    as [email], [phone] and [code]. `known_codes` (the day's real codes)
    are replaced exactly, in any case, before the generic code patterns."""
    if not s:
        return s
    s = _EMAIL.sub("[email]", s)
    known = _known(known_codes)
    if known is not None:
        s = known.sub("[code]", s)
    s = _DIGIT_RUN.sub(_phone, s)
    s = _CODE_AFTER_WORD.sub(lambda m: m.group(1) + "[code]", s)
    s = _CODE_WITH_DIGIT.sub("[code]", s)
    return _CODE_UPPER.sub("[code]", s)


def _scalar(v: Any, known_codes: Iterable[str]) -> Any:
    if isinstance(v, str):
        return text(v, known_codes)
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    return None  # nested values never pass


def _has_code(d: dict[str, Any]) -> bool:
    for key in _CODE_KEYS:
        if d.get(key):
            return True
    # A booking summary nested under `current` (modify_booking's result).
    return any(isinstance(v, dict) and _has_code(v) for v in d.values())


def payload(d: dict[str, Any], *, tool: str | None = None,
            known_codes: Iterable[str] = ()) -> dict[str, Any]:
    """The allowlisted view of one tool input or result: `name` (= `tool`),
    party_size, start, end, outcome, error (strings redacted), and
    `code_present`, whether a confirmation code was given or returned.
    Every other key, and every nested value, is dropped."""
    d = d if isinstance(d, dict) else {}
    known_codes = tuple(known_codes)
    out: dict[str, Any] = {}
    if tool is not None:
        out["name"] = tool
    for key in ("party_size", "start", "end", "outcome", "error"):
        for src in _RENAMED.get(key, (key,)):
            if src in d and _scalar(d[src], known_codes) is not None:
                out[key] = _scalar(d[src], known_codes)
                break
    out["code_present"] = _has_code(d)
    return out
