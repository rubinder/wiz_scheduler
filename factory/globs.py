"""Path globs for protected paths and gate triggers.

`**` spans directories (and `**/` may match none), `*` and `?` stay inside
one path segment, `{a,b}` is alternation. fnmatch is not used because its
`*` crosses `/`, which would make `billing*.py` match a whole subtree.

Matching fails closed, because a miss lets a PR auto-merge: it is
case-insensitive (macOS and Windows checkouts fold case), and a pattern that
does not compile, such as an unmatched `{`, matches every path.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Optional


@lru_cache(maxsize=512)
def _compile(pattern: str) -> Optional[re.Pattern[str]]:
    """The pattern as a regex, or None when it is malformed."""
    try:
        return _translate(pattern.lower())
    except (ValueError, re.error):
        return None


def _translate(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "{":
            end = pattern.index("}", i)
            alts = pattern[i + 1:end].split(",")
            out.append("(?:" + "|".join(re.escape(a) for a in alts) + ")")
            i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


def glob_match(path: str, pattern: str) -> bool:
    compiled = _compile(pattern)
    return compiled is None or compiled.match(path.lower()) is not None


def any_match(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)
