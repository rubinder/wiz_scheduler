"""Service helpers for the Special Hours Days feature."""
from __future__ import annotations

import copy
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


# Day-of-week index → day-name string used by the legacy flat-list shape.
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_hhmm(t: time) -> str:
    """Render as HH:MM to match the format used by the seed / production
    weekly_schedule flat entries (the ShiftTemplates UI renderer expects this
    exact shape)."""
    return t.strftime("%H:%M")


def _flat_entries_for_dow(
    src_days: list[dict], target_dow: int
) -> list[dict]:
    """Return a list of flat per-role entries (one per role) that should
    populate the clone's weekly_schedule for ``target_dow``.

    The output shape matches the legacy / production shape that every other
    ShiftTemplate row uses and that the ``/manager/shift-templates`` UI knows
    how to render:

    ``[{"day": "Thursday", "role_id": "r1", "role_name": "Server",
        "headcount": 3, "start_time": "10:00", "end_time": "13:00"}, ...]``

    The input can be in EITHER shape:

    (a) Legacy / production flat list:
        ``[{"day": "Monday", "role_id": ..., "role_name": ..., "headcount": N,
            "start_time": "HH:MM", "end_time": "HH:MM"}, ...]``
        → matching entries by ``day == day_name(target_dow)`` are taken as-is.

    (b) Dow-grouped (the shape introduced by an earlier iteration of this
        helper, still valid for any externally-built templates):
        ``[{"day_of_week": int, "roles": [{role_id, role_name, ...}, ...]}, ...]``
        → roles for ``day_of_week == target_dow`` are unrolled into flat entries.

    Fallback if the target dow has no entries: pick the busiest day (by entry
    count for flat shape, or any roles-bearing entry for dow-grouped shape).
    The manager can edit the clone afterwards.
    """
    target_day_name = _DAY_NAMES[target_dow]

    # Shape (a) — direct match by day name.
    flat_match = [
        copy.deepcopy(d) for d in src_days
        if isinstance(d, dict) and d.get("day") == target_day_name
    ]
    if flat_match:
        return [_flat_from_legacy(e, target_day_name) for e in flat_match]

    # Shape (b) — dow-grouped match.
    dow_match = next(
        (copy.deepcopy(d) for d in src_days
         if isinstance(d, dict) and d.get("day_of_week") == target_dow
         and d.get("roles")),
        None,
    )
    if dow_match is not None:
        return [_flat_from_dow_role(r, target_day_name) for r in dow_match["roles"]]

    # Fallback (a) — busiest day in the legacy shape.
    flat_by_day: dict[str, list[dict]] = {}
    for d in src_days:
        if isinstance(d, dict) and d.get("day"):
            flat_by_day.setdefault(d["day"], []).append(copy.deepcopy(d))
    if flat_by_day:
        best_day = max(flat_by_day, key=lambda k: len(flat_by_day[k]))
        return [_flat_from_legacy(e, target_day_name) for e in flat_by_day[best_day]]

    # Fallback (b) — any roles-bearing dow-grouped entry.
    fallback_dow = next(
        (copy.deepcopy(d) for d in src_days
         if isinstance(d, dict) and d.get("roles")),
        None,
    )
    if fallback_dow is not None:
        return [_flat_from_dow_role(r, target_day_name) for r in fallback_dow["roles"]]

    return []


def _flat_from_legacy(e: dict, day_name: str) -> dict:
    """Copy a legacy flat-shape entry, normalising the ``day`` to the target."""
    return {
        "day": day_name,
        "role_id": e.get("role_id"),
        "role_name": e.get("role_name"),
        "headcount": e.get("headcount", e.get("required_headcount", 1)),
        "start_time": e.get("start_time"),
        "end_time": e.get("end_time"),
    }


def _flat_from_dow_role(r: dict, day_name: str) -> dict:
    """Convert a dow-grouped role dict into the legacy flat-shape entry."""
    return {
        "day": day_name,
        "role_id": r.get("role_id"),
        "role_name": r.get("role_name"),
        "headcount": r.get("headcount", r.get("required_headcount", 1)),
        "start_time": r.get("start_time"),
        "end_time": r.get("end_time"),
    }


async def clone_template_for_date(
    db: AsyncSession,
    *,
    source: ShiftTemplate,
    target_date: date,
    open_time: time,
    close_time: time,
    label: str | None,
) -> ShiftTemplate:
    """Clone ``source`` into a single-day variant for ``target_date``.

    - The clone's ``weekly_schedule`` uses the **legacy flat-list shape** so
      the ``/manager/shift-templates`` UI renderer (which expects that shape)
      can display the clone alongside the recurring templates.
    - Roles are copied from the source via ``_flat_entries_for_dow``, which
      handles both the legacy flat shape and the dow-grouped shape on input.
    - Every entry's ``start_time`` / ``end_time`` is replaced with the
      special open / close in ``HH:MM`` format.
    - ``name = f"{source.name} — {label or target_date.isoformat()}"``.
    - ``specific_date`` is set on the clone — this is the load-bearing
      column the scheduler's per-day resolver uses to pick the override.
    - The clone is added to the session and flushed (so callers see an ``id``).
    """
    target_dow = target_date.weekday()
    src_days = source.weekly_schedule or []
    entries = _flat_entries_for_dow(src_days, target_dow)

    open_str = _format_hhmm(open_time)
    close_str = _format_hhmm(close_time)
    for e in entries:
        e["start_time"] = open_str
        e["end_time"] = close_str

    clone = ShiftTemplate(
        company_id=source.company_id,
        location_id=source.location_id,
        name=f"{source.name} — {label or target_date.isoformat()}",
        weekly_schedule=entries,
        specific_date=target_date,
    )
    db.add(clone)
    await db.flush()
    return clone
