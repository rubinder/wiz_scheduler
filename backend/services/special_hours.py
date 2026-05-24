"""Service helpers for the Special Hours Days feature."""
from __future__ import annotations

import copy
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


# Day-of-week index → day-name string used by the legacy flat-list shape.
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_time(t: time) -> str:
    """Render as HH:MM:SS to match the format already stored in weekly_schedule."""
    return t.strftime("%H:%M:%S")


def _extract_roles_for_dow(
    src_days: list[dict], target_dow: int
) -> list[dict]:
    """Return the list of role-requirement dicts that should drive the clone's
    one-day entry, regardless of which storage shape `src_days` is in.

    Two shapes exist in production:

    (a) **Dow-grouped** (`clone_template_for_date` and the design spec):
        ``[{"day_of_week": int, "roles": [{role_id, role_name, ...}, ...]}, ...]``

    (b) **Flat list** (the legacy/seed shape — every existing customer):
        ``[{"day": "Monday", "role_id": ..., "role_name": ..., "headcount": N,
            "start_time": "HH:MM", "end_time": "HH:MM"}, ...]``

    For (b), we group by day name and return entries for the target dow
    converted into the dow-grouped role shape (so the clone's
    ``weekly_schedule[0].roles`` is uniform with shape (a)).

    Fallback: if no entry matches the target dow, return the first non-empty
    day's roles. Manager can edit the clone afterwards.
    """
    matching_dow_grouped = next(
        (copy.deepcopy(d) for d in src_days
         if isinstance(d, dict) and d.get("day_of_week") == target_dow),
        None,
    )
    if matching_dow_grouped is not None and matching_dow_grouped.get("roles"):
        return matching_dow_grouped["roles"]

    target_day_name = _DAY_NAMES[target_dow]
    flat_for_target = [
        copy.deepcopy(d) for d in src_days
        if isinstance(d, dict) and d.get("day") == target_day_name
    ]
    if flat_for_target:
        return [
            {
                "role_id": e.get("role_id"),
                "role_name": e.get("role_name"),
                "required_headcount": e.get("required_headcount", e.get("headcount", 1)),
                "start_time": e.get("start_time"),
                "end_time": e.get("end_time"),
            }
            for e in flat_for_target
        ]

    fallback_dow_grouped = next(
        (copy.deepcopy(d) for d in src_days
         if isinstance(d, dict) and d.get("roles")),
        None,
    )
    if fallback_dow_grouped is not None:
        return fallback_dow_grouped["roles"]

    # Fallback to the flat shape: take whichever day has the most entries.
    flat_by_day: dict[str, list[dict]] = {}
    for d in src_days:
        if isinstance(d, dict) and d.get("day"):
            flat_by_day.setdefault(d["day"], []).append(copy.deepcopy(d))
    if flat_by_day:
        best_day = max(flat_by_day, key=lambda k: len(flat_by_day[k]))
        return [
            {
                "role_id": e.get("role_id"),
                "role_name": e.get("role_name"),
                "required_headcount": e.get("required_headcount", e.get("headcount", 1)),
                "start_time": e.get("start_time"),
                "end_time": e.get("end_time"),
            }
            for e in flat_by_day[best_day]
        ]

    return []


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
    - Roles are copied from the source — handling both the dow-grouped shape
      and the legacy flat-list shape (see `_extract_roles_for_dow`).
    - Every role's `start_time` / `end_time` is replaced with the special
      open / close.
    - `name = f"{source.name} — {label or target_date.isoformat()}"`.
    - `specific_date` is set on the clone.
    - The clone is added to the session and flushed (so callers see an `id`).
    """
    target_dow = target_date.weekday()
    src_days = source.weekly_schedule or []
    roles = _extract_roles_for_dow(src_days, target_dow)

    open_str = _format_time(open_time)
    close_str = _format_time(close_time)
    for role in roles:
        role["start_time"] = open_str
        role["end_time"] = close_str

    clone = ShiftTemplate(
        company_id=source.company_id,
        location_id=source.location_id,
        name=f"{source.name} — {label or target_date.isoformat()}",
        weekly_schedule=[{"day_of_week": target_dow, "roles": roles}],
        specific_date=target_date,
    )
    db.add(clone)
    await db.flush()
    return clone
