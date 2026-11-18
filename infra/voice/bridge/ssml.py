"""SSML for the voice bridge (#259 step 6): the backend's plain reply text
as Polly speaks it.

The text is escaped first (`&`, `<`, `>`, `"`, `'`) and the say-as rules
run on the escaped text, so nothing in model output can become a tag: the
only tags in the result are the ones these rules insert. The rules:

- times "7:00 PM" -> "7 pm", "7:30 p.m." -> "7:30 pm";
- ISO dates "2026-11-21" -> "Saturday, November 21st";
- E.164 numbers as digit groups (a +1 number as 3-3-4 without the country
  code; others "plus", the country code, then groups of up to four);
- a confirmation code (6 characters of reservations/codes.ALPHABET) letter
  by letter, only right after "code" or "confirmation code", so ordinary
  uppercase words are never spelled.

Stdlib only: this ships in the Lambda zip.
"""

import re
from datetime import date

# reservations/codes.ALPHABET: no 0/O, 1/I.
_CODE_CHARS = "A-HJ-NP-Z2-9"

_TIME = re.compile(
    r"\b(1[0-2]|0?[1-9]):([0-5][0-9])\s*(?:([AaPp])[Mm]\b|([AaPp])\.\s?[Mm]\.)")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CODE = re.compile(
    rf"(\b(?:[Cc]onfirmation\s+)?[Cc]ode(?:\s+is|:)?\s+)([{_CODE_CHARS}]{{6}})\b")
_PHONE = re.compile(r"\+([1-9][0-9]{6,14})\b")
_MARKDOWN = re.compile(r"\*\*|__")

_BREAK = '<break strength="weak"/>'


def escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _time(m: re.Match[str]) -> str:
    hour, minute = int(m.group(1)), m.group(2)
    meridiem = f"{(m.group(3) or m.group(4)).lower()}m"
    return f"{hour} {meridiem}" if minute == "00" else f"{hour}:{minute} {meridiem}"


def _date(m: re.Match[str]) -> str:
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return m.group(0)
    return f"{d:%A, %B} {_ordinal(d.day)}"


def _digits(group: str) -> str:
    return f'<say-as interpret-as="digits">{group}</say-as>'


def _chunks(digits: str) -> list[str]:
    out = []
    while len(digits) > 4:
        out.append(digits[:3])
        digits = digits[3:]
    return out + [digits]


def _phone(m: re.Match[str]) -> str:
    number = m.group(1)
    if number.startswith("1") and len(number) == 11:
        groups = [number[1:4], number[4:7], number[7:]]
        return _BREAK.join(_digits(g) for g in groups)
    # Country codes are 1-3 digits; without a table, read the first two
    # (most common length) and group the rest.
    cc, rest = number[:2], number[2:]
    return "plus " + _digits(cc) + _BREAK + _BREAK.join(_digits(g) for g in _chunks(rest))


def _code(m: re.Match[str]) -> str:
    return f'{m.group(1)}<say-as interpret-as="characters">{m.group(2)}</say-as>'


def render(text: str) -> str:
    """Plain reply text -> one `<speak>` document."""
    out = escape(_MARKDOWN.sub("", text or ""))
    out = _DATE.sub(_date, out)
    out = _TIME.sub(_time, out)
    out = _CODE.sub(_code, out)
    out = _PHONE.sub(_phone, out)
    return f"<speak>{out}</speak>"
