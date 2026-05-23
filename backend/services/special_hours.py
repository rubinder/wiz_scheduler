"""Service helpers for the Special Hours Days feature."""
from __future__ import annotations

import copy
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


def _format_time(t: time) -> str:
    """Render as HH:MM:SS to match the format already stored in weekly_schedule."""
    return t.strftime("%H:%M:%S")


async def clone_template_for_date(
    db: AsyncSession,
    *,
    source: ShiftTemplate,
    target_date: date,
    open_time: time,
    close_time: time,
    label: str | None,
) -> ShiftTemplate:
    """Clone `source` into a single-day variant for `target_date`.

    - The clone's `weekly_schedule` is a 1-element list whose `day_of_week`
      is `target_date.weekday()`.
    - Roles are copied from the source's matching-dow entry, or (when the
      source has no matching dow) from the first non-empty entry.
    - Every role's `start_time` / `end_time` is replaced with the special
      open / close.
    - `name = f"{source.name} — {label or target_date.isoformat()}"`.
    - `specific_date` is set on the clone.
    - The clone is added to the session and flushed (so callers see an `id`).
    """
    target_dow = target_date.weekday()
    src_days = source.weekly_schedule or []

    matching = next(
        (copy.deepcopy(d) for d in src_days if d.get("day_of_week") == target_dow),
        None,
    )
    if matching is None or not matching.get("roles"):
        # Fall back to the first non-empty day; rewrite its dow to match the target.
        fallback = next(
            (copy.deepcopy(d) for d in src_days if d.get("roles")),
            {"day_of_week": target_dow, "roles": []},
        )
        fallback["day_of_week"] = target_dow
        day_entry = fallback
    else:
        day_entry = matching

    open_str = _format_time(open_time)
    close_str = _format_time(close_time)
    for role in day_entry.get("roles", []):
        role["start_time"] = open_str
        role["end_time"] = close_str

    clone = ShiftTemplate(
        company_id=source.company_id,
        location_id=source.location_id,
        name=f"{source.name} — {label or target_date.isoformat()}",
        weekly_schedule=[day_entry],
        specific_date=target_date,
    )
    db.add(clone)
    await db.flush()
    return clone
