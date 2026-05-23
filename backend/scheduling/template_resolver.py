"""Per-day ShiftTemplate resolution.

The scheduler needs to know, for each calendar date in the target week, which
ShiftTemplate to apply. Precedence:

1. ShiftTemplate where (location_id, specific_date) matches the date.
2. The recurring template the manager selected via selected_template_ids
   (filtered to the location's templates with specific_date IS NULL).
3. If no selection: the only recurring template for the location.

If a date has no specific_date row AND the location has no recurring template,
the resolver raises LocationMissingTemplate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import ShiftTemplate


class LocationMissingTemplate(Exception):
    def __init__(self, location_id: str, missing_date: date):
        self.location_id = location_id
        self.missing_date = missing_date
        super().__init__(
            f"Location {location_id} has no template for {missing_date.isoformat()}"
        )


async def resolve_templates_for_week(
    db: AsyncSession,
    *,
    location_id: str,
    week_dates: list[date],
    selected_template_ids: list[str] | None,
) -> dict[date, ShiftTemplate]:
    """Return {date: ShiftTemplate} for every date in week_dates."""
    specific_rows = (await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.location_id == location_id,
            ShiftTemplate.specific_date.in_(week_dates),
        )
    )).scalars().all()
    specific_by_date: dict[date, ShiftTemplate] = {
        t.specific_date: t for t in specific_rows if t.specific_date is not None
    }

    recurring_q = select(ShiftTemplate).where(
        ShiftTemplate.location_id == location_id,
        ShiftTemplate.specific_date.is_(None),
    )
    if selected_template_ids:
        recurring_q = recurring_q.where(
            ShiftTemplate.id.in_(selected_template_ids)
        )
    recurring_q = recurring_q.order_by(ShiftTemplate.id.asc())
    recurring_rows = (await db.execute(recurring_q)).scalars().all()
    recurring = recurring_rows[0] if recurring_rows else None

    result: dict[date, ShiftTemplate] = {}
    for d in week_dates:
        chosen = specific_by_date.get(d) or recurring
        if chosen is None:
            raise LocationMissingTemplate(location_id, d)
        result[d] = chosen
    return result
