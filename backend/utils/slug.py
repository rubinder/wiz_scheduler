"""Turn free text into a URL- and filename-safe slug.

A pure function with no I/O. It knows nothing about the database, so two
inputs can produce the same slug — disambiguation is the caller's job. It
does not transliterate: accented and non-Latin letters are dropped, not
folded to ASCII, so ``"café"`` becomes ``"caf"``.

Nothing calls this yet. ``Company.slug`` is still minted by
``secrets.token_hex(3)`` in the auth and ownership-group routers.
"""
from __future__ import annotations

import re

# Anything that is not a lowercase ASCII letter or digit. The input is
# lowercased first, so the class does not need A-Z.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Returned when nothing survives the transform. Not subject to max_len.
_FALLBACK = "item"


def slugify(text: str, max_len: int = 30) -> str:
    """Return *text* as lowercase ``[a-z0-9]`` and single dashes, with no
    dash at either end, at most *max_len* characters long, and never empty.

    Every run of characters outside ``[a-z0-9]`` — whitespace, punctuation,
    underscores, non-ASCII letters — collapses to one ``-``. Edge dashes are
    stripped before truncating, so leading junk never eats the budget.
    Truncation is a plain character cut; it may split a word.

    If nothing survives (empty, whitespace-only, punctuation-only,
    all-non-ASCII input, or a *max_len* of zero or less) the result is
    ``"item"``. That fallback is returned verbatim even when *max_len* is
    shorter than four, because a fallback that could shrink to ``""`` would
    defeat its purpose. Consequently ``slugify(slugify(x, n), n) ==
    slugify(x, n)`` holds for every ``n >= 4`` but not below it:
    ``slugify("", 2)`` is ``"item"`` while ``slugify("item", 2)`` is ``"it"``.

    Never raises for any ``str`` *text* and any ``int`` *max_len*. Non-``str``
    input is not handled; ``None`` raises ``AttributeError`` at ``.lower()``.
    """
    slug = _NON_ALNUM.sub("-", text.lower()).strip("-")
    # A negative slice bound would cut from the end ("hello"[:-1] == "hell"),
    # so clamp: a non-positive budget means nothing fits and the fallback fires.
    # Only a trailing dash can be exposed by the cut — the leading edge was
    # already cleaned above — so rstrip, not strip.
    slug = slug[: max(max_len, 0)].rstrip("-")
    return slug or _FALLBACK
