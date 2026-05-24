"""Pagination helpers — keeps caller-supplied ?limit values inside a
safe band before they reach the DB.

The current routers either pre-bound `?limit` via FastAPI's `Query(..., le=N)`
(see backend/routers/failure_logs.py) or inline-clamp with `min(limit, N)`.
`clamp_limit()` is the third option, useful when the limit comes from a
non-Query source (a body field, a settings value, a multi-stage default)
or when the caller wants a single shared default applied across endpoints.
"""
from __future__ import annotations


def clamp_limit(
    limit: int | None,
    default: int = 100,
    max: int = 500,
) -> int:
    """Return a safe pagination limit.

    - ``limit is None`` -> use ``default``.
    - ``limit <= 0`` -> use ``default`` (callers usually mean "give me a
      reasonable page", not "give me nothing").
    - ``limit > max`` -> clamp to ``max``.

    Never raises — DB queries should not 500 because a caller typed
    `?limit=99999999`.
    """
    if limit is None or limit <= 0:
        return default
    if limit > max:
        return max
    return limit
